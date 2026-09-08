# OtterCode — capa HTTP REST: todos los endpoints /api (vía APIRouter)
from __future__ import annotations
from backend.config import *  # noqa: F401,F403
from backend.vault import _memory_recall  # noqa: E402
from backend.memory import get_memory, add_memory, delete_memory_item # noqa: E402
from backend.runtime import ACTIVE_RUN, ACTIVITY, ACTIVITY_LOCK, RUN_LOCK, _activity_finish  # noqa: E402
from backend.runstate import OtterRun  # noqa: E402
from backend.prompts import extract_json_object  # noqa: E402
from backend.profiles import _delete_profile, _get_profile, _load_active_profile, _load_profiles, _save_active_profile, _save_profile  # noqa: E402
from backend.ollama import _LlmSession, fetch_models, flush_all_vram, flush_vram, invalidate_models_cache, stream_llm  # noqa: E402
from backend.history import HISTORY, _append_session_event  # noqa: E402
from backend.engine import run_task_stream  # noqa: E402
from backend.db import get_session_detail, search_history  # noqa: E402
from backend.config import APP_VERSION, COMPACT_THRESHOLD_CHARS, DB_PATH, DEFAULT_MODEL, LLM_BACKEND, MAX_REVIEW_ROUNDS, NUM_CTX_DEFAULT, NUM_PREDICT_DEFAULT, OLLAMA_BASE_URL, SOUL_PATH, USER_PATH, VRAM_TOTAL_BYTES, WORKSPACE_ROOT, _ollama_httpx, _save_identity  # noqa: E402
from backend.agents import AGENT_FACTORY_SYSTEM, AGENT_ORDER, CORE_AGENTS, DYNAMIC_AGENTS, _load_skills_cfg, build_profile_prompt, get_agent, normalize_agent_profile, set_skill_enabled  # noqa: E402
from backend.engine import *  # noqa: F401,F403
from backend.agents import *  # noqa: F401,F403
from backend.agents import _load_skills_cfg  # noqa
from backend.ollama import *  # noqa: F401,F403
from backend.runtime import *  # noqa: F401,F403
from backend.runtime import _force_stop_run, _activity_set, _activity_finish  # noqa
from backend.db import *  # noqa: F401,F403
from backend.history import HISTORY  # noqa
from backend.vault import *  # noqa: F401,F403
from fastapi import APIRouter  # noqa
router = APIRouter(tags=["api"])


import backend.config as _otter_cfg  # noqa: E402
import backend.profiles as _otter_profiles  # noqa: E402
import time  # noqa: E402


def _read_proc_meminfo() -> Dict[str, int]:
    """RAM del sistema desde /proc/meminfo (Linux, sin dependencias)."""
    vals: Dict[str, int] = {}
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                key, rest = line.split(":", 1)
                vals[key.strip()] = int(rest.strip().split()[0])  # kB
    except (OSError, ValueError):
        return {}
    return vals


def _read_proc_cpu() -> tuple:
    """Contadores acumulados de CPU desde /proc/stat (línea `cpu `)."""
    try:
        with open("/proc/stat", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("cpu "):
                    parts = [int(x) for x in line.split()[1:]]
                    idle = parts[3] + (parts[4] if len(parts) > 4 else 0)
                    return sum(parts), idle  # total, idle
    except (OSError, ValueError):
        pass
    return (0, 0)


def _cpu_percent_cached() -> float:
    """% CPU medio entre dos muestras de /proc/stat (caché de 600 ms)."""
    now = time.monotonic()
    prev = getattr(_cpu_percent_cached, "_prev", None)
    if prev:
        ts, t0, i0 = prev
        if now - ts >= 0.6:
            t1, i1 = _read_proc_cpu()
            if t1 and t1 > t0:
                dt = t1 - t0
                di = i1 - i0
                _cpu_percent_cached._prev = (now, t1, i1)
                if dt > 0:
                    return max(0.0, min(100.0, (dt - di) * 100.0 / dt))
                return 0.0
            _cpu_percent_cached._prev = None
    t0, i0 = _read_proc_cpu()
    if t0:
        _cpu_percent_cached._prev = (now, t0, i0)
    return 0.0


@router.get(Route.SYSTEM)
def api_system() -> Dict[str, Any]:
    """v6.3 · Métricas de hardware para el panel derecho estilo V2:
    RAM/CPU desde /proc (cero dependencias, Linux) y VRAM desde Ollama /api/ps."""
    mem = _read_proc_meminfo()
    total_kb = mem.get("MemTotal", 0)
    avail_kb = mem.get("MemAvailable", mem.get("MemFree", 0))
    ps_models: List[Dict[str, Any]] = []
    try:
        resp = _ollama_httpx.get("/api/ps")
        resp.raise_for_status()
        ps_models = [
            {
                "name": m.get("name"),
                "size_vram": m.get("size_vram"),
                "size_ram": m.get("size_ram"),
            }
            for m in resp.json().get("models", [])
        ]
    except (httpx.HTTPError, ValueError):
        pass
    cpu_count = 0
    try:
        with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
            cpu_count = sum(1 for line in f if line.startswith("processor"))
    except OSError:
        pass
    used_vram = sum(int(m.get("size_vram") or 0) for m in ps_models)
    return {
        "ok": True,
        "os": "linux",
        "cpu": {"count": cpu_count, "percent": round(_cpu_percent_cached(), 1)},
        "ram": {
            "total": total_kb * 1024,
            "used": (total_kb - avail_kb) * 1024,
            "free": avail_kb * 1024,
        },
        "vram": {"total": VRAM_TOTAL_BYTES, "used": used_vram, "models": ps_models},
    }
@router.get(Route.HEALTHZ)
def healthz() -> Dict[str, str]:
    return {"ok": "true", "version": APP_VERSION}


@router.get(Route.STATUS)
def api_status() -> Dict[str, Any]:
    models = fetch_models()
    return {
        "ok": True,
        "version": APP_VERSION,
        "ollama": {
            "ok": models is not None,
            "url": OLLAMA_BASE_URL,
            "models": models or [],
            "models_count": len(models) if models else 0,
        },
        "default_model": DEFAULT_MODEL,
        "running": bool(ACTIVE_RUN),
        "max_review_rounds": MAX_REVIEW_ROUNDS,
        "dynamic_agents": len(DYNAMIC_AGENTS),
        "api": LLM_BACKEND,
        # v3.3: defaults del motor de rendimiento (UI muestra el selector)
        "num_ctx_default": NUM_CTX_DEFAULT,
        "num_predict_default": NUM_PREDICT_DEFAULT,
        "compact_chars": COMPACT_THRESHOLD_CHARS,
    }


@router.get(Route.PULSE)
def api_pulse() -> Dict[str, Any]:
    """v3.3 · Latido único para el polling del frontend: estado + VRAM +
    actividad en UNA llamada (antes eran 2 requests cada pocos segundos por
    pestaña abierta; con N pestañas el ruido se multiplicaba)."""
    models = fetch_models()  # v6.0 · usa la caché (evita golpear /api/tags cada poll)
    ps_models: List[Dict[str, Any]] = []
    try:
        resp = _ollama_httpx.get("/api/ps")
        resp.raise_for_status()
        ps_models = [
            {
                "name": m.get("name"),
                "size_vram": m.get("size_vram"),
                "size_ram": m.get("size_ram"),
                "expires_at": m.get("expires_at"),
            }
            for m in resp.json().get("models", [])
        ]
    except (httpx.HTTPError, ValueError):
        pass
    with ACTIVITY_LOCK:
        activity = dict(ACTIVITY)
    ok = models is not None
    # v6.0 · solo inyectar DEFAULT_MODEL cuando Ollama respondió: si está
    # caído devolvemos [] para que la UI no crea que ya hay un modelo.
    merged = sorted(set(models)) if ok else sorted([])
    return {
        "ok": ok,
        "version": APP_VERSION,
        "default_model": DEFAULT_MODEL,
        "models": merged,
        "ps": ps_models,
        "running": bool(ACTIVE_RUN),
        "activity": activity,
        "num_ctx_default": NUM_CTX_DEFAULT,
        "vram_total": VRAM_TOTAL_BYTES,
    }


@router.get(Route.MODELS)
def api_models() -> Dict[str, Any]:
    models = fetch_models()
    merged = sorted(set((models or []) + [DEFAULT_MODEL]))
    details: List[Dict[str, Any]] = []
    try:
        resp = _ollama_httpx.get("/api/tags")
        resp.raise_for_status()
        for m in resp.json().get("models") or []:
            if not isinstance(m, dict):
                continue
            name = str(m.get("name") or "")
            size = int(m.get("size") or 0)
            details.append({
                "name": name,
                "size": size,
                "size_gb": round(size / (1024 ** 3), 2) if size else 0,
                "vram_est_gb": round(size / (1024 ** 3) * 0.7, 2) if size else 0,
            })
    except Exception:
        details = [{"name": n, "size": 0, "size_gb": 0, "vram_est_gb": 0} for n in merged]
    from backend.profiles import suggest_model_for_role
    return {
        "ollama_ok": models is not None,
        "models": merged,
        "details": details,
        "suggest": {
            "Programador": suggest_model_for_role("Programador", merged),
            "resumen": suggest_model_for_role("resumen", merged),
        },
    }


@router.get(Route.PS)
def api_ps() -> Dict[str, Any]:
    """Modelos CARGADOS en VRAM/RAM ahora mismo (Ollama /api/ps)."""
    try:
        resp = _ollama_httpx.get("/api/ps")
        resp.raise_for_status()
        models = [
            {
                "name": m.get("name"),
                "size_vram": m.get("size_vram"),
                "size_ram": m.get("size_ram"),
                "expires_at": m.get("expires_at"),
            }
            for m in resp.json().get("models", [])
        ]
        return {"ok": True, "models": models}
    except (httpx.HTTPError, ValueError):
        return {"ok": False, "models": []}


# ------------------------------ VRAM MANAGER -------------------------------

class FlushRequest(BaseModel):
    model: Optional[str] = None


@router.post(Route.FLUSH)
def api_flush(req: FlushRequest) -> JSONResponse:
    """Flush manual de VRAM.

    - Con `model` indicado: expulsa SOLO ese modelo (keep_alive:0).
    - Sin modelo (null): expulsa CUALQUIER modelo cargado en ese momento
      (leer /api/ps), de modo que el flush global sirve de verdad.
    """
    if not req.model:
        result = flush_all_vram()
    else:
        result = flush_vram(req.model)
    if not result["ok"]:
        return JSONResponse(status_code=502, content=result)
    return result


# ------------------------------ AJUSTES OLLAMA ------------------------------

@router.get(Route.SETTINGS)
def api_settings_get() -> Dict[str, Any]:
    import backend.settings as _s
    return {"ok": True, "settings": _s.load_runtime_settings()}


class SettingsRequest(BaseModel):
    settings: Dict[str, Any] = Field(default_factory=dict)


@router.post(Route.SETTINGS)
def api_settings_save(req: SettingsRequest) -> Dict[str, Any]:
    import backend.settings as _s
    try:
        saved = _s.save_runtime_settings(req.settings)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"No se pudo guardar: {exc}")
    return {"ok": True, "settings": saved}


@router.post(Route.SETTINGS_RESET)
def api_settings_reset() -> Dict[str, Any]:
    import backend.settings as _s
    return {"ok": True, "settings": _s.reset_runtime_settings()}


@router.post(Route.SETTINGS_APPLY_PROFILE)
def api_settings_apply_profile() -> Dict[str, Any]:
    """Copia el subset de generación (temperature/top_p/num_ctx) de los ajustes
    globales al perfil activo y lo persiste."""
    import backend.settings as _s
    s = _s.load_runtime_settings()
    active = _otter_profiles._ACTIVE_PROFILE or {}
    name = active.get("name") or "default"
    prof = _otter_profiles._get_profile(name) or active
    prof["temperature"] = s.get("temperature", 0.7)
    prof["top_p"] = s.get("top_p", 0.9)
    prof["num_ctx"] = s.get("num_ctx", NUM_CTX_DEFAULT)
    try:
        _otter_profiles._save_profile(prof)
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if active.get("name") == name:
        _otter_profiles._ACTIVE_PROFILE = prof
    return {"ok": True, "profile": name, "settings": s}


# --------------------- GESTIÓN DE MODELOS (paridad Ollama) -------------------

_MODEL_NAME_RE = re.compile(r"^[\w.\-/:%]+$")
_SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}


class ModelNameRequest(BaseModel):
    model: str = Field(min_length=1, max_length=120)


class ModelCreateRequest(BaseModel):
    model: str = Field(min_length=1, max_length=120)
    modelfile: str = Field(min_length=1, max_length=100_000)


class ModelCopyRequest(BaseModel):
    source: str = Field(min_length=1, max_length=120)
    destination: str = Field(min_length=1, max_length=120)


def _validate_model_name(name: str) -> None:
    if not _MODEL_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail=f"Nombre de modelo inválido: {name!r}")


@router.get(Route.VERSION)
def api_version() -> Dict[str, Any]:
    try:
        resp = requests.get(f"{OLLAMA_BASE_URL}/api/version", timeout=(5, 10))
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Ollama no accesible: {exc}")


@router.post(Route.MODELS_PULL)
def api_models_pull(req: ModelNameRequest) -> StreamingResponse:
    """Descarga un modelo emitiendo progreso SSE (hitos cada ≥5%)."""
    _validate_model_name(req.model)

    def gen() -> Iterator[str]:
        last_pct = -25
        try:
            with requests.post(
                f"{OLLAMA_BASE_URL}/api/pull",
                json={"model": req.model},
                stream=True,
                timeout=(10, 3600),
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if "error" in ev:
                        yield sse(SseEvent.task_error, {"message": f"pull: {ev['error']}"})
                        return
                    status = ev.get("status", "")
                    completed, total = ev.get("completed"), ev.get("total")
                    if completed and total:
                        pct = int(completed * 100 / total)
                        if pct - last_pct >= 5 or pct >= 100:
                            last_pct = pct
                            yield sse(SseEvent.pull_progress, {
                                "model": req.model, "status": status, "pct": pct,
                                "completed": completed, "total": total,
                            })
                    else:
                        yield sse(SseEvent.pull_progress, {"model": req.model, "status": status})
                    if status == "success":
                        invalidate_models_cache()
                        break
            yield sse(SseEvent.pull_done, {"ok": True, "model": req.model})
        except requests.RequestException as exc:
            yield sse(SseEvent.task_error, {"message": f"pull no disponible: {exc}"})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.post(Route.MODELS_CREATE)
def api_models_create(req: ModelCreateRequest) -> StreamingResponse:
    """Crea un modelo desde un Modelfile emitiendo progreso SSE."""
    _validate_model_name(req.model)

    def gen() -> Iterator[str]:
        try:
            with requests.post(
                f"{OLLAMA_BASE_URL}/api/create",
                json={"model": req.model, "modelfile": req.modelfile},
                stream=True,
                timeout=(10, 1800),
            ) as resp:
                resp.raise_for_status()
                for line in resp.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    try:
                        ev = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if "error" in ev:
                        yield sse(SseEvent.task_error, {"message": f"create: {ev['error']}"})
                        return
                    yield sse(SseEvent.create_progress, {
                        "model": req.model, "status": ev.get("status", ""),
                    })
                    if ev.get("status") == "success":
                        invalidate_models_cache()
                        break
            yield sse(SseEvent.create_done, {"ok": True, "model": req.model})
        except requests.RequestException as exc:
            yield sse(SseEvent.task_error, {"message": f"create no disponible: {exc}"})

    return StreamingResponse(gen(), media_type="text/event-stream", headers=_SSE_HEADERS)


@router.delete(Route.MODELS_DELETE)
def api_models_delete(req: ModelNameRequest) -> Dict[str, Any]:
    _validate_model_name(req.model)
    try:
        resp = requests.request(
            "DELETE", f"{OLLAMA_BASE_URL}/api/delete",
            json={"model": req.model}, timeout=(10, 120),
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"No se pudo borrar '{req.model}': {exc}")
    invalidate_models_cache()
    return {"ok": True, "model": req.model}


@router.post(Route.MODELS_COPY)
def api_models_copy(req: ModelCopyRequest) -> Dict[str, Any]:
    _validate_model_name(req.source)
    _validate_model_name(req.destination)
    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/copy",
            json={"source": req.source, "destination": req.destination},
            timeout=(10, 300),
        )
        resp.raise_for_status()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Copia fallida: {exc}")
    invalidate_models_cache()
    return {"ok": True, "source": req.source, "destination": req.destination}


@router.post(Route.MODEL_SHOW)
def api_model_show(req: ModelNameRequest) -> Dict[str, Any]:
    """Info detallada de un modelo (params, template, modelfile)."""
    _validate_model_name(req.model)
    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/show",
            json={"model": req.model}, timeout=(10, 60),
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"show falló para '{req.model}': {exc}")

    def _cap(text: Any, limit: int = 1200) -> str:
        text = str(text or "")
        return text if len(text) <= limit else text[:limit] + "\n… (truncado)"

    details = data.get("details") or {}
    return {
        "ok": True,
        "model": req.model,
        "license": _cap(data.get("license"), 800),
        "modelfile": _cap(data.get("modelfile")),
        "parameters": _cap(data.get("parameters")),
        "template": _cap(data.get("template")),
        "system": _cap(data.get("system"), 800),
        "family": details.get("family"),
        "families": details.get("families"),
        "parameter_size": details.get("parameter_size"),
        "quantization_level": details.get("quantization_level"),
    }


# --------------------------- SKILLS (toggles UI) ----------------------------

@router.get(Route.SKILLS)
def api_skills() -> Dict[str, Any]:
    from backend.md_skills import list_md_skills
    disabled = set(_load_skills_cfg().get("disabled", []))
    skills = []
    for name, meta in tools.TOOLS.items():
        skills.append({
            "name": name,
            "cat": meta.get("cat", "FS"),
            "desc": meta.get("desc", ""),
            "writes_fs": bool(meta.get("writes_fs")),
            "enabled": name not in disabled,
            "kind": "tool",
        })
    for md in list_md_skills():
        skills.append({
            "name": md["name"],
            "cat": md["cat"],
            "desc": md["desc"],
            "writes_fs": False,
            "enabled": md["enabled"],
            "kind": "markdown",
        })
    skills.sort(key=lambda sk: (sk["cat"], sk["name"]))
    return {"skills": skills, "disabled": sorted(disabled)}


class SkillToggleRequest(BaseModel):
    tool: str
    enabled: bool = True


@router.post(Route.SKILLS_CONFIG)
def api_skills_config(req: SkillToggleRequest) -> Dict[str, Any]:
    from backend.md_skills import md_skill_names
    canonical = tools.resolve_name(req.tool.strip())
    if canonical not in tools.TOOLS and req.tool.strip() not in md_skill_names():
        raise HTTPException(status_code=400, detail=f"Skill desconocida: {req.tool}")
    name = canonical if canonical in tools.TOOLS else req.tool.strip()
    set_skill_enabled(name, req.enabled)
    return {"ok": True, "tool": name, "enabled": req.enabled}


# --------------------------- AGENTES DINÁMICOS ------------------------------

@router.get(Route.AGENTS)
def api_agents() -> Dict[str, Any]:
    """Registro de agentes: 4 core + dinámicos creados al vuelo."""
    return {
        "agents": [a.to_dict() for a in
                   list(CORE_AGENTS.values()) + list(DYNAMIC_AGENTS.values())],
    }


@router.get(Route.ACTIVITY)
def api_activity() -> Dict[str, Any]:
    """Snapshot de actividad en vivo para la vista Agentes (poll ~3 s)."""
    with ACTIVITY_LOCK:
        snap = dict(ACTIVITY)
    if snap.get("running") and isinstance(snap.get("started_at"), (int, float)):
        snap["elapsed_s"] = round(time.time() - snap["started_at"], 1)
    return snap


# ------------------------------ CEREBRO OBSIDIAN ---------------------------

# --- Fase 4 · Memoria Atómica ---

@router.get("/api/memory")
def api_get_memory():
    return {"content": get_memory()}

@router.post("/api/memory/add")
def api_add_memory(req: Dict[str, str]):
    add_memory(req.get("item", ""))
    return {"ok": True}

@router.post("/api/memory/delete")
def api_delete_memory(req: Dict[str, int]):
    delete_memory_item(req.get("index", 0))
    return {"ok": True}

# FASE 2 · Identidad — endpoints SOUL.md / USER.md
# ---------------------------------------------------------------------------

class IdentityRequest(BaseModel):
    content: str = ""


@router.get(Route.IDENTITY_SOUL)
def api_identity_soul() -> Dict[str, str]:
    return {"content": _otter_cfg._SOUL_CONTENT, "path": str(SOUL_PATH)}


@router.post(Route.IDENTITY_SOUL)
def api_identity_soul_save(req: IdentityRequest) -> Dict[str, Any]:
    _save_identity("soul", req.content)
    return {"ok": True, "content": _otter_cfg._SOUL_CONTENT}


@router.get(Route.IDENTITY_USER)
def api_identity_user() -> Dict[str, str]:
    return {"content": _otter_cfg._USER_CONTENT, "path": str(USER_PATH)}


@router.post(Route.IDENTITY_USER)
def api_identity_user_save(req: IdentityRequest) -> Dict[str, Any]:
    _save_identity("user", req.content)
    return {"ok": True, "content": _otter_cfg._USER_CONTENT}


# ---------------------------------------------------------------------------
# FASE 3 · Perfiles — endpoints CRUD + activo
# ---------------------------------------------------------------------------

@router.get(Route.PROFILES)
def api_profiles_list() -> Dict[str, Any]:
    """Lista todos los perfiles disponibles."""
    profiles = _load_profiles()
    active = _otter_profiles._ACTIVE_PROFILE.get("name", "default")
    return {"profiles": profiles, "active": active}


@router.get(Route.PROFILES_ACTIVE)
def api_profiles_active() -> Dict[str, Any]:
    """Devuelve el perfil activo actual."""
    return {"active": _otter_profiles._ACTIVE_PROFILE}


@router.post(Route.PROFILES_ACTIVE)
def api_profiles_active_set(req: ActiveProfileRequest) -> Dict[str, Any]:
    """Cambia el perfil activo."""
    p = _get_profile(req.name)
    if not p:
        raise HTTPException(status_code=404, detail=f"Perfil '{req.name}' no encontrado.")
    _save_active_profile(req.name)
    return {"ok": True, "active": _otter_profiles._ACTIVE_PROFILE}


@router.get(Route.PROFILES_DETAIL)
def api_profiles_detail(name: str) -> Dict[str, Any]:
    """Devuelve un perfil específico."""
    p = _get_profile(name)
    if not p:
        raise HTTPException(status_code=404, detail=f"Perfil '{name}' no encontrado.")
    return p


@router.post(Route.PROFILES_SAVE)
def api_profiles_save(req: ProfileRequest) -> Dict[str, Any]:
    """Crea o actualiza un perfil."""
    profile = {
        "name": req.name.strip(),
        "display_name": req.display_name or req.name.strip(),
        "model": req.model,
        "temperature": req.temperature,
        "top_p": req.top_p,
        "num_ctx": req.num_ctx,
        "system_override": req.system_override,
    }
    _save_profile(profile)
    # Si es el perfil activo, recargar
    if _otter_profiles._ACTIVE_PROFILE.get("name") == req.name.strip():
        _load_active_profile()
    return {"ok": True, "profile": profile}


@router.delete(Route.PROFILES_DETAIL)
def api_profiles_delete(name: str) -> Dict[str, Any]:
    """Elimina un perfil."""
    if name == "default":
        raise HTTPException(status_code=400, detail="No se puede eliminar el perfil por defecto.")
    if not _delete_profile(name):
        raise HTTPException(status_code=404, detail=f"Perfil '{name}' no encontrado.")
    return {"ok": True, "deleted": name}


# ---------------------------------------------------------------------------
class AgentCreateRequest(BaseModel):
    description: str = Field(min_length=8, max_length=2000)
    model: str = DEFAULT_MODEL


@router.post(Route.AGENTS_CREATE)
def api_agents_create(req: AgentCreateRequest) -> StreamingResponse:
    """FÁBRICA DE AGENTES: el usuario describe un especialista en lenguaje
    natural y la IA (Ollama) autogenera su perfil técnico completo.

    Flujo SSE: agent_create_start → vram_flush (Regla de Oro: la generación
    también es un turno) → token… → agent_created (perfil completo, listo
    para ser inyectado en la cadena por el Arquitecto).
    """
    if not RUN_LOCK.acquire(blocking=False):
        raise HTTPException(
            status_code=409,
            detail="La balsa ya está en misión: la Fábrica también ocupa la GPU (relevo secuencial).",
        )

    def generator() -> Iterator[str]:
        try:
            session_id = _latest_task_id()
            yield sse(SseEvent.agent_create_start, {
                "description": req.description, "session_id": session_id,
            })
            # ⚓ REGLA DE ORO: flush antes de generar (protege los 8GB)
            flush = flush_vram(req.model)
            yield sse(SseEvent.vram_flush, {"agent": "agent-factory", "iteration": 0, **flush})
            if not flush["ok"]:
                yield sse(SseEvent.agent_create_error, {
                    "detail": flush.get("error") or flush.get("detail") or "VRAM flush fallido",
                })
                return
            session = _LlmSession(req.model)
            obj: Optional[Dict[str, Any]] = None
            text = ""
            for attempt in (1, 2):
                prompt = build_profile_prompt(req.description)
                if attempt == 2:
                    prompt += (
                        "\n\nERROR DEL INTENTO ANTERIOR: no emitiste un JSON válido "
                        "con las 6 claves. Reemite SOLO el bloque ```json."
                    )
                text, _stats = yield from stream_llm(  # type: ignore[misc]
                    session, "agent-factory", AGENT_FACTORY_SYSTEM, prompt
                )
                obj = extract_json_object(text)
                if obj:
                    break
                if attempt == 1:
                    yield sse(SseEvent.system, {
                        "text": "⚠️ Perfil no parseable: pidiendo reemisión a la Fábrica."
                    })
            if not obj:
                yield sse(SseEvent.agent_create_error, {
                    "detail": "La Fábrica no logró emitir un JSON de perfil válido tras 2 intentos."
                })
                return
            try:
                agent = normalize_agent_profile(obj, "dyn-" + uuid.uuid4().hex[:6], session_id)
            except ValueError as ve:
                yield sse(SseEvent.agent_create_error, {"detail": str(ve)})
                return
            DYNAMIC_AGENTS[agent.id] = agent
            _append_session_event(
                session_id, {"kind": "agent_created", "agent": agent.to_dict()}
            )
            yield sse(SseEvent.agent_created, {"agent": agent.to_dict(), "session_id": session_id})
        except AbortRequested:
            yield sse(SseEvent.agent_create_error, {"detail": "Creación abortada."})
        except Exception as exc:  # noqa: BLE001
            yield sse(SseEvent.agent_create_error, {"detail": f"{type(exc).__name__}: {exc}"})
        finally:
            RUN_LOCK.release()

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ------------------------------ MISIÓN / SSE -------------------------------

class TaskRequest(BaseModel):
    task: str = Field(min_length=1, max_length=8000)
    model: str = DEFAULT_MODEL
    loop_mode: bool = False
    mode: str = "chain"                  # "chain" | "chat"
    start_agent: str = "architect"       # architect|researcher|developer|reviewer
    hacker: bool = False                 # 🏴 prompt sin censura (denylist intacta)
    num_ctx: Optional[int] = Field(default=None, ge=2048, le=131072)
    force: bool = False                  # aborta la misión en curso y toma el relevo
    # ── v4.1 · comandos estilo Claude Code ──
    goal: str = Field(default="", max_length=600)        # /goal: objetivo mayor
    plan_only: bool = False                              # /ultraplan: planifica y ESPERA
    ultra_review: bool = False                           # /ultrareview: auditoría profunda
    resume_plan: Optional[Dict[str, str]] = None         # {"plan":…, "context":…} ejecuta un plan aprobado
    resume_task: str = Field(default="", max_length=8000)  # tarea original al ejecutar un plan (modo chat)
    # 🧵 continuidad conversacional: id de la misión anterior cuyo workspace
    # y transcript se heredan (hilo estilo ChatGPT/Claude)
    continue_task: Optional[str] = Field(default=None) # Pattern quitado para permitir "" en el test
    # FASE 3 · perfil de configuración (override de model/temperature/num_ctx)
    profile: Optional[str] = Field(default=None, max_length=50)
    # FASE 4 · /sys: regla temporal inyectada al contexto del sistema
    system_inject: Optional[str] = Field(default=None, max_length=2000)
    # FASE 4 · /yolo: desactivar confirmaciones de seguridad
    yolo: bool = False
    # B · tope configurable del bucle Programador↔Revisor
    max_rounds: Optional[int] = Field(default=None, ge=1, le=25)
    skill: Optional[str] = Field(default=None, max_length=64)
    resume_checkpoint: Optional[str] = Field(default=None, max_length=80)
    project_root: Optional[str] = Field(default=None, max_length=500)


@router.post(Route.TASK)
def api_task(req: TaskRequest) -> StreamingResponse:
    """Arranca la misión. Responde con un stream SSE (eventos en vivo)."""
    # FASE 3 · resolver perfil para esta misión
    _task_profile = _otter_profiles._ACTIVE_PROFILE
    if req.profile:
        _p = _get_profile(req.profile)
        if _p:
            _task_profile = _p
    if req.mode not in ("chain", "chat"):
        raise HTTPException(status_code=400, detail="mode debe ser 'chain' o 'chat'.")
    # v4.3 · 'agent' (Claude Code) solo tiene sentido en modo chat
    # v6.0 · Fase 1 · PERFILES: en modo chat puedes hablar con CUALQUIER
    # agente registrado (Otter, núcleo, presets o creados con la Fábrica).
    if req.mode == "chat":
        _valid = sorted(set(CORE_AGENTS) | set(DYNAMIC_AGENTS))
        if req.start_agent not in _valid:
            raise HTTPException(
                status_code=400,
                detail=(f"Perfil '{req.start_agent}' no registrado. Opciones: "
                        f"{', '.join(_valid)}."))
    elif req.start_agent not in AGENT_ORDER:
        raise HTTPException(
            status_code=400,
            detail=(f"start_agent inválido para batallón. "
                    f"Opciones: {', '.join(AGENT_ORDER)}."))
    # 🧵 continuidad conversacional: el hilo anterior debe existir
    if req.continue_task:
        if not re.fullmatch(r"[A-Za-z0-9\-_]+", req.continue_task):
            raise HTTPException(status_code=400, detail="continue_task contiene caracteres inválidos.")
        _ct_dir = (WORKSPACE_ROOT / req.continue_task).resolve()
        if not str(_ct_dir).startswith(str(WORKSPACE_ROOT.resolve())) or not _ct_dir.is_dir():
            raise HTTPException(
                status_code=404,
                detail=f"No existe la conversación '{req.continue_task}' para continuar.",
            )
    if not RUN_LOCK.acquire(blocking=False):
        # v3.3 · TOMAR EL RELEVO: con force=True se aborta la misión en curso
        # (cierre agresivo del socket incluido) y se espera a que el lock
        # quede libre; sin force, aviso 409 claro.
        if req.force:
            for active in list(ACTIVE_RUN.values()):
                _force_stop_run(active)
            deadline = time.time() + 8.0
            while time.time() < deadline:
                if RUN_LOCK.acquire(blocking=False):
                    break
                time.sleep(0.15)
            else:
                raise HTTPException(
                    status_code=409,
                    detail="La misión anterior no liberó el relevo a tiempo; reintenta en unos segundos.",
                )
        else:
            raise HTTPException(
                status_code=409,
                detail="La balsa ya está en misión (relevo secuencial 1 a 1). Espera a que termine, aborta o usa force=true.",
            )
    # 🧵 continuidad: si existe un hilo previo válido, REUTILIZA el mismo
    # task_id/workspace (en vez de crear una nueva sesión). Así, "seguir en el
    # chat en el que ya se trabajaba" produce UNA sola entrada en el historial
    # y las burbujas anteriores se conservan. Si el hilo ya no existe (borrado)
    # cae al fallback: id y workspace nuevos.
    adopted_task_id: Optional[str] = None
    prev_transcript: List[Dict[str, Any]] = []
    prev_meta: Optional[Dict[str, Any]] = None
    if req.continue_task:
        src = WORKSPACE_ROOT / req.continue_task
        tp = src / "ottercode_transcript.json"
        if src.is_dir() and tp.exists():
            adopted_task_id = req.continue_task
            try:
                data = json.loads(tp.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    prev_transcript = data.get("transcript") or []
                    prev_meta = data.get("meta")
            except (json.JSONDecodeError, OSError):
                prev_transcript = []
    original_title: Optional[str] = None
    if prev_meta and isinstance(prev_meta, dict) and prev_meta.get("task"):
        original_title = str(prev_meta["task"])
    task_id = adopted_task_id or (
        datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    )
    from backend.project import resolve_workdir
    from backend.workspace_git import ensure_mission_git, maybe_auto_rag
    workdir = resolve_workdir(task_id, req.project_root)
    workdir.mkdir(parents=True, exist_ok=True)
    try:
        ensure_mission_git(workdir, task_id)
    except Exception:
        pass
    maybe_auto_rag(workdir)

    run = OtterRun(
        task_id, req.task.strip(),
        req.model.strip() or _task_profile.get("model", DEFAULT_MODEL),
        req.loop_mode, req.mode, req.start_agent, workdir,
        hacker=bool(req.hacker),
        num_ctx=req.num_ctx or _task_profile.get("num_ctx"),
        temperature=_task_profile.get("temperature", 0.7),
        top_p=_task_profile.get("top_p", 0.9),
        goal=req.goal, plan_only=req.plan_only,
        ultra_review=req.ultra_review, resume_plan=req.resume_plan,
        resume_task=req.resume_task,
        continue_task=req.continue_task or "",
        system_inject=req.system_inject,
        max_rounds=req.max_rounds,
    )
    # FASE 4 · YOLO guard
    run.yolo = bool(req.yolo)

    # 🧵 hilo adoptado: precargar el transcript previo SIN duplicar el mensaje
    # del usuario — OtterRun.__init__ ya añadió la entrada user y los marcadores
    # de sistema ("⚓ Misión zarpada" y "🧵 Continuando…"). El nuevo mensaje del
    # usuario se inserta tras el historial y delante de los marcadores del turno.
    if adopted_task_id:
        _init_user = run.transcript[0]          # entrada user del turno actual
        _init_sys = [e for e in run.transcript if e.get("kind") == "system"]
        run.transcript = list(prev_transcript) + [_init_user] + _init_sys
        if prev_meta and isinstance(prev_meta, dict):
            pass  # conservamos el task inicial del run (el de la misión)
    else:
        pass  # __init__ ya registró la entrada user y el system ⚓

    run.memory_block = _memory_recall(run.task_text)   # 🧠 recuerdo del vault
    if req.resume_checkpoint:
        from backend.runstate import resume_summary
        _rs = resume_summary(req.resume_checkpoint)
        if _rs:
            run.system_inject = (run.system_inject or "") + "\n\n" + _rs
            run.transcript.append({"kind": "system", "text": "▶ Reanudando desde checkpoint " + req.resume_checkpoint})
    ACTIVE_RUN[run.task_id] = run
    # Dedup en el historial: si el hilo ya tenía meta, reemplázalo (1 fila) y
    # CONSERVA el título original de la conversación (no el del nuevo mensaje).
    if adopted_task_id and prev_meta:
        if original_title:
            run.meta["task"] = original_title
        for i, m in enumerate(HISTORY):
            if m.get("id") == adopted_task_id:
                HISTORY[i] = run.meta
                break
        else:
            HISTORY.insert(0, run.meta)
    else:
        HISTORY.insert(0, run.meta)
    _activity_set(running=True, status="running", task_id=run.task_id,
                  task=run.task_text, mode=run.mode, loop_mode=run.loop_mode,
                  started_at=time.time(), elapsed_s=0.0,
                  agent=run.start_agent,
                  agent_nombre=get_agent(run.start_agent).nombre,
                  agent_icon=get_agent(run.start_agent).icon,
                  iteration=0, last_tool=None, last_tool_ok=None)

    from backend.router import route, direct_reply
    from backend.md_skills import get_md_skill, active_skill_prompt
    _decision = {"tipo": "agente", "destino": req.start_agent}
    if os.environ.get("OTTERCODE_ROUTER", "1") != "0" and not req.continue_task:
        try:
            _decision = route(req.task)
        except Exception:
            pass
    if req.skill:
        _decision = {"tipo": "skill", "destino": req.skill}
        sk = get_md_skill(req.skill)
        if sk:
            extra = f"\n\n# SKILL {sk['name']}\n{sk['body'][:4000]}"
            run.system_inject = (run.system_inject or "") + extra
    elif _decision.get("tipo") == "skill":
        sk = get_md_skill(str(_decision.get("destino") or ""))
        if sk:
            run.system_inject = (run.system_inject or "") + f"\n\n# SKILL {sk['name']}\n{sk['body'][:4000]}"

    def generator() -> Iterator[str]:
        """Puente cola+worker: si el modelo está cargando a VRAM y no fluyen
        tokens, emite heartbeats SSE (': heartbeat') para que ningún proxy
        o cliente corte la conexión por inactividad."""
        if _decision.get("tipo") == "directo" and req.mode == "chat":
            try:
                yield sse(SseEvent.session_id, {"task_id": run.task_id})
                yield sse(SseEvent.system, {"text": "⚡ router (modelo pequeño, keep_alive=-1)"})
                text = direct_reply(req.task)
                for ch in text:
                    yield sse(SseEvent.token, {"agent": "router", "token": ch})
                run.transcript.append({"kind": "agent", "agent": "router", "text": text})
                yield sse(SseEvent.task_done, {
                    "task_id": run.task_id, "mode": "chat", "approved": True,
                    "iterations": 0, "files": [], "duration_s": 0, "review_verdict": "",
                    "injected_agents": [], "router": True,
                })
            finally:
                _activity_finish("done")
                RUN_LOCK.release()
                ACTIVE_RUN.pop(run.task_id, None)
            return
        q: "queue.Queue" = queue.Queue()
        sentinel = object()

        def worker() -> None:
            try:
                q.put(sse(SseEvent.session_id, {"task_id": run.task_id}))
                for chunk in run_task_stream(run):
                    q.put(chunk)
            finally:
                q.put(sentinel)

        # v4.4 · aviso de cold start: si el modelo no está cargado en VRAM,
        # la primera generación tardará más de lo normal.
        try:
            payload_ps = requests.get(f"{OLLAMA_BASE_URL}/api/ps", timeout=3).json()
            modelos_ps = payload_ps.get("models") if isinstance(payload_ps, dict) else []
            cargados = {str(m.get("name", ""))
                        for m in (modelos_ps or []) if isinstance(m, dict)}
            if run.model not in cargados:
                yield sse(SseEvent.system, {
                    "text": f"📦 Cargando {run.model} en VRAM… "
                            f"la primera generación puede tardar más de lo normal."
                })
        except requests.RequestException:
            pass

        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        try:
            while True:
                try:
                    item = q.get(timeout=15)
                except queue.Empty:
                    yield ": heartbeat\n\n"
                    continue
                if item is sentinel:
                    break
                yield item
        finally:
            # Cliente desconectado o fin de stream: para el worker y libera
            # el relevo (el worker detecta run.aborted en su bucle de tokens).
            if not run.aborted and getattr(run, "elapsed", None):
                _activity_finish("done")
            else:
                _activity_finish("aborted")
            run.aborted = True
            RUN_LOCK.release()
            ACTIVE_RUN.pop(run.task_id, None)

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post(Route.TASK_ABORT)
def api_abort(task_id: str) -> Dict[str, bool]:
    run = ACTIVE_RUN.get(task_id)
    if run is None:
        raise HTTPException(status_code=404, detail="No hay ninguna misión activa con ese id.")
    _force_stop_run(run)
    return {"ok": True}


# --------------------------- SKILLS DIRECTOS (UI) --------------------------

def _resolve_workspace(task_id: Optional[str]) -> Tuple[Path, str]:
    tid = task_id or (_latest_task_id())
    if not tid:
        raise HTTPException(status_code=404, detail="No hay ninguna misión con workspace todavía. Lanza primero una tarea.")
    if not re.fullmatch(r"[A-Za-z0-9\-_]+", tid):
        raise HTTPException(status_code=400, detail="Id de sesión inválido.")
    workdir = (WORKSPACE_ROOT / tid).resolve()
    if not workdir.is_dir():
        raise HTTPException(status_code=404, detail="Tarea no encontrada.")
    return workdir, tid


def _latest_task_id() -> Optional[str]:
    return HISTORY[0]["id"] if HISTORY else None


class SkillRequest(BaseModel):
    tool: str
    args: Dict[str, Any] = Field(default_factory=dict)
    task_id: Optional[str] = None


_SKILL_SAFE_TOOLS = {
    "list_dir", "tree", "read_file", "grep_search", "glob_files",
    "write_file", "append_file", "mkdir", "edit_file",
    "todo_write", "todo_read", "hash_text", "uuid_gen", "base64_encode",
    "base64_decode", "sys_info", "web_fetch", "web_search",
    "wikipedia_search", "arxiv_search", "vault_search", "vault_read",
    "image_describe", "model_list", "model_info", "embed_text",
    "csv_peek", "json_query", "http_request",
    "sqlite_query", "files", "git_status", "git_diff", "git_log",
}


@router.post(Route.SKILL)
def api_skill(req: SkillRequest) -> Dict[str, Any]:
    """Ejecuta una skill directamente desde la UI (slash commands /read_file,
    /bash, /tree, /ls…) sobre el workspace de la última misión.
    execute_bash y python_exec solo están disponibles en contexto de misión."""
    workdir, tid = _resolve_workspace(req.task_id)
    executor = tools.ToolExecutor(workdir)
    if req.tool == "files":
        files = executor.list_workspace()
        output = "\n".join(f"📄 {f['path']}  ({f['size']} B)" for f in files) or "(workspace vacío)"
        return {"ok": True, "output": output, "ms": 0,
                "title": tools.tool_title("files", {}), "task_id": tid}
    canonical = tools.resolve_name(req.tool)
    if canonical not in tools.TOOL_NAMES:
        raise HTTPException(
            status_code=400,
            detail=f"Herramienta desconocida: {req.tool}. Válidas: {', '.join(tools.TOOL_NAMES)}.",
        )
    if canonical not in _SKILL_SAFE_TOOLS:
        raise HTTPException(
            status_code=403,
            detail=f"Herramienta '{canonical}' no permitida vía /api/skill sin contexto de misión.",
        )
    result = executor.dispatch(canonical, req.args)
    return {**result, "title": tools.tool_title(canonical, req.args), "task_id": tid}


@router.get(Route.TREE)
def api_tree(task_id: Optional[str] = None, path: str = ".") -> Dict[str, Any]:
    """Árbol del workspace (context management) para la UI."""
    workdir, tid = _resolve_workspace(task_id)
    executor = tools.ToolExecutor(workdir)
    try:
        tree = executor.tree(path)
    except tools.ToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {"ok": True, "task_id": tid, "path": path, "tree": tree}


# --------------------------- Explorer (estilo Arena) ------------------------

_WS_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv",
                 ".pytest_cache", ".cache", ".mypy_cache", ".ruff_cache"}
_WS_MAX_NODES = 2000


def _ws_node(p: Path, budget: List[int]) -> Optional[Dict[str, Any]]:
    """Nodo JSON recursivo con tope de nodos y skip de basura."""
    if budget[0] <= 0:
        return None
    try:
        stat = p.stat()
    except OSError:
        return None
    node: Dict[str, Any]
    if p.is_dir():
        children = []
        try:
            entries = sorted(p.iterdir(), key=lambda c: (not c.is_dir(), c.name.lower()))
        except OSError:
            entries = []
        for child in entries:
            # 🛟 el transcript de auditoría no aparece en el árbol del Explorer
            if child.name in _WS_SKIP_DIRS or child.name == "ottercode_transcript.json":
                continue
            if child.is_symlink():
                continue
            budget[0] -= 1
            sub = _ws_node(child, budget)
            if sub:
                children.append(sub)
        node = {"name": p.name, "type": "dir", "children": children}
    else:
        node = {"name": p.name, "type": "file", "size": stat.st_size}
    return node


@router.get(Route.WORKSPACE)
def api_workspace(task_id: Optional[str] = None) -> Dict[str, Any]:
    """Explorer completo: árbol JSON + stats del workspace de una tarea."""
    workdir, tid = _resolve_workspace(task_id)
    n_files, n_dirs, total_bytes = 0, 0, 0
    for p in workdir.rglob("*"):
        if p.name == "ottercode_transcript.json" or any(
            part in _WS_SKIP_DIRS for part in p.parts
        ):
            continue
        if p.is_file():
            n_files += 1
            try:
                total_bytes += p.stat().st_size
            except OSError:
                pass
        elif p.is_dir():
            n_dirs += 1
    budget = [_WS_MAX_NODES]
    tree = _ws_node(workdir, budget)
    if not tree:
        raise HTTPException(status_code=500, detail="Workspace ilegible.")
    from backend.hooks import load_file_hooks
    try:
        from backend.workspace_git import maybe_auto_rag
        maybe_auto_rag(workdir)
    except Exception:
        pass
    return {
        "ok": True,
        "task_id": tid,
        "name": workdir.name,
        "stats": {
            "files": n_files,
            "dirs": n_dirs,
            "bytes": total_bytes,
            "human": f"{n_files} archivos · {total_bytes / 1024:.1f} KB"
            if total_bytes < 1024 ** 2 else f"{n_files} archivos · {total_bytes / 1024 ** 2:.1f} MB",
        },
        "truncated": budget[0] <= 0,
        "tree": tree.get("children", []),
        "hooks": load_file_hooks(workdir),
    }


# --------------------------- Entregables / archivos ------------------------

class FileSaveRequest(BaseModel):
    task_id: str
    path: str
    content: str = ""


@router.post(Route.FILE_SAVE)
def api_file_save(req: FileSaveRequest) -> Dict[str, Any]:
    """Guarda un archivo del workspace desde Studio (edición humana)."""
    workdir, tid = _resolve_workspace(req.task_id)
    try:
        executor = tools.ToolExecutor(workdir)
        target = executor.resolve_safe(req.path)
    except tools.ToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(req.content, encoding="utf-8")
    return {"ok": True, "task_id": tid, "path": req.path, "bytes": len(req.content.encode("utf-8"))}


@router.get(Route.FILE)
def api_file(task_id: str, path: str, download: int = 0):
    """Sirve un archivo del workspace (viewer UI). download=1 fuerza descarga."""
    workdir, _ = _resolve_workspace(task_id)
    try:
        target = tools.ToolExecutor(workdir).resolve_safe(path)
    except tools.ToolError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not target.is_file():
        raise HTTPException(status_code=404, detail="El archivo no existe en el workspace.")
    if download:
        return FileResponse(target, filename=target.name)
    return FileResponse(target)


@router.get(Route.TASK_ZIP)
def api_task_zip(task_id: str) -> StreamingResponse:
    """Descarga el workspace completo de una tarea como ZIP."""
    workdir, _ = _resolve_workspace(task_id)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(workdir.rglob("*")):
            if p.is_file() and p.name != "ottercode_transcript.json":
                zf.write(p, p.relative_to(workdir))
    payload = buf.getvalue()
    return StreamingResponse(
        iter([payload]),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="ottercode-{task_id}.zip"',
            "Content-Length": str(len(payload)),
        },
    )


# ------------------------------- HISTORIA ----------------------------------

@router.get(Route.HISTORY)
def api_history(search: str = "", limit: int = 50, offset: int = 0) -> Dict[str, List[Dict[str, Any]]]:
    # FASE 2 · SQLite + FTS5 búsqueda (fallback a in-memory si DB vacía)
    try:
        sessions = search_history(search, limit, offset)
        if sessions:
            return {"sessions": sessions}
    except Exception:
        pass
    # Fallback a lista in-memory (legacy)
    return {"sessions": HISTORY[:limit]}


@router.get(Route.HISTORY_DETAIL)
def api_history_detail(task_id: str) -> Dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9\-_]+", task_id):
        raise HTTPException(status_code=400, detail="Id de sesión inválido.")
    # FASE 2 · intentar SQLite primero
    try:
        detail = get_session_detail(task_id)
        if detail:
            return detail
    except Exception:
        pass
    # Fallback a JSON legacy
    path = WORKSPACE_ROOT / task_id / "ottercode_transcript.json"
    if not path.exists():
        raise HTTPException(status_code=404, detail="Sesión no encontrada.")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        raise HTTPException(status_code=500, detail="Transcript corrupto.")


@router.delete(Route.HISTORY_DELETE)
def api_history_delete(task_id: str) -> Dict[str, Any]:
    """Borra un chat: transcript + carpeta del workspace + entrada del historial."""
    if not re.fullmatch(r"[A-Za-z0-9\-_]+", task_id):
        raise HTTPException(status_code=400, detail="Id de sesión inválido.")
    workdir = (WORKSPACE_ROOT / task_id).resolve()
    if not str(workdir).startswith(str(WORKSPACE_ROOT.resolve())) or not workdir.is_dir():
        raise HTTPException(status_code=404, detail="Sesión no encontrada.")
    active = ACTIVE_RUN.get(task_id)
    if active is not None:
        _force_stop_run(active)
        raise HTTPException(status_code=409, detail="Esa sesión está en misión: aborta primero.")
    shutil.rmtree(workdir, ignore_errors=True)
    HISTORY[:] = [m for m in HISTORY if m.get("id") != task_id]
    # FASE 2 · eliminar de SQLite también
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("DELETE FROM messages WHERE session_id = ?", (task_id,))
        conn.execute("DELETE FROM sessions WHERE id = ?", (task_id,))
        conn.commit()
        conn.close()
    except Exception as _e:  # noqa: BLE001
        print(f"[WARN] SQLite delete falló para {task_id}: {_e}", flush=True)
    return {"ok": True, "deleted": task_id}


# FASE 4 · /save — renombrar sesión en SQLite + HISTORY in-memory

class RenameRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)


@router.post(Route.HISTORY_RENAME)
def api_history_rename(task_id: str, req: RenameRequest) -> Dict[str, Any]:
    """Renombra la tarea de una sesión en SQLite e historial in-memory."""
    if not re.fullmatch(r"[A-Za-z0-9\-_]+", task_id):
        raise HTTPException(status_code=400, detail="Id de sesión inválido.")
    new_name = req.name.strip()
    if not new_name:
        raise HTTPException(status_code=400, detail="El nombre no puede estar vacío.")
    # Sanitizar: eliminar tags HTML y limitar longitud
    import re as _re
    new_name = _re.sub(r"<[^>]+>", "", new_name)[:200]
    # Actualizar en memoria
    for m in HISTORY:
        if m.get("id") == task_id:
            m["task"] = new_name
            break
    # Actualizar en SQLite
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute("UPDATE sessions SET task = ? WHERE id = ?", (new_name, task_id))
        conn.commit()
        conn.close()
    except Exception as _e:  # noqa: BLE001
        print(f"[WARN] SQLite rename falló para {task_id}: {_e}", flush=True)
    return {"ok": True, "id": task_id, "name": new_name}


# FASE 9 · Pruning / Compresión de sesiones en SQLite
@router.post(Route.HISTORY_PRUNE)
def api_history_prune(task_id: str) -> Dict[str, Any]:
    """Comprime mensajes antiguos de una sesión SQLite reemplazándolos por un resumen."""
    if not re.fullmatch(r"[A-Za-z0-9\-_]+", task_id):
        raise HTTPException(status_code=400, detail="Id de sesión inválido.")
    try:
        conn = sqlite3.connect(str(DB_PATH))
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, kind, text FROM messages WHERE session_id = ? ORDER BY id ASC",
            (task_id,)
        ).fetchall()
        if len(rows) <= 6:
            conn.close()
            return {"ok": True, "pruned": False, "message": "Sesión corta (≤6 mensajes), no requiere pruning."}
        
        # Guardar los últimos 4 mensajes intactos, resumir el resto
        old_rows = rows[:-4]
        keep_ids = [r["id"] for r in rows[-4:]]
        old_ids = [r["id"] for r in old_rows]
        
        raw_text = "\n".join(f"{r['kind']}: {r['text'][:150]}" for r in old_rows)
        summary = f"[RESUMEN DE SESIÓN PRUNED — {len(old_rows)} MENSAJES COMPRIMIDOS]\n{raw_text[:600]}"
        
        # Eliminar mensajes antiguos de la tabla messages y FTS
        conn.execute(f"DELETE FROM messages WHERE id IN ({','.join('?'*len(old_ids))})", old_ids)
        # Insertar un mensaje 'system' con el resumen al inicio
        conn.execute(
            """INSERT INTO messages (session_id, kind, agent, iteration, text)
               VALUES (?, 'system', 'system', 0, ?)""",
            (task_id, summary)
        )
        conn.commit()
        conn.close()
        return {"ok": True, "pruned": True, "compressed_count": len(old_rows), "summary": summary[:200]}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error en pruning: {exc}")


class SkillEnableRequest(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    enable: bool = True


@router.post(Route.SKILLS_ENABLE)
def api_skills_enable(req: SkillEnableRequest) -> Dict[str, Any]:
    """Activa o desactiva una skill dinámica/globalmente."""
    canonical = tools.resolve_name(req.name)
    if canonical not in tools.TOOLS and req.name not in tools.TOOLS:
        raise HTTPException(status_code=404, detail=f"Skill '{req.name}' no encontrada.")
    
    set_skill_enabled(canonical if canonical in tools.TOOLS else req.name, req.enable)
    return {"ok": True, "skill": canonical if canonical in tools.TOOLS else req.name, "enabled": req.enable}



class ApproveRequest(BaseModel):
    id: str
    allow: bool

@router.get(Route.PROJECT)
def api_project_get() -> Dict[str, Any]:
    from backend.project import load_project
    return load_project()


class ProjectRequest(BaseModel):
    path: str = ""


@router.post(Route.PROJECT)
def api_project_set(req: ProjectRequest) -> Dict[str, Any]:
    from backend.project import save_project
    try:
        return save_project(req.path)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.get(Route.MCP)
def api_mcp() -> Dict[str, Any]:
    try:
        import mcp_client
        mgr = mcp_client.get_mcp_manager()
        ready = False
        try:
            ready = bool(mcp_client.mcp_loop.is_ready())
        except Exception:
            ready = bool(mgr.is_available)
        servers = []
        for name, sess in (mgr.sessions or {}).items():
            conn = (mgr._connections or {}).get(name)
            servers.append({
                "name": name,
                "connected": bool(conn and conn.connected),
                "tools": list(conn.tools) if conn else [],
            })
        return {
            "ok": True,
            "ready": ready,
            "servers": servers,
            "tools": list(mgr.tools_catalog.keys()),
        }
    except Exception as exc:
        return {"ok": False, "ready": False, "servers": [], "tools": [], "error": str(exc)}


@router.post("/api/approve")
def api_approve(req: ApproveRequest):
    from backend.engine import PENDING_PERMISSIONS, PERMISSION_RESPONSES
    if req.id in PENDING_PERMISSIONS:
        PERMISSION_RESPONSES[req.id] = req.allow
        PENDING_PERMISSIONS[req.id].set()
        return {"ok": True}
    return {"ok": False, "detail": "Solicitud no encontrada"}
