import json
import os
import threading
import traceback
import uuid
import tools
from pathlib import Path
from typing import Iterator, Optional, Dict, Any, List, Tuple

# FASE 5 · Gate de permisos para herramientas destructivas
PENDING_PERMISSIONS: Dict[str, threading.Event] = {}
PERMISSION_RESPONSES: Dict[str, bool] = {}
ASK_PERMISSIONS = os.environ.get("OTTERCODE_ASK_PERMISSIONS", "1").strip().lower() not in ("0", "false", "no")
_DANGEROUS_TOOLS = {"python_exec", "execute_bash"}
_WRITE_FS_TOOLS = {"write_file", "append_file", "edit_file", "mkdir"}


def _path_outside_workspace(run: Any, args: Dict[str, Any]) -> bool:
    fp = str(args.get("filepath") or args.get("path") or "").strip()
    if not fp:
        return False
    p = Path(fp).expanduser()
    if not p.is_absolute():
        return False
    try:
        p.resolve().relative_to(Path(run.workdir).resolve())
        return False
    except ValueError:
        return True


def _needs_permission(run: Any, name: str, args: Dict[str, Any]) -> bool:
    if not ASK_PERMISSIONS or getattr(run, "yolo", False):
        return False
    if getattr(run, "_session_allow", False):
        return False
    if name in _DANGEROUS_TOOLS or str(name).startswith("mcp__"):
        return True
    if name in _WRITE_FS_TOOLS and _path_outside_workspace(run, args):
        return True
    return False

from backend.vault import memory_note_for_run  # noqa: E402
from backend.runtime import _activity_finish  # noqa: E402
from backend.runstate import OtterRun, _condense_entries, append_checkpoint, note_stall, resume_summary  # noqa: E402
from backend.prompts import _looks_like_tool_attempt, _prev_conversation_block, alias_args, build_architect_prompt, build_chat_prompt, build_developer_prompt, build_expert_prompt, build_researcher_prompt, build_reviewer_prompt, extract_injections, extract_tool_call, format_tool_result, is_approved  # noqa: E402
from backend.ollama import ContextOverflow, _estimate_tokens, _ollama_ndjson_text, flush_vram, stream_llm  # noqa: E402
from backend.history import TOOL_CAPABLE_MODELS, save_session  # noqa: E402
from backend.db import save_session_to_db  # noqa: E402
from backend.config import HACKER_SUFFIX, LLM_BACKEND, MAX_INJECTIONS, MAX_TOOL_STEPS, NUM_CTX_DEFAULT, NUM_PREDICT_DEFAULT, OLLAMA_BASE_URL  # noqa: E402
from backend.agents import AGENT_ORDER, Agent, DYNAMIC_AGENTS, ULTRAREVIEW_SUFFIX, _CORE_BASE_PROMPTS, _agent_meta, _chat_base, get_agent, skill_enabled, tool_protocol  # noqa: E402
from backend.agents import *  # noqa: F401,F403
from backend.prompts import *  # noqa: F401,F403
from backend.ollama import *  # noqa: F401,F403
from backend.runstate import *  # noqa: F401,F403
from backend.runtime import *  # noqa: F401,F403  (hen ATIVITY, ACTIVE_RUN)
from backend.runtime import _force_stop_run, _activity_set, _activity_finish  # noqa
from backend.history import *  # noqa: F401,F403
from backend.vault import _memory_finish, _memory_recall, memory_note_for_run  # noqa
from backend.memory import get_memory, harvest_memory  # noqa




_COMPACT_SYSTEM = (
    "Eres el compresor de memoria de OtterCode. Resume el estado del trabajo "
    "en ESPAÑOL. Formato exacto:\n"
    "OBJETIVO: <una línea>\nHECHO: <logros>\n"
    "PENDIENTE: <lo que falta>\nDATOS CLAVE: <rutas, flags, versiones>\n"
    "ERRORES LITERALES: <mensajes de error PEGADOS COMPLETOS, sin parafrasear>\n"
    "RESTRICCIONES DEL USUARIO: <preferencias y vetos explícitos>\n"
    "DEPURACIÓN: <qué se probó, qué se descartó, con qué evidencia>\n"
    "REGLAS: conserva CUALQUIER contenido pegado por el usuario (logs, stack "
    "traces, fragmentos de config) palabra por palabra. Conserva nombres de "
    "flags, rutas, números de versión y mensajes de error exactos. No inventes "
    "ni suavices. Máximo 400 palabras salvo citas literales de error."
)
_COMPACT_TAIL_N = int(os.environ.get("OTTERCODE_COMPACT_TAIL", "6") or "6")
_TODO_IDLE_MAX = int(os.environ.get("OTTERCODE_TODO_IDLE_MAX", "3") or "3")
_RESCUE_MAX_TRIES = int(os.environ.get("OTTERCODE_RESCUE_TRIES", "3") or "3")


def _reserved_output_tokens(run: Any) -> int:
    pred = getattr(run, "num_predict", None)
    if pred is None:
        pred = os.environ.get("OTTERCODE_NUM_PREDICT") or NUM_PREDICT_DEFAULT
    try:
        return max(256, int(pred))
    except (TypeError, ValueError):
        return int(NUM_PREDICT_DEFAULT)


def _active_num_ctx(run: Any) -> int:
    try:
        return max(1024, int(getattr(run, "num_ctx", None) or NUM_CTX_DEFAULT))
    except (TypeError, ValueError):
        return int(NUM_CTX_DEFAULT)


def _compact_threshold(run: Any) -> int:
    num_ctx = _active_num_ctx(run)
    return max(512, int(num_ctx * 0.55))


def _call_token_estimate(run: Any, system_prompt: str, prompt: str = "") -> int:
    hist = "".join(str(m.get("content") or "") for m in (getattr(run, "messages", None) or []))
    tools_blob = ""
    try:
        tools_blob = json.dumps(tools.get_ollama_tools(), ensure_ascii=False)[:80_000]
    except Exception:
        tools_blob = "x" * 1600
    return (
        _estimate_tokens(system_prompt)
        + _estimate_tokens(hist)
        + _estimate_tokens(prompt)
        + _estimate_tokens(tools_blob)
    )


def _preflight_eval(run: Any, system_prompt: str, prompt: str = "") -> Dict[str, Any]:
    try:
        est = _call_token_estimate(run, system_prompt, prompt)
        thresh = _compact_threshold(run)
        num_ctx = _active_num_ctx(run)
        reserved = min(_reserved_output_tokens(run), num_ctx // 2)
        over = est > thresh
    except Exception as exc:  # noqa: BLE001
        rec = {
            "preflight": True, "est": 0, "thresh": 10**9,
            "num_ctx": _active_num_ctx(run), "reserved": 0, "over": False,
            "error": f"{type(exc).__name__}: {exc}",
        }
        run._last_compact_eval = rec
        return rec
    rec = {
        "preflight": True, "est": est, "thresh": thresh,
        "num_ctx": num_ctx, "reserved": reserved, "over": over,
    }
    run._last_compact_eval = rec
    return rec


def _set_step(run: Any, step: str) -> None:
    run._current_step = step


def _log_uncaught(run: Any, exc: BaseException, step: Optional[str] = None) -> str:
    """D1: traceback completo al log + línea en JSONL. Devuelve resumen de 1 línea."""
    step = step or str(getattr(run, "_current_step", "") or "desconocido")
    tid = getattr(run, "task_id", "") or "?"
    et = type(exc).__name__
    one = f"{et}: {exc}"[:400]
    tb = traceback.format_exc()
    print(f"[ottercode] UNCAUGHT mission_id={tid} step={step}\n{tb}", flush=True)
    try:
        append_checkpoint(
            run, kind="error", done=one,
            decisions=f"step={step}", pending="diagnóstico / reintentar",
            next_action="Reintentar desde checkpoint (no regenerar desde cero)",
            extra={"error_type": et, "step": step, "traceback": tb[-4000:]},
        )
    except Exception:
        pass
    return one


class RescueStalled(RuntimeError):
    """Rescate/completación falló 3 veces sobre el mismo archivo."""


import backend.config as _otter_cfg  # noqa: E402
import backend.profiles as _otter_profiles  # noqa: E402
def _compact_context(run: Any, agent_id: str, entries: List[Tuple[str, str]]) -> Optional[str]:
    """Llamada LLM corta (sin stream) que resume los pasos previos del turno.

    Devuelve None ante cualquier fallo (el llamador aplica truncado tonto).
    Nunca emite SSE: es un detalle interno del motor.
    """
    condensed = _condense_entries(entries)
    num_ctx = getattr(run, "num_ctx", None) or NUM_CTX_DEFAULT
    try:
        if LLM_BACKEND == "openai":
            resp = requests.post(
                f"{_chat_base()}/chat/completions",
                json={
                    "model": run.model, "stream": False,
                    "max_tokens": 512,
                    "messages": [
                        {"role": "system", "content": _COMPACT_SYSTEM},
                        {"role": "user", "content": condensed},
                    ],
                },
                timeout=(10, 120),
            )
            resp.raise_for_status()
            data = resp.json()
            return ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or None
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": run.model, "prompt": condensed,
                "system": _COMPACT_SYSTEM, "stream": False,
                "options": {"num_ctx": min(int(num_ctx), 4096), "num_predict": 400, "num_gpu": 99},
            },
            timeout=(10, 120),
        )
        resp.raise_for_status()
        return _ollama_ndjson_text(resp.text)
    except Exception:  # noqa: BLE001 — la compactación jamás tumba un turno
        return None


_MSG_CLIP = 2200


def _workspace_snapshot(run: Any) -> str:
    """Índice del disco: el proyecto vive en archivos, no en el KV de la GPU."""
    lines: List[str] = []
    try:
        for f in run.executor.list_workspace():
            if isinstance(f, dict):
                p = str(f.get("path") or "")
                sz = int(f.get("size") or 0)
            else:
                p, sz = str(f), 0
            if not p or p.startswith(".") or p.endswith(".indexed"):
                continue
            lines.append(f"- {p} ({sz} B)")
    except Exception:
        pass
    if not lines:
        return "(workspace vacío — aún no hay archivos)"
    return (
        "ARCHIVOS EN DISCO (edítalos con write_file/append_file/edit_file; "
        "NO los pegues enteros en el chat):\n" + "\n".join(lines[:48])
    )


def _clip_message(m: Dict[str, Any]) -> Dict[str, Any]:
    c = str(m.get("content") or "")
    if len(c) <= _MSG_CLIP:
        return m
    out = dict(m)
    out["content"] = (
        c[:700]
        + f"\n… [{len(c)} caracteres omitidos; el código está en el workspace]\n"
        + c[-350:]
    )
    return out


def _hard_trim_messages(run: Any) -> None:
    """Último recurso: deja resumen + 4 mensajes recientes. El repo sigue en disco."""
    msgs = list(run.messages or [])
    if not msgs:
        return
    head = msgs[:1]
    tail = [_clip_message(m) for m in msgs[-4:]]
    snap = _workspace_snapshot(run)
    run.messages = head + [{
        "role": "user",
        "content": (
            "[CHECKPOINT GPU — contexto reiniciado para no llenar VRAM]\n"
            "El historial largo está en el JSONL de la misión. "
            "Sigue el MISMO proyecto; no empieces de cero.\n"
            f"{snap}"
        ),
    }] + tail
    run._compact_blocked = False


def _maybe_compact_messages(run: Any, agent_id: str, system_prompt: str,
                            prompt: str = "", force: bool = False) -> Optional[str]:
    """T1: compactación PREFLIGHT (antes de la llamada, nunca a mitad de generación).

    Umbral = num_ctx activo − margen de salida reservado.
    T3: si una compactación no baja del umbral, no se reintenta en el mismo paso.
    Nunca lanza: un fallo aquí no debe abortar la misión.
    """
    try:
        run._llm_calls = int(getattr(run, "_llm_calls", 0) or 0) + 1
        ev = _preflight_eval(run, system_prompt, prompt)
        if getattr(run, "_compact_blocked", False) and not force:
            return None
        if not force and not ev.get("over"):
            return None
        msgs = [_clip_message(m) for m in (run.messages or [])]
        if len(msgs) < 3 and not force:
            run.messages = msgs
            return None
        tail_n = max(2, min(_COMPACT_TAIL_N, len(msgs)))
        tail = msgs[-tail_n:]
        old = msgs[1:-tail_n] if len(msgs) > tail_n + 1 else msgs[1:-2] if len(msgs) > 3 else []
        _cond = [(m.get("content", ""), "") for m in old]
        _summ = _compact_context(run, agent_id, _cond) or "(historial antiguo truncado)"
        head = msgs[:1] if msgs else []
        snap = _workspace_snapshot(run)
        run.messages = (
            head
            + [{"role": "user", "content": (
                "[CHECKPOINT DE COMPACTACIÓN — resumen + cola reciente]\n"
                f"{_summ}\n\n{snap}\n\n"
                "El historial original sigue en el JSONL; el código está en disco. "
                "No regeneres archivos enteros: edita por partes."
            )}]
            + tail
        )
        after = _call_token_estimate(run, system_prompt, prompt)
        still_over = after > int(ev.get("thresh") or 0)
        if still_over:
            _hard_trim_messages(run)
            after = _call_token_estimate(run, system_prompt, prompt)
            still_over = after > int(ev.get("thresh") or 0)
            if still_over:
                run._compact_blocked = True
        try:
            append_checkpoint(
                run, kind="compact", done="compactación de contexto (preflight)",
                decisions=_summ[:800], pending="cola reciente intacta",
                next_action="llamar al modelo" if not still_over else "aviso: sigue demasiado grande",
                extra={"est_before": ev.get("est"), "est_after": after, "still_over": still_over,
                       "tail_n": tail_n, "num_ctx": ev.get("num_ctx")},
            )
        except Exception:
            pass
        run._last_compact_eval = {**ev, "est_after": after, "still_over": still_over, "compacted": True}
        return _summ
    except Exception as exc:  # noqa: BLE001
        print(f"[ottercode] compact skipped: {type(exc).__name__}: {exc}", flush=True)
        return None


def _thermal_ease(run: Any) -> Optional[str]:
    """L6: si VRAM/temp altas de forma sostenida, baja num_ctx y espacia."""
    try:
        from backend.config import VRAM_TOTAL_BYTES, _ollama_httpx
        used = 0
        resp = _ollama_httpx.get("/api/ps")
        for m in (resp.json().get("models") or []):
            used += int(m.get("size_vram") or 0)
        ratio = used / max(int(VRAM_TOTAL_BYTES or 1), 1)
    except Exception:
        ratio = 0.0
    temp = 0
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=temperature.gpu", "--format=csv,noheader,nounits"],
            timeout=2, text=True,
        ).strip()
        temp = int(out.splitlines()[0])
    except Exception:
        temp = 0
    hot = ratio >= 0.92 or temp >= 80
    now = time.time()
    if hot:
        since = getattr(run, "_high_vram_since", None)
        if since is None:
            run._high_vram_since = now
            return None
        hold = float(os.environ.get("OTTERCODE_THERMAL_HOLD_S", "90"))
        if now - since < hold:
            return None
        if getattr(run, "_thermal_eased", False):
            time.sleep(float(os.environ.get("OTTERCODE_THERMAL_SLEEP", "2")))
            return None
        orig = getattr(run, "num_ctx", None) or NUM_CTX_DEFAULT
        run.num_ctx = max(2048, int(int(orig) * 0.7))
        run._thermal_eased = True
        msg = (f"🌡️ Carga sostenida (VRAM {ratio:.0%} temp={temp}°C): "
               f"num_ctx {orig}→{run.num_ctx}, llamadas más espaciadas.")
        append_checkpoint(run, kind="thermal", done=msg, pending="GPU al límite",
                          next_action="seguir con ctx reducido")
        time.sleep(float(os.environ.get("OTTERCODE_THERMAL_SLEEP", "2")))
        return msg
    run._high_vram_since = None
    return None


_THINK_RE = re.compile(r"<think>[\s\S]*?</think>|<think>[\s\S]*$", re.IGNORECASE)
_THINK_CLOSE_RE = re.compile(r"</think>", re.IGNORECASE)


def _strip_think(text: str) -> str:
    """Quita el razonamiento del modelo y deja solo el contenido útil.

    Tres casos reales (v4.2):
    1. Pares explícitos: <think>…</think> → se eliminan.
    2. THINK AUTO-ABIERTO por la plantilla (qwen3 en Ollama): el modelo nace
       dentro de <think> y su primera emisión es un </think> HUÉRFANO. Todo
       lo previo al ÚLTIMO </think> es razonamiento → se descarta.
    3. Think sin cerrar por truncado: <think>…$ → se elimina.

    El JSON de skill se busca en el texto limpio: el razonamiento gigante
    nunca debe llegar al parser ni al chat."""
    t = text or ""
    if not t:
        return ""
    had_opener = "<think>" in t.lower()
    t = _THINK_RE.sub("", t)                     # caso 1 + caso 3
    closes = _THINK_CLOSE_RE.split(t)
    if len(closes) > 1 and not had_opener:       # caso 2: think auto-abierto
        # conserva TODOS los segmentos de contenido (p. ej. dos skills en
        # pasos distintos concatenados), descarta todo el razonamiento
        t = "\n".join(seg for seg in closes[1:] if seg.strip())
    t = _THINK_CLOSE_RE.sub("", t)               # restos de cierres huérfanos
    return t.strip()


def _salvage_cut_json(text: str) -> Optional[Tuple[str, str]]:
    """Extrae (filepath, contenido) de un JSON de skill CORTADO a mitad.

    Cuando el modelo intenta write_file con 40k chars, num_predict mata el
    JSON antes del cierre. Pedir reemisión es quemar ~12k tokens por intento
    (y el modelo vuelve a volcarlo entero): el contenido está ÍNTEGRO hasta
    el punto de corte, así que se rescata y se escribe directamente."""
    t = text or ""
    if '"tool"' not in t:
        return None
    m_fp = re.search(r'"filepath"\s*:\s*"([^"]{1,200})"', t)
    m_ct = re.search(r'"content"\s*:\s*"(.*)', t, re.S)
    if not m_fp or not m_ct:
        return None
    raw = m_ct.group(1).rstrip()
    if raw.endswith('"}'):
        raw = raw[:-2]
    elif raw.endswith('"'):
        raw = raw[:-1]
    if len(raw) < 500:
        return None
    out = (raw.replace("\\\\", "\x00")
              .replace("\\n", "\n").replace("\\t", "\t")
              .replace('\\"', '"').replace("\x00", "\\"))
    nombre = m_fp.group(1).strip()
    if not re.fullmatch(r"[\w./-]+\.(?:html?|css|js|mjs|py|json|md|csv|txt|svg)", nombre):
        return None
    return nombre, out


def _salvage_before_finalize(text: str) -> Optional[Tuple[str, str]]:
    """🛟 v4.9 · FIX A: rescata un write/append que el extractor SALTÓ.

    Cuando el modelo emite write/append + finalizar juntos y el JSON del
    write tiene un escape inválido, extract_tool_call salta al finalizar
    (válido) y el trabajo se pierde. Aquí se corta el texto en el bloque
    finalizar y se aplica el salvamento normal sobre la parte del write."""
    i = (text or "").find('"finalizar"')
    if i <= 0:
        return None
    return _salvage_cut_json(text[:i])


def _write_size_guard(run: Any, nombre: str, contenido: str) -> bool:
    """🛟 v4.9 · FIX C: NO sobrescribir un archivo bueno con uno menor.

    Si el archivo ya existe y el contenido nuevo es <60% del existente,
    alguien está regenerando por encima de una versión más completa → se
    conserva la previa. Devuelve True si la escritura está permitida."""
    try:
        previa = (run.workdir / nombre).read_text(encoding="utf-8")
        if len(previa) > 500 and len(contenido) < len(previa) * 0.6:
            return False
    except (OSError, ValueError):
        pass
    return True


def _invalid_json_feedback(text: str) -> str:
    """Feedback ante un intento de skill no parseable.

    Si el texto era enorme, casi seguro fue CORTADO por num_predict:
    pedir reemisión completa es un bucle infinito → se exige escritura
    por partes (write_file + append_file)."""
    if len(text or "") > 8000:
        return (
            "ERROR: tu JSON fue CORTADO por exceder la longitud máxima de "
            "generación. NO lo repitas entero. Divide el trabajo: escribe el "
            "archivo en PARTES de ≤150 líneas usando write_file para la primera "
            "y append_file para las siguientes. Una sola parte por llamada."
        )
    return (
        "ERROR: el JSON de tu última skill NO fue parseable (JSON inválido). "
        "Reemite el MISMO JSON corregido: comillas balanceadas, sin comas "
        "finales, y escapes dentro de 'content' (\\\" para comillas dobles, "
        "\\n para saltos de línea)."
    )


def run_simple_agent(
    run: OtterRun, agent_id: str, system_prompt: str, prompt: str, iteration: int = 1
) -> Iterator[str]:
    """Turno sin skills (Arquitecto): flush → stream → texto completo."""
    meta = _agent_meta(get_agent(agent_id))
    # FASE 3 · Si el perfil tiene system_override, reemplaza el prompt base
    profile_override = _otter_profiles._ACTIVE_PROFILE.get("system_override", "")
    if profile_override:
        system_prompt = profile_override
    # FASE 2 · SOUL al inicio, USER al final del system prompt
    if _otter_cfg._SOUL_CONTENT:
        system_prompt = _otter_cfg._SOUL_CONTENT + "\n\n" + system_prompt
    if _otter_cfg._USER_CONTENT:
        system_prompt = system_prompt + "\n\n" + _otter_cfg._USER_CONTENT
    if run.hacker:
        system_prompt = system_prompt + HACKER_SUFFIX
    _activity_set(agent=agent_id, agent_nombre=meta.get("nombre", ""),
                  agent_icon=meta.get("icon", ""), iteration=iteration,
                  last_tool=None, last_tool_ok=None)
    yield sse(SseEvent.agent_start, {"agent": agent_id, "iteration": iteration, **meta})
    flush = flush_vram(run.model)  # ⚓ REGLA DE ORO
    yield sse(SseEvent.vram_flush, {"agent": agent_id, "iteration": iteration, **flush})
    if not flush["ok"]:
        yield sse(SseEvent.system, {
            "text": f"⚠️ VRAM flush no confirmado ({flush.get('error', flush.get('detail'))})"
        })
    _eval = _preflight_eval(run, system_prompt, prompt)
    yield sse(SseEvent.system, {
        "text": (f"🗜️ preflight ANTES de llamar al modelo: est={_eval['est']} tok · "
                 f"umbral={_eval['thresh']} (num_ctx={_eval['num_ctx']} − reserva="
                 f"{_eval['reserved']}) · compactar={'sí' if _eval['over'] else 'no'}")
    })
    _maybe_compact_messages(run, agent_id, system_prompt, prompt=prompt)
    try:
        text, _stats = yield from stream_llm(run, agent_id, system_prompt, prompt)  # type: ignore[misc]
    except ContextOverflow:
        _maybe_compact_messages(run, agent_id, system_prompt, prompt=prompt, force=True)
        text, _stats = yield from stream_llm(run, agent_id, system_prompt, prompt)  # type: ignore[misc]
    run.transcript.append({"kind": "agent", "agent": agent_id, "iteration": iteration, "text": text})
    yield sse(SseEvent.agent_end, {"agent": agent_id, "iteration": iteration})
    try:
        harvest_memory(run, agent_id, text)
    except Exception:
        pass
    return text  # type: ignore[return-value]


def _save_partial_on_abort(run: Any, agent_id: str, iteration: int) -> None:
    """v4.4 · Aborto con generación en curso: conserva el texto parcial.

    Lo generado hasta el momento entra al transcript (limpio de <think>) con
    una marca final, para que abortar a mitad de una generación larga no
    suponga perder TODO lo que el modelo llevaba escrito."""
    parcial = getattr(run, "_partial_text", "")
    if not parcial:
        return
    run.transcript.append(
        {"kind": "agent", "agent": agent_id, "iteration": iteration,
         "text": (_strip_think(parcial)
                  + "\n\n⏹ (generación abortada por el usuario — texto parcial conservado)")}
    )


def _run_native_tool(run: Any, agent_id: str, iteration: int, executor: Any, call: Dict[str, Any]) -> Iterator[Dict[str, Any]]:
    """Ejecuta una herramienta de Function Calling nativa."""
    func = call.get("function", {})
    name = str(func.get("name") or "")
    args_raw = func.get("arguments", "{}")
    if isinstance(args_raw, dict):
        args = dict(args_raw)
    else:
        try:
            args = json.loads(args_raw or "{}")
        except json.JSONDecodeError:
            args = {}
    if not isinstance(args, dict):
        args = {}

    yield {"kind": "start", "name": name, "args": args}

    if not name:
        yield {"kind": "result", "name": name, "ok": False, "output": "sin herramienta"}
        return {"ok": False, "output": "sin herramienta"}

    args = alias_args(name, args)
    if name == "write_file":
        fp = str(args.get("filepath") or args.get("path") or "").strip()
        try:
            cand = (run.workdir / fp) if fp else None
            if cand is not None and cand.is_file() and cand.stat().st_size > 80:
                msg = (
                    f"ACCESO DENEGADO: «{fp}» ya existe. Usa edit_file o append_file; "
                    "write_file lo regeneraría desde cero."
                )
                yield {"kind": "result", "name": name, "ok": False, "output": msg}
                return {"ok": False, "output": msg}
        except OSError:
            pass
    if _needs_permission(run, name, args):
        perm_id = str(uuid.uuid4())
        PENDING_PERMISSIONS[perm_id] = threading.Event()
        yield {"kind": "perm", "id": perm_id, "tool": name, "args": args,
               "title": tools.tool_title(name, args)}
        PENDING_PERMISSIONS[perm_id].wait(timeout=300)
        perm_ok = PERMISSION_RESPONSES.pop(perm_id, False)
        PENDING_PERMISSIONS.pop(perm_id, None)
        if not perm_ok:
            msg = f"ACCESO DENEGADO: usuario denegó '{name}'"
            yield {"kind": "result", "name": name, "ok": False, "output": msg}
            return {"ok": False, "output": msg}
    res = executor.dispatch(name, args)

    yield {"kind": "result", "name": name, "ok": res.get("ok", False), "output": res.get("output", "")}

    return res


def run_agent_turn(run: OtterRun, agent_id: str, iteration: int, prompt: str,
                   flush: bool = True,
                   max_steps: int = MAX_TOOL_STEPS,
                   only_tools: Optional[set] = None) -> Iterator[str]:
    """Turno con skills: flush → bucle (LLM → skill → resultado → LLM …) → texto.

    Funciona idéntico para agentes core y DINÁMICOS: la lista de skills, el
    system prompt y el modo solo-lectura salen del objeto `Agent`. El VRAM
    flush (Regla de Oro) se ejecuta en cada paso de testigo, sin excepción.
    """
    agent = get_agent(agent_id)
    meta = _agent_meta(agent)
    allowed = [t for t in agent.tools_disponibles if skill_enabled(t)]
    # v4.9 · turnos correctivos pueden RESTRINGIR el arsenal (p. ej. solo
    # append_file en la completación: imposible volver a volcar en un write)
    if only_tools:
        allowed = [t for t in allowed if t in only_tools]
    # Regenera el protocolo de skills según los toggles activos (core agents)
    base = _CORE_BASE_PROMPTS.get(agent.id)
    # FASE 3 · Si el perfil activo tiene system_override, usarlo como base
    profile_override = _otter_profiles._ACTIVE_PROFILE.get("system_override", "")
    if profile_override:
        system_prompt = profile_override + "\n\n" + tool_protocol(allowed)
    else:
        system_prompt = (
            base + tool_protocol(allowed) if base else agent.system_prompt
        )
    if run.hacker:
        system_prompt = system_prompt + HACKER_SUFFIX
    # v4.1 · /ultrareview: el Revisor recibe el protocolo exhaustivo
    if getattr(run, "ultra_review", False) and agent_id == "reviewer":
        system_prompt = system_prompt + ULTRAREVIEW_SUFFIX
    # FASE 4 · /sys: inyección temporal de regla al contexto
    if getattr(run, "system_inject", ""):
        system_prompt = system_prompt + "\n\n" + run.system_inject
    # FASE 2 · SOUL al inicio, USER al final del system prompt
    if _otter_cfg._SOUL_CONTENT:
        system_prompt = _otter_cfg._SOUL_CONTENT + "\n\n" + system_prompt
    if _otter_cfg._USER_CONTENT:
        system_prompt = system_prompt + "\n\n" + _otter_cfg._USER_CONTENT
    
    # INYECCIÓN DE MEMORIA ATÓMICA
    mem = get_memory()
    if mem:
        system_prompt = system_prompt + "\n\n# 🧠 MEMORIA APRENDIDA (CONTEXTO A LARGO PLAZO)\n" + mem
    from backend.md_skills import active_skill_prompt
    _sk = active_skill_prompt(getattr(run, "forced_skill", "") or "")
    if _sk:
        system_prompt = system_prompt + "\n\n# SKILLS ACTIVAS (markdown)\n" + _sk
    
    _activity_set(agent=agent_id, agent_nombre=meta.get("nombre", ""),
                  agent_icon=meta.get("icon", ""), iteration=iteration,
                  last_tool=None, last_tool_ok=None)
    executor = (
        tools.ToolExecutor(run.workdir, readonly=True) if agent.readonly else run.executor
    )

    _set_step(run, f"agent_turn:{agent_id}:iter{iteration}")
    yield sse(SseEvent.agent_start, {"agent": agent_id, "iteration": iteration, **meta})
    # ⚓ REGLA DE ORO (v4.8): los turnos de CONTINUACIÓN del mismo agente
    # (correctivos/de completación) llaman con flush=False — el modelo YA está
    # cargado y recargarlo provocaba timeouts de 300 s tras un rescate.
    if flush:
        flush_info = flush_vram(run.model)
        yield sse(SseEvent.vram_flush, {"agent": agent_id, "iteration": iteration, **flush_info})

    # v3.4 · registro de skills usadas EN ESTE TURNO (para obligaciones:
    # developer sin archivos creados, researcher que finaliza sin explorar)
    run._turn_tools = set()
    run._turn_salvaged_truncated = False   # v5.1 · salvamento cortado este turno
    run._last_salvaged_file = ""           # v5.1 · archivo salvado (para completar)

    # v6.0 · Fase 1: el prompt del turno entra en la COLA DE CONTEXTO con
    # roles; las respuestas y resultados de skills se van encadenando ahí.
    run.messages.append({"role": "user", "content": prompt})
    steps = 0
    last_text = ""
    while steps < max_steps:
        if run.aborted:
            raise AbortRequested()
        _th = _thermal_ease(run)
        if _th:
            yield sse(SseEvent.system, {"text": _th})
        _eval = _preflight_eval(run, system_prompt, prompt)
        if _eval.get("over"):
            yield sse(SseEvent.system, {
                "text": (f"🗜️ preflight: est={_eval['est']} tok · umbral={_eval['thresh']} "
                         f"(num_ctx={_eval['num_ctx']} − reserva={_eval['reserved']}) → compactar")
            })
        try:
            _summ = _maybe_compact_messages(run, agent_id, system_prompt, prompt=prompt)
        except Exception as _cx:  # noqa: BLE001
            _summ = None
            print(f"[ottercode] compact swallow: {_cx}", flush=True)
        if _summ:
            _after = getattr(run, "_last_compact_eval", {}) or {}
            if _after.get("still_over"):
                yield sse(SseEvent.system, {
                    "text": "⚠️ el contexto sigue siendo demasiado grande tras compactar. "
                            "Esperando acción explícita (Compactar ahora) — no se reintenta en bucle."
                })
            else:
                yield sse(SseEvent.system, {
                    "text": f"🗜️ Compactación preflight: {len(_summ)} chars de resumen + cola reciente."
                })
            run.transcript.append({"kind": "system", "text": f"🗜️ contexto compactado ({len(_summ)} chars)."})
        steps += 1
        # v4.4 · cada generación arranca con el parcial a cero
        try:
            run._partial_text = ""
        except AttributeError:
            pass
        try:
            last_text, _stats = yield from stream_llm(run, agent_id, system_prompt, prompt)  # type: ignore[misc]
        except ContextOverflow:
            n_ov = int(getattr(run, "_overflow_n", 0) or 0) + 1
            run._overflow_n = n_ov
            yield sse(SseEvent.system, {
                "text": f"⚠️ Contexto GPU lleno ({n_ov}/3). Checkpoint: resumen + archivos en disco…"
            })
            _hard_trim_messages(run)
            prompt = (prompt or "")[:2500]
            if n_ov >= 3:
                yield sse(SseEvent.system, {
                    "text": "🗜️ Contexto al límite tras 3 recortes. Sigue en este chat; "
                            "el código está en el workspace. Pulsa Compactar ahora si hace falta."
                })
                last_text, _stats = "", {}
                break
            try:
                last_text, _stats = yield from stream_llm(run, agent_id, system_prompt, prompt)  # type: ignore[misc]
            except ContextOverflow:
                continue
        except AbortRequested:
            # v4.4 · aborto en plena generación: lo generado se conserva
            if getattr(run, "_partial_text", ""):
                yield sse(SseEvent.system, {
                    "text": "⏹ Texto parcial conservado en el transcript."
                })
            _save_partial_on_abort(run, agent_id, iteration)
            raise
        # v3.4 · el parsing y el historial usan el texto SIN bloques <think>
        parse_text = _strip_think(last_text)
        # FASE 5 · Native Function Calling
        if any(m in run.model for m in TOOL_CAPABLE_MODELS):
            _msg = _stats.get("message")
            native_calls = (_msg or {}).get("tool_calls", [])
            if native_calls:
                run.messages.append(_stats["message"])
                for call in native_calls:
                    for event in _run_native_tool(run, agent_id, iteration, executor, call):
                        if event["kind"] == "perm":
                            yield sse(SseEvent.perm_request, {
                                "id": event["id"], "tool": event["tool"],
                                "args": event.get("args") or {},
                                "title": event.get("title") or event["tool"],
                            })
                        elif event["kind"] == "start":
                            yield sse(SseEvent.tool_call, {"agent": agent_id, "iteration": iteration,
                                      "tool": event["name"], "args": event["args"], "title": f"🛠️ {event['name']}"})
                        elif event["kind"] == "result":
                            yield sse(SseEvent.tool_result, {"tool": event["name"], "ok": event["ok"], "output": event["output"]})
                            run.messages.append({
                                "role": "tool",
                                "tool_call_id": call.get("id"),
                                "content": str(event.get("output", ""))
                            })
                            if event.get("ok") and event["name"] in _WRITE_TOOLS:
                                run._files_ever_written = True
                            if not isinstance(getattr(run, "_turn_tools", None), set):
                                run._turn_tools = set()
                            run._turn_tools.add(event["name"])
                # Native FC ya ejecutó las tools: no reparsear JSON/Hermes (doble dispatch).
                continue

        run.transcript.append(
            {"kind": "agent", "agent": agent_id, "iteration": iteration, "text": parse_text}
        )
        run.messages.append({"role": "assistant", "content": parse_text})

        call = extract_tool_call(parse_text)
        if call is None:
            # Robustez: el agente intentó una skill con JSON inválido →
            # feedback adaptado: si era gigante fue CORTADO → partes.
            if _looks_like_tool_attempt(parse_text) and steps < max_steps:
                huge = len(parse_text) > 8000
                # 🛟 v4.8 · SALVAJE DE JSON CORTADO: s         # en vez de pedir reemisión (que re-vuelca todo y re-se corta).
                salv = _salvage_cut_json(parse_text)   # v5.0.1 · sin umbral: el salvamento ya valida ≥500
                if salv and not agent.readonly:
                    s_nombre, s_contenido = salv
                    if not _write_size_guard(run, s_nombre, s_contenido):
                        run._files_ever_written = True
                        run.transcript.append(
                            {"kind": "system",
                             "text": (f"🛟 Se conserva la versión previa de "
                                      f"{s_nombre} (más completa): nada se "
                                      f"sobrescribe.")})
                        break
                    s_res = executor.dispatch(
                        "write_file", {"filepath": s_nombre, "content": s_contenido})
                    if s_res.get("ok"):
                        s_id = str(uuid.uuid4())
                        yield sse(SseEvent.tool_call, {
                            "id": s_id, "agent": agent_id, "iteration": iteration,
                            "tool": "write_file", "args": {"filepath": s_nombre},
                            "title": f"🛟 Salvado: {s_nombre}",
                        })
                        s_out = (f"OK: {len(s_contenido)} caracteres escritos en "
                                 f"{s_nombre} (salvado de un JSON cortado)")
                        yield sse(SseEvent.tool_result, {
                            "id": s_id, "tool": "write_file", "ok": True,
                            "output": s_out, "ms": 0,
                        })
                        _activity_set(last_tool=f"🛟 Salvado: {s_nombre}", last_tool_ok=True)
                        run.transcript.append(
                            {"kind": "tool", "tool": "write_file", "agent": agent_id,
                             "iteration": iteration, "args": {"filepath": s_nombre},
                             "ok": True, "output": s_out})
                        run.transcript.append(
                            {"kind": "system",
                             "text": (f"🛟 JSON cortado salvado: contenido escrito en "
                                      f"{s_nombre} sin reemisión.")})
                        if not isinstance(getattr(run, "_turn_tools", None), set):
                            run._turn_tools = set()
                        run._turn_tools.add("write_file")
                        run._files_ever_written = True
                        # v5.1 · el contenido salvado SIEMPRE está cortado
                        # (num_predict mató el JSON): hay que completarlo.
                        run._last_salvaged_file = s_nombre
                        try:
                            _tail = (run.workdir / s_nombre).read_text(encoding="utf-8")[-900:]
                        except OSError:
                            _tail = ""
                        run.messages.append({"role": "user", "content": (
                            f"OK: {s_nombre} está en disco PERO incompleto. "
                            "PROHIBIDO write_file (machaca lo escrito). "
                            f"Siguiente skill: append_file filepath=\"{s_nombre}\" "
                            "con SOLO lo que falta (≤150 líneas). Cola actual:\n"
                            f"{_tail}"
                        )})
                        continue
                fb = _invalid_json_feedback(parse_text)
                yield sse(SseEvent.system, {
                    "text": ("⚠️ JSON cortado por longitud: pidiendo escritura POR PARTES."
                             if huge else
                             "⚠️ JSON de skill inválido: pidiendo reemisión al agente.")
                })
                run.messages.append({"role": "user", "content": fb})
                continue
            break

        raw_tool = str(call["tool"])
        tool_name = tools.resolve_name(raw_tool)
        args = alias_args(tool_name, call.get("arguments") or {})
        if not isinstance(args, dict):
            args = {}
        call_id = str(uuid.uuid4())
        title = tools.tool_title(tool_name, args)

        if tool_name == "finalizar":
            run._turn_tools.add("finalizar")
            # 🛟 v4.9 · FIX A: si esta MISMA generación llevaba un write/append
            # con contenido grande cuyo JSON el extractor saltó (un escape
            # inválido), se rescata ANTES de cerrar: el trabajo no se pierde.
            salv_pre = (_salvage_before_finalize(parse_text)
                        if len(parse_text) > 800 else None)
            if salv_pre and not agent.readonly:
                sp_nombre, sp_contenido = salv_pre
                if _write_size_guard(run, sp_nombre, sp_contenido):
                    sp_res = executor.dispatch(
                        "write_file",
                        {"filepath": sp_nombre, "content": sp_contenido})
                    if sp_res.get("ok"):
                        run._files_ever_written = True
                        run._turn_tools.add("write_file")
                        sp_id = str(uuid.uuid4())
                        sp_out = (f"OK: {len(sp_contenido)} caracteres escritos "
                                  f"en {sp_nombre} (salvado antes del finalizar)")
                        yield sse(SseEvent.tool_call, {
                            "id": sp_id, "agent": agent_id,
                            "iteration": iteration, "tool": "write_file",
                            "args": {"filepath": sp_nombre},
                            "title": f"🛟 Salvado: {sp_nombre}",
                        })
                        yield sse(SseEvent.tool_result, {
                            "id": sp_id, "tool": "write_file", "ok": True,
                            "output": sp_out, "ms": 0,
                        })
                        _activity_set(last_tool=f"🛟 Salvado: {sp_nombre}",
                                      last_tool_ok=True)
                        run.transcript.append(
                            {"kind": "tool", "tool": "write_file",
                             "agent": agent_id, "iteration": iteration,
                             "args": {"filepath": sp_nombre}, "ok": True,
                             "output": sp_out})
            yield sse(SseEvent.tool_call, {
                "id": call_id, "agent": agent_id, "iteration": iteration,
                "tool": tool_name, "args": args, "title": title,
            })
            resumen = str(args.get("resumen", ""))
            yield sse(SseEvent.tool_result, {
                "id": call_id, "tool": tool_name, "ok": True,
                "output": resumen or "finalizado", "ms": 0,
            })
            run.transcript.append(
                {"kind": "tool", "tool": tool_name, "agent": agent_id, "iteration": iteration,
                 "args": args, "ok": True, "output": resumen or "finalizado"}
            )
            idle_n = int(getattr(run, "_todo_idle", 0) or 0)
            pending_todos = _pending_todo_items(run)
            if pending_todos and idle_n < _TODO_IDLE_MAX:
                run._todo_idle = idle_n + 1
                nleft = _TODO_IDLE_MAX - run._todo_idle
                reminder = (
                    "RECORDATORIO AUTOMÁTICO: hay ítems de Todo pendientes o en curso:\n"
                    + "\n".join(f"- [{t.get('status')}] {t.get('content')}" for t in pending_todos[:12])
                    + "\nContinúa el siguiente ítem pendiente. No finalices todavía."
                )
                yield sse(SseEvent.system, {
                    "text": f"📋 Todo incompleto: recordatorio {run._todo_idle}/{_TODO_IDLE_MAX}."
                })
                run.messages.append({"role": "user", "content": reminder})
                continue
            if pending_todos and idle_n >= _TODO_IDLE_MAX:
                yield sse(SseEvent.system, {
                    "text": "🛑 Todo a medias tras varios recordatorios sin progreso. "
                            "Intervención del usuario — no se insiste más."
                })
            break

        if tool_name not in allowed:
            # v4.2 · agente solo-lectura intentando escribir: feedback que
            # CORTA el intento (el genérico invitaba a reintentar con otro
            # JSON gigante). Le recuerda su rol y le manda a finalizar.
            if agent.readonly and tool_name in _WRITE_TOOLS:
                error_msg = (
                    f"ACCESO DENEGADO: eres un agente SOLO-LECTURA y '{tool_name}' "
                    "escribe archivos. NO insistas ni generes código: tu trabajo "
                    "es informar. Emite tu informe/veredicto y cierra con "
                    '{"tool": "finalizar", "arguments": {"resumen": "..."}}.'
                )
            else:
                error_msg = (
                    f"ACCESO DENEGADO: '{raw_tool}' no está en tu lista de skills. "
                    f"Disponibles para ti: {', '.join(allowed) + ', finalizar'}."
                )
            yield sse(SseEvent.tool_call, {
                "id": call_id, "agent": agent_id, "iteration": iteration,
                "tool": tool_name, "args": args, "title": title,
            })
            yield sse(SseEvent.tool_result, {
                "id": call_id, "tool": tool_name, "ok": False, "output": error_msg, "ms": 0,
            })
            run.transcript.append(
                {"kind": "tool", "tool": tool_name, "agent": agent_id, "iteration": iteration,
                 "args": args, "ok": False, "output": error_msg}
            )
            run.messages.append({"role": "user", "content": error_msg})
            continue

        if _needs_permission(run, tool_name, args):
            perm_id = str(uuid.uuid4())
            PENDING_PERMISSIONS[perm_id] = threading.Event()
            yield sse(SseEvent.perm_request, {
                "id": perm_id, "tool": tool_name, "args": args, "title": title
            })
            PENDING_PERMISSIONS[perm_id].wait(timeout=300)
            perm_ok = PERMISSION_RESPONSES.pop(perm_id, False)
            PENDING_PERMISSIONS.pop(perm_id, None)
            try:
                append_checkpoint(run, kind="perm", done=f"{tool_name} {'ok' if perm_ok else 'deny'}",
                                  decisions=title, pending="", next_action="continuar")
            except Exception:
                pass
            if not perm_ok:
                error_msg = f"ACCESO DENEGADO: usuario denegó '{tool_name}'"
                yield sse(SseEvent.tool_call, {
                    "id": call_id, "agent": agent_id, "iteration": iteration,
                    "tool": tool_name, "args": args, "title": title,
                })
                yield sse(SseEvent.tool_result, {"id": call_id, "tool": tool_name, "ok": False, "output": error_msg, "ms": 0})
                run.messages.append({"role": "user", "content": error_msg})
                continue

        yield sse(SseEvent.tool_call, {
            "id": call_id, "agent": agent_id, "iteration": iteration,
            "tool": tool_name, "args": args, "title": title,
        })
        _activity_set(last_tool=title, last_tool_ok=None)
        result = executor.dispatch(tool_name, args)
        if result.get("ok") and tool_name == "todo_write":
            run._todo_idle = 0
            yield sse(SseEvent.system, {"text": "📋 Todo actualizado (persistido en disco)."})
        if result.get("ok") and tool_name in ("write_file", "append_file", "edit_file"):
            fp = str(args.get("filepath") or args.get("path") or "")
            out = str(result.get("output") or "")
            if fp and "```diff" not in out:
                result["output"] = out + f"\n```diff\n*** {fp}\n+ escrito/modificado\n```"
        if result.get("ok") and tool_name in ("write_file", "append_file", "edit_file"):
            try:
                from backend.hooks import post_code_generated, remember_file_hook
                fp = str(args.get("filepath") or args.get("path") or "")
                body = ""
                try:
                    body = (run.workdir / fp).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    body = str(args.get("content") or "")
                lint = post_code_generated(fp, body)
                remember_file_hook(run.workdir, lint)
                append_checkpoint(
                    run, kind="verify", done=f"verificación {fp}",
                    decisions=("ok" if lint.get("ok") else "; ".join(lint.get("issues") or [])),
                    pending="" if lint.get("ok") else "corregir sintaxis",
                    next_action="siguiente archivo o revisor",
                    extra={"path": fp, "ok": lint.get("ok")},
                )
                if not lint.get("ok"):
                    yield sse(SseEvent.system, {
                        "text": "⚠️ hook post_code_generated: " + "; ".join(lint.get("issues") or []),
                    })
                    if note_stall(run, fp, "; ".join(lint.get("issues") or [])):
                        yield sse(SseEvent.system, {
                            "text": f"🛑 Circuit breaker: {fp} bloqueado tras 3 fallos iguales. "
                                    "Intervención humana."
                        })
                        break
            except Exception:
                pass
        yield sse(SseEvent.tool_result, {
            "id": call_id, "tool": tool_name, "ok": result["ok"],
            "output": result["output"], "ms": result["ms"],
        })
        _activity_set(last_tool_ok=result["ok"])
        if result.get("ok") and tool_name in _WRITE_TOOLS:
            run._files_ever_written = True   # v4.9 · FIX B
            append_checkpoint(
                run, kind="file", done=f"escrito {args.get('filepath') or args.get('path')}",
                decisions=tool_name, pending="más archivos o revisión",
                next_action="continuar unidad de trabajo",
            )
        if not result.get("ok"):
            err = str(result.get("output") or "error")
            if note_stall(run, tool_name, err):
                yield sse(SseEvent.system, {
                    "text": f"🛑 Circuit breaker: '{tool_name}' falló 3 veces igual. "
                            "Subtarea bloqueada; se pasa a lo siguiente."
                })
                break
        run.transcript.append(
            {"kind": "tool", "tool": tool_name, "agent": agent_id, "iteration": iteration,
             "args": args, "ok": result["ok"], "output": result["output"]}
        )
        run._turn_tools.add(tool_name)
        run.messages.append(
            {"role": "user", "content": format_tool_result(tool_name, result)})
    else:
        yield sse(SseEvent.system, {
            "text": f"⚠️ Límite de skills por turno alcanzado ({max_steps}); el turno termina."
        })

    yield sse(SseEvent.agent_end, {"agent": agent_id, "iteration": iteration, "steps": steps})
    cleaned = _strip_think(last_text)
    try:
        harvest_memory(run, agent_id, cleaned)
    except Exception:
        pass
    return cleaned  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# El relevo completo (AgentLoop) + meta-orquestación
# ---------------------------------------------------------------------------

def _pending_todo_items(run: Any) -> List[Dict[str, Any]]:
    try:
        p = Path(run.workdir) / ".otter_todo.json"
        if not p.is_file():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    out: List[Dict[str, Any]] = []
    for it in data:
        if not isinstance(it, dict):
            continue
        st = str(it.get("status") or "pending").lower()
        if st in ("pending", "pendiente", "in_progress", "en curso", "en_curso"):
            out.append(it)
    return out


def compact_run_now(run: Any, agent_id: str = "agent") -> Dict[str, Any]:
    """T4: compactación manual (siempre disponible)."""
    run._compact_blocked = False
    sys_p = ""
    try:
        sys_p = str((run.messages or [{}])[0].get("content") or "")
    except Exception:
        sys_p = ""
    summ = _maybe_compact_messages(run, agent_id, sys_p, force=True)
    if not summ:
        _hard_trim_messages(run)
        summ = "(checkpoint GPU: historial recortado; archivos en disco)"
    ev = getattr(run, "_last_compact_eval", {}) or {}
    return {"ok": True, "summary": (summ or "")[:800], **ev}


_WRITE_TOOLS = {"write_file", "append_file", "edit_file", "mkdir", "python_exec"}
_EXPLORE_TOOLS = {"tree", "list_dir", "read_file", "grep_search", "glob_files",
                  "web_search", "web_fetch", "wikipedia_search",
                  "execute_bash", "git_status"}


def _forced_step_prompt(agent_id: str, used: set) -> Optional[str]:
    """v3.4 · OBLIGACIONES POR AGENTE: nadie 'finaliza' sin hacer su trabajo.

    - Developer que termina sin crear/modificar NI UN archivo → paso forzado.
    - Researcher que finaliza sin ejecutar ninguna skill de exploración → paso
      forzado. Devuelve el prompt correctivo o None si el agente cumplió."""
    real = set(used or []) - {"finalizar"}
    if agent_id == "developer" and not (real & _WRITE_TOOLS):
        return (
            "CORRECCIÓN OBLIGATORIA: terminaste tu turno SIN crear ni modificar "
            "NI UN solo archivo (cero write_file/append_file). Prohibido dar "
            "explicaciones: llama AHORA a write_file con la PRIMERA parte del "
            "archivo principal (≤150 líneas) y continúa con append_file hasta "
            "completarlo. Solo después puedes finalizar."
        )
    if agent_id == "researcher" and not (real & _EXPLORE_TOOLS):
        return (
            "CORRECCIÓN OBLIGATORIA: finalizaste SIN explorar el workspace "
            "(cero tree/read_file/list_dir). Ejecuta tree y read_file sobre lo "
            "que exista ANTES de emitir cualquier informe o volver a finalizar."
        )
    return None


_CODE_FENCE_RE = re.compile(r"```[\s\S]*?```|```[\s\S]*$", re.MULTILINE)
_CODE_SMELL_RE = re.compile(
    r"<!DOCTYPE|<html[\s>]|</div>|def\s+\w+\s*\(|function\s+\w+\s*\(|"
    r"console\.log|import\s+\w+\s+from|SELECT\s+.+\s+FROM\s+\w+", re.IGNORECASE)


def _plan_has_code(text: str) -> bool:
    """Detecta si el plan del Arquitecto contiene código (prohibido).

    El ÚNICO fence legítimo es el bloque ```json {"inject_agents": …} de la
    meta-orquestación: se excluye antes de analizar. Los planes legítimos son
    listas en lenguaje natural."""
    t = text or ""
    t = re.sub(r"```json\s*\{[^`]*inject_agents[^`]*\}\s*```", "", t)
    if _CODE_FENCE_RE.search(t):
        return True
    return len(_CODE_SMELL_RE.findall(t)) >= 3


# ---------------------------------------------------------------------------
# 🛟 v4.5 · RED DE RESCATE: código soltado como TEXTO sin crear archivos
# ---------------------------------------------------------------------------

_RESCUE_FENCE_RE = re.compile(r"```([\w+-]*)\n?([\s\S]*?)```")
# v4.6 · fence ABIERTO (sin cerrar): la generación se cortó a mitad — el caso
# real nº1 (num_predict mata el JSON/el HTML antes del cierre).
_RESCUE_OPEN_RE = re.compile(r"```([\w+-]*)\n?([\s\S]+)$")


def _code_candidates(text: str) -> List[Tuple[str, str, bool]]:
    """Candidatos a rescate como (lenguaje, contenido, truncado).

    Incluye los fences cerrados Y, si tras quitarlos queda un ``` abierto con
    contenido, ese bloque incompleto (truncado=True) — justo el HTML cortado
    por num_predict que antes se perdía. v4.8 · también detecta HTML CRUDO
    SIN fences (el modelo a veces suelta <!DOCTYPE html… directamente en el
    texto): se trata como candidato truncado."""
    t = text or ""
    out: List[Tuple[str, str, bool]] = []
    for m in _RESCUE_FENCE_RE.finditer(t):
        out.append((m.group(1).lower().strip(), m.group(2), False))
    sin_cerrar = _RESCUE_FENCE_RE.sub("", t)
    m = _RESCUE_OPEN_RE.search(sin_cerrar)
    if m and m.group(2).strip():
        out.append((m.group(1).lower().strip(), m.group(2), True))
    if not out:
        # HTML crudo sin fences: desde <!DOCTYPE html / <html hasta el final
        m2 = re.search(r"<!DOCTYPE\s+html[\s\S]+|<html[\s>][\s\S]+",
                       sin_cerrar, re.IGNORECASE)
        if m2 and len(m2.group(0).strip()) >= 200:
            out.append(("html", m2.group(0), True))
        elif (re.search(r"<(div|h[1-6]|section|article|p)\s+class=", sin_cerrar, re.I)
              and sin_cerrar.count("<") >= 8 and len(sin_cerrar.strip()) >= 200):
            # Fragmento Tailwind/HTML pegado en el chat (sin <html>)
            out.append(("html", sin_cerrar.strip(), True))
    return out


def _should_rescue(text: str, turn_tools: set) -> bool:
    """🛟 True si el agente pegó código cercado como TEXTO y no creó archivos.

    Se cumple cuando en este turno NO se usó NINGUNA skill de escritura
    (_WRITE_TOOLS) y el texto contiene un bloque ```…``` (cerrado O ABIERTO
    por generación cortada) con ≥200 caracteres de contenido."""
    try:
        if set(turn_tools or []) & _WRITE_TOOLS:
            return False
    except TypeError:
        pass
    return any(len(contenido) >= 200 for _lang, contenido, _tr in
               _code_candidates(text))


def _rescue_code_from_text(run: OtterRun, agent_id: str, text: str) -> Iterator[str]:
    """🛟 Red de rescate: guarda el mayor bloque cercado del texto como archivo.

    El modelo a veces suelta el HTML/CSS/JS COMPLETO como texto sin llamar a
    write_file NI UNA VEZ. Esta red extrae el bloque más grande, deduce el
    nombre de archivo por el lenguaje del fence (o el contenido) y lo escribe
    con dispatch("write_file") para que quede auditado; emite los eventos
    tool_call/tool_result sintéticos para que la UI muestre el bloque de
    terminal y la tarjeta de artefacto.

    ES UN GENERADOR (emite SSE): hace yield de los eventos y devuelve el nombre
    del archivo rescatado o None si no se pudo rescatar. Marca
    run._rescue_truncated=True cuando el bloque estaba SIN CERRAR (generación
    cortada) para que el llamador pida completarlo por partes."""
    candidatos = _code_candidates(text or "")
    if not candidatos:
        return  # type: ignore[return-value]
    # el de MAYOR contenido (cerrado o abierto)
    lang, contenido, truncado = max(candidatos, key=lambda c: len(c[1]))
    low = contenido.lstrip().lower()
    if lang == "html" or low.startswith("<!doctype") or low.startswith("<html") or "<div" in low:
        nombre = "index.html"
        if not low.startswith("<!doctype") and not low.startswith("<html"):
            contenido = (
                "<!DOCTYPE html>\n<html lang=\"es\"><head>"
                "<meta charset=\"utf-8\"/>"
                "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/>"
                "<script src=\"https://cdn.tailwindcss.com\"></script>"
                "<title>OtterCode</title></head><body>\n"
                + contenido
                + "\n</body></html>\n"
            )
            low = contenido.lstrip().lower()
    elif lang == "css":
        nombre = "styles.css"
    elif lang in ("js", "javascript"):
        nombre = "script.js"
    elif lang in ("python", "py"):
        nombre = "main.py"
    elif lang == "svg":
        nombre = "image.svg"
    else:
        return  # type: ignore[return-value]  # lenguaje desconocido → no rescata
    if not _write_size_guard(run, nombre, contenido):
        # v4.9 · FIX C: el archivo existente es MÁS completo → se conserva.
        # Se emiten los eventos igualmente para que la UI muestre el bloque
        # y la tarjeta (sin sobrescribir nada).
        run._files_ever_written = True
        call_id = str(uuid.uuid4())
        yield sse(SseEvent.tool_call, {
            "id": call_id, "agent": agent_id, "iteration": 1,
            "tool": "write_file", "args": {"filepath": nombre},
            "title": f"🛟 Rescate: {nombre}",
        })
        yield sse(SseEvent.tool_result, {
            "id": call_id, "tool": "write_file", "ok": True,
            "output": (f"Se conserva la versión previa de {nombre} (más completa "
                       f"que el nuevo intento): nada se sobrescribe."),
            "ms": 0,
        })
        run.transcript.append(
            {"kind": "tool", "tool": "write_file", "agent": agent_id,
             "iteration": 1, "args": {"filepath": nombre}, "ok": True,
             "output": "versión previa conservada (más completa)"})
        run.transcript.append(
            {"kind": "system",
             "text": (f"🛟 Rescate detenido: {nombre} ya tenía una versión más "
                      f"completa; se conserva sin sobrescribir.")})
        return nombre  # type: ignore[return-value]
    result = run.executor.dispatch("write_file", {"filepath": nombre, "content": contenido})
    if not result.get("ok"):
        return  # type: ignore[return-value]
    run._files_ever_written = True
    run._rescue_truncated = bool(truncado)   # el llamador decide si completar
    call_id = str(uuid.uuid4())
    yield sse(SseEvent.tool_call, {
        "id": call_id, "agent": agent_id, "iteration": 1,
        "tool": "write_file", "args": {"filepath": nombre},
        "title": f"🛟 Rescate: {nombre}",
    })
    yield sse(SseEvent.tool_result, {
        "id": call_id, "tool": "write_file", "ok": True,
        "output": (f"OK: {len(contenido)} caracteres escritos en {nombre} "
                   f"(rescatado del texto del agente)"),
        "ms": 0,
    })
    # v4.8 · el panel Agentes también ve el rescate (antes: last_tool=None)
    _activity_set(last_tool=f"🛟 Rescate: {nombre}", last_tool_ok=True)
    # Las obligaciones (_forced_step_prompt / corrección en modo agente) ya lo ven.
    run._files_ever_written = True
    turn_tools = getattr(run, "_turn_tools", None)
    if not isinstance(turn_tools, set):
        turn_tools = set()
        run._turn_tools = turn_tools
    turn_tools.add("write_file")
    run.transcript.append(
        {"kind": "tool", "tool": "write_file", "agent": agent_id, "iteration": 1,
         "args": {"filepath": nombre}, "ok": True,
         "output": (f"OK: {len(contenido)} caracteres escritos en {nombre} "
                    f"(rescatado del texto del agente)")}
    )
    run.transcript.append(
        {"kind": "system",
         "text": (f"🛟 Red de rescate: el agente pegó código en el texto sin crear "
                  f"archivos; guardado como {nombre}.")}
    )
    return nombre  # type: ignore[return-value]


def _code_blob_from_text(text: str) -> str:
    """Saca el código de un dump de completación (fence o JS/HTML crudo)."""
    t = _strip_think(text or "").strip()
    if not t:
        return ""
    fences = list(_RESCUE_FENCE_RE.finditer(t))
    if fences:
        return fences[-1].group(2).strip()
    m = _RESCUE_OPEN_RE.search(t)
    if m and m.group(2).strip():
        return m.group(2).strip()
    if re.search(r"function\s+\w+|const\s+\w+\s*=|</\w+>|<div\b", t):
        return t
    return ""


def _append_continuation(run: OtterRun, target: str, blob: str) -> bool:
    """Pega el dump del modelo al archivo real (Studio), no solo al chat."""
    blob = (blob or "").strip()
    if len(blob) < 40:
        return False
    path = run.workdir / target
    try:
        prev = path.read_text(encoding="utf-8") if path.is_file() else ""
    except OSError:
        prev = ""
    # Evita duplicar el mismo trozo
    sample = blob[:180].strip()
    if sample and sample in prev:
        return False
    piece = blob
    if target.lower().endswith((".html", ".htm")):
        low = blob.lstrip().lower()
        if not low.startswith("<!doctype") and not low.startswith("<html"):
            if re.search(r"function\s+|const\s+\w+\s*=", blob) and "<script" not in low:
                piece = "\n<script>\n" + blob + "\n</script>\n"
        merged = prev
        if "</body>" in merged.lower():
            idx = merged.lower().rfind("</body>")
            merged = merged[:idx] + piece + "\n" + merged[idx:]
        else:
            merged = merged + "\n" + piece
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(merged, encoding="utf-8")
    else:
        res = run.executor.dispatch("append_file", {"filepath": target, "content": "\n" + piece})
        if not res.get("ok"):
            return False
    run._files_ever_written = True
    return True


def _complete_rescued_file(run: OtterRun, target: str) -> Iterator[str]:
    """D2: completación por partes con preflight, backoff Ollama y stall×3."""
    _set_step(run, f"rescue_complete:{target}")
    yield sse(SseEvent.system, {
        "text": "🛟 El archivo rescatado estaba incompleto (generación "
                "cortada): Otter lo va a completar por partes…"
    })
    tries = max(1, _RESCUE_MAX_TRIES)
    last_exc: Optional[BaseException] = None
    for attempt in range(1, tries + 1):
        if run.aborted:
            raise AbortRequested()
        _set_step(run, f"rescue_complete:{target}:try{attempt}")
        corr_trunc = (
            "COMPLETACIÓN OBLIGATORIA: el archivo principal se creó pero "
            "quedó INCOMPLETO (la generación anterior se cortó a mitad). "
            f"El archivo es EXACTAMENTE «{target}»: usa "
            f"append_file con filepath=\"{target}\". "
            "NO crees NI toques ningún otro archivo. "
            "1) Continúalo con append_file POR PARTES (≤150 líneas por "
            "llamada) hasta que el archivo termine correctamente "
            "(p. ej. cerrando </html>). 2) Después emite finalizar. "
            "NO lo reescribas desde cero: continúa lo que ya hay. "
            "En este turno write_file está DESHABILITADO: solo puedes "
            "append_file, read_file y finalizar."
        )
        try:
            _tail = (run.workdir / target).read_text(encoding="utf-8")[-1200:]
            corr_trunc += (
                f"\n\nAQUÍ ESTÁ EL FINAL ACTUAL DE «{target}»"
                " — continúa "
                "EXACTAMENTE desde donde acaba (NO uses read_file, ya lo "
                "tienes aquí; NO reescribas lo que existe):\n"
                f"{_tail}\n"
                f"Tu PRÓXIMA llamada es append_file con filepath="
                f"\"{target}\" y SOLO la siguiente "
                "parte (≤150 líneas)."
            )
        except OSError:
            pass
        try:
            dumped = yield from run_agent_turn(
                run, run.start_agent, 1, corr_trunc,
                flush=False, max_steps=12,
                only_tools={"append_file", "read_file", "finalizar"})
            blob = _code_blob_from_text(dumped or "")
            if blob and _append_continuation(run, target, blob):
                cid = str(uuid.uuid4())
                yield sse(SseEvent.tool_call, {
                    "id": cid, "agent": run.start_agent, "iteration": 1,
                    "tool": "append_file", "args": {"filepath": target},
                    "title": f"🛟 Completación aplicada a {target}",
                })
                yield sse(SseEvent.tool_result, {
                    "id": cid, "tool": "append_file", "ok": True,
                    "output": f"OK: continuación escrita en {target}",
                    "ms": 0,
                })
            return
        except AbortRequested:
            raise
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            _log_uncaught(run, exc, step=f"rescue_complete:{target}:try{attempt}")
            stalled = note_stall(run, f"rescue:{target}", str(exc), limit=tries)
            yield sse(SseEvent.system, {
                "text": (f"⚠️ Completación de «{target}» falló "
                         f"({type(exc).__name__}: {exc}) — "
                         f"intento {attempt}/{tries}.")
            })
            if stalled or attempt >= tries:
                raise RescueStalled(
                    f"El rescate de «{target}» falló {tries} veces seguidas: "
                    f"{type(exc).__name__}: {exc}. Pulsa Reintentar para "
                    f"continuar desde el archivo ya escrito, no desde cero."
                ) from exc
            wait = min(32, 2 ** attempt)
            yield sse(SseEvent.system, {
                "text": f"⏳ Reintento automático de completación en {wait}s…"
            })
            time.sleep(wait)
    if last_exc:
        raise last_exc


def run_task_stream(run: OtterRun) -> Iterator[str]:
    """Ejecuta la misión (cadena delegada o chat directo) y emite SSE en vivo."""
    started = time.time()
    # v4.1 · ▶ ejecución de un plan ya aprobado (resume_plan)
    resumed = bool(getattr(run, "resume_plan", None))
    yield sse(SseEvent.task_start, {
        "task_id": run.task_id, "task": run.task_text, "model": run.model,
        "loop_mode": run.loop_mode, "mode": run.mode,
        "start_agent": run.start_agent, "workspace": run.workdir.name,
        "max_rounds": run.max_rounds, "hacker": run.hacker,
        "goal": run.goal, "plan_only": run.plan_only,
        "ultra_review": run.ultra_review, "resumed": resumed,
    })
    verdict = ""
    try:
        # ======= MODO CHAT / CLAUDE CODE: un solo agente con skills =======
        if run.mode == "chat":
            prompt = build_chat_prompt(run)
            # v4.3 · ejecutar un plan aprobado en modo agente
            if getattr(run, "resume_plan", None):
                rp = run.resume_plan or {}
                task_txt = run.resume_task or run.task_text
                prompt = build_chat_prompt(run, task_text=task_txt)
                prompt += ("\n\n# PLAN APROBADO POR EL USUARIO — EJECÚTALO YA "
                           "(escribe los archivos, sin volver a planear)\n"
                           + str(rp.get("plan", "")))
                ctx = str(rp.get("context", ""))
                if ctx.strip():
                    prompt += "\n\n# CONTEXTO YA RECABADO\n" + ctx
            # v4.3 · /ultraplan en modo agente: planear SIN escribir
            if getattr(run, "plan_only", False):
                prompt += (
                    "\n\nMODO PLAN (no implementes todavía): NO escribas NI UN "
                    "archivo. Presenta tu plan numerado —qué archivos crearás, "
                    "estructura y pasos— y emite finalizar con el plan como "
                    "resumen. El usuario lo revisará antes de ejecutar."
                )
            # 🧵 continuidad conversacional: anteponer el hilo previo al prompt
            if getattr(run, "continue_task", ""):
                prev_block = _prev_conversation_block(run.continue_task)
                if prev_block:
                    prompt = prev_block + "\n\n" + prompt
            last_text_chat = yield from run_agent_turn(  # type: ignore[misc]
                run, run.start_agent, 1, prompt)
            # 🛟 v4.5 · RED DE RESCATE en modo agente: si soltó código cercado
            # como TEXTO sin usar NINGUNA skill de escritura, se guarda el
            # bloque mayor como archivo ANTES de dar la misión por terminada.
            _rescue_needed_chat = False
            rescued = ""                       # v5.1 · definido SIEMPRE (evita NameError)
            _skip_rescue = bool(getattr(run, "continue_task", "")
                                and getattr(run, "_files_ever_written", False))
            if (not _skip_rescue and not getattr(run, "plan_only", False)
                    and _should_rescue(
                    last_text_chat or "", getattr(run, "_turn_tools", set()))):
                _rescue_needed_chat = True
                run._rescue_truncated = False
                rescued = (yield from _rescue_code_from_text(
                    run, run.start_agent, last_text_chat or "")) or ""  # type: ignore[misc]
            # 🛟 v4.6 · RESCATE TRUNCADO: el bloque estaba SIN CERRAR (generación
            # cortada por num_predict) → el archivo existe pero incompleto.
            # Un turno más para completarlo POR PARTES con append_file.
            # v4.8 · a prueba de fallos: si el turno explota (timeout/VRAM),
            # el artifact rescatado SE CONSERVA y la misión acaba task_done.
            _salv_trunc = getattr(run, "_turn_salvaged_truncated", False)
            _target_file = rescued or getattr(run, "_last_salvaged_file", "") or "index.html"
            if (_salv_trunc
                    or (_rescue_needed_chat and getattr(run, "_rescue_truncated", False))
                    ) and not getattr(run, "plan_only", False) and not run.aborted:
                yield from _complete_rescued_file(run, _target_file)
            # 🛟 v4.5 · CORRECCIÓN OBLIGATORIA (UNA sola vez): había código en
            # el texto pero el rescate no pudo guardar nada (p.ej. lenguaje
            # desconocido) → un turno correctivo que obliga a escribir archivos
            # de verdad con write_file/append_file.
            if (_rescue_needed_chat
                    and not getattr(run, "continue_task", "")
                    and not (getattr(run, "_turn_tools", set()) & _WRITE_TOOLS)
                    and not getattr(run, "_files_ever_written", False)):
                yield sse(SseEvent.system, {
                    "text": ("⚠️ Has pegado el código en el texto sin crear "
                             "archivos: prohibido. Creando archivos de verdad…")
                })
                corr = ("CORRECCIÓN OBLIGATORIA: tu respuesta anterior contenía "
                        "código como TEXTO y NO creaste ningún archivo. "
                        "Está PROHIBIDO pegar código en tu texto. Vuelve a generar "
                        "el contenido PERO ahora crea los archivos de verdad: "
                        "write_file para la 1ª parte (≤150 líneas) y append_file "
                        "para las siguientes. Solo después finaliza.")
                try:
                    yield from run_agent_turn(run, run.start_agent, 1, corr,
                                              flush=False, max_steps=8)
                except Exception as exc:  # noqa: BLE001
                    run.transcript.append(
                        {"kind": "system",
                         "text": (f"⚠️ El turno correctivo falló "
                                  f"({type(exc).__name__}); reintenta la misión.")}
                    )
                    yield sse(SseEvent.system, {
                        "text": "⚠️ El turno correctivo no pudo ejecutarse "
                                "(modelo ocupado/lento)."
                    })
            # 🔁 v5.1 · LOOP REAL en modo agente: tras el trabajo de Otter,
            # el Revisor audita; si no aprueba, Otter corrige sobre los
            # archivos existentes. Máx 3 rondas (bucle infinito real).
            if (getattr(run, "loop_mode", False)
                    and not getattr(run, "plan_only", False)
                    and not run.aborted):
                max_loops = min(run.max_rounds or 3, 3)
                run.approved = None
                for _ronda in range(1, max_loops + 1):
                    if run.aborted:
                        break
                    yield sse(SseEvent.loop_iter, {
                        "iteration": _ronda, "max": max_loops, "infinite": True})
                    run.transcript.append(
                        {"kind": "system",
                         "text": f"🔁 Ronda de revisión {_ronda}/{max_loops}: "
                                 f"el Revisor audita el trabajo de Otter."})
                    veredicto = yield from run_agent_turn(  # type: ignore[misc]
                        run, "reviewer", _ronda,
                        build_reviewer_prompt(run, ""),
                    )
                    if is_approved(veredicto):
                        run.approved = True
                        run.transcript.append(
                            {"kind": "system",
                             "text": f"✅ Revisor: Aprobado en la ronda {_ronda}."})
                        yield sse(SseEvent.system, {
                            "text": f"✅ El Revisor aprobó el trabajo (ronda {_ronda})."
                        })
                        break
                    yield sse(SseEvent.system, {
                        "text": f"🔁 El Revisor pide correcciones: Otter las "
                                f"aplica (ronda {_ronda}/{max_loops})…"
                    })
                    corr_loop = (
                        "CORRECCIONES DEL REVISOR — aplícalas SOBRE los archivos "
                        "que ya existen (no empieces de cero):\n\n"
                        + (veredicto or "")[:4000]
                        + "\n\nAGENTE OTTER — MODO CLAUDE CODE. Corrige con "
                        "edit_file (quirúrgico) o append_file/write_file por "
                        "partes si hay que añadir secciones. Cuando termines, "
                        "finaliza con el resumen."
                    )
                    try:
                        yield from run_agent_turn(  # type: ignore[misc]
                            run, run.start_agent, _ronda + 1, corr_loop,
                            flush=False, max_steps=8,
                            only_tools={"write_file", "append_file", "edit_file",
                                        "read_file", "execute_bash", "finalizar"},
                        )
                    except Exception as exc:  # noqa: BLE001
                        run.transcript.append(
                            {"kind": "system",
                             "text": (f"⚠️ La ronda de corrección {_ronda} falló "
                                      f"({type(exc).__name__}).")})
                        break
                else:
                    if not run.approved and not run.aborted:
                        run.transcript.append(
                            {"kind": "system",
                             "text": (f"🔁 Límite de {max_loops} rondas alcanzado: "
                                      f"el trabajo queda con las observaciones "
                                      f"del último Revisor.")})
            if getattr(run, "plan_only", False):
                plan_text = last_text_chat or ""
                run.files_report = run.executor.list_workspace()
                run.iterations = 0
                run.elapsed = round(time.time() - started, 1)
                run.meta["plan"] = plan_text
                run.meta["context"] = ""
                run.transcript.append(
                    {"kind": "system",
                     "text": "📋 Plan listo: esperando aprobación del usuario."}
                )
                _activity_finish("planned")
                yield sse(SseEvent.task_done, {
                    "task_id": run.task_id, "mode": "chat", "approved": None,
                    "iterations": 0, "files": run.files_report,
                    "duration_s": run.elapsed, "review_verdict": "",
                    "injected_agents": [],
                    "plan_ready": True, "plan": plan_text, "context": "",
                    "goal": run.goal,
                })
                return
            run.files_report = run.executor.list_workspace()
            run.iterations = 1
            run.elapsed = round(time.time() - started, 1)
            run.meta["files"] = run.files_report
            _memory_finish(run, "done")
            from backend.runstate import mark_checkpoint_closed
            mark_checkpoint_closed(run.task_id)
            yield sse(SseEvent.task_done, {
                "task_id": run.task_id, "mode": "chat",
                "approved": run.approved,   # v5.1 · dictamen del loop en chat
                "iterations": 1, "files": run.files_report,
                "duration_s": run.elapsed, "review_verdict": "",
                "injected_agents": [],
            })
            _activity_finish("done")
            return

        # ============ MODO CADENA: delegación jerárquica secuencial =======
        plan = ""
        context = ""
        expert_reports: List[str] = []
        injected: List[Agent] = []
        start_idx = 0
        try:
            start_idx = AGENT_ORDER.index(run.start_agent)
        except ValueError:
            # «agent» (Otter) no forma parte de la cadena: ejecutar como chat.
            run.mode = "chat"
            run.start_agent = "agent"
            prompt = build_chat_prompt(run)
            last_text_chat = yield from run_agent_turn(  # type: ignore[misc]
                run, "agent", 1, prompt)
            run.files_report = run.executor.list_workspace()
            run.iterations = 1
            run.elapsed = round(time.time() - started, 1)
            _memory_finish(run, "done")
            yield sse(SseEvent.task_done, {
                "task_id": run.task_id, "mode": "chat", "approved": run.approved,
                "iterations": 1, "files": run.files_report,
                "duration_s": run.elapsed, "review_verdict": "",
                "injected_agents": [],
            })
            _activity_finish("done")
            return
        # v4.1 · ▶ EJECUTAR PLAN APROBADO: con resume_plan se saltan
        # Arquitecto e Investigador (el plan ya fue aprobado por el usuario).
        if resumed:
            rp = run.resume_plan or {}
            plan = str(rp.get("plan", ""))
            context = str(rp.get("context", ""))
            run.transcript.append(
                {"kind": "system",
                 "text": "▶ Ejecutando PLAN APROBADO por el usuario "
                         "(Arquitecto e Investigador ya hicieron su parte)."}
            )
            yield sse(SseEvent.system, {
                "text": "▶ Ejecutando el plan aprobado: directo al Programador."
            })

        # 1) 🧠 Arquitecto: analiza, divide subtareas, delega y (meta-
        #    orquestación) decide si inyectar agentes dinámicos.
        if start_idx <= 0 and not resumed:
            plan = yield from run_simple_agent(  # type: ignore[misc]
                run, "architect", get_agent("architect").system_prompt,
                build_architect_prompt(run.task_text,
                                       memory=getattr(run, "memory_block", ""),
                                       goal=run.goal),
            )
            # v4.0 · GUARDRAIL: el Arquitecto NO puede soltar código. Si lo
            # hace, se rechaza el plan y se le exige SOLO el formato de plan.
            if _plan_has_code(plan):
                yield sse(SseEvent.system, {
                    "text": "🚧 El Arquitecto intentó escribir código: plan rechazado, "
                            "pidiendo SOLO el plan de delegación…"
                })
                run.transcript.append(
                    {"kind": "system",
                     "text": "🚧 Guardrail: plan del Arquitecto rechazado por contener código."}
                )
                plan = yield from run_simple_agent(  # type: ignore[misc]
                    run, "architect", get_agent("architect").system_prompt,
                    build_architect_prompt(run.task_text,
                                           memory=getattr(run, "memory_block", ""),
                                           goal=run.goal)
                    + "\n\nCORRECCIÓN OBLIGATORIA: tu respuesta anterior contenía "
                    "BLOQUES DE CÓDIGO y fue RECHAZADA. Devuelve ÚNICAMENTE el plan "
                    "en el formato indicado, SIN ningún bloque ``` ni una línea de "
                    "código. Máximo 40 líneas.",
                )
            req_ids, reason = extract_injections(plan)
            for aid in req_ids[:MAX_INJECTIONS]:
                agent = DYNAMIC_AGENTS.get(aid)
                if agent is None or agent in injected:
                    continue
                injected.append(agent)
                run.injected_agents.append(agent.id)
                run.transcript.append(
                    {"kind": "system",
                     "text": f"🧩 Meta-orquestación: el Arquitecto inyecta a {agent.icon} {agent.nombre} "
                             f"({agent.id}) en la cadena — {reason or 'sin motivo indicado'}."}
                )
                yield sse(SseEvent.agent_injected, {
                    "agent": agent.to_dict(),
                    "position": "after_context",
                    "reason": reason,
                })
            if injected:
                extra = " · tras el contexto trabajará " + ", ".join(
                    f"{a.icon} {a.nombre}" for a in injected
                ) + " (especialista inyectado)"
            else:
                extra = ""
            from backend.hooks import pre_agent_handoff
            pre_agent_handoff("architect", "researcher", run.task_id)
            yield sse(SseEvent.delegate, {
                "from": "architect", "to": "researcher",
                "text": f"🧠 → Delegando a 🔬 el Investigador (análisis de contexto){extra}",
            })

        # 2) 🔬 Investigador: explora el workspace y produce el informe.
        if start_idx <= 1 and not resumed:
            context = yield from run_agent_turn(  # type: ignore[misc]
                run, "researcher", 1, build_researcher_prompt(run.task_text, plan,
                                                              goal=run.goal)
            )
            # v3.4 · OBLIGACIÓN: si finalizó sin explorar NADA → 1 paso forzado
            fp = _forced_step_prompt("researcher", getattr(run, "_turn_tools", set()))
            if fp:
                yield sse(SseEvent.system, {
                    "text": "⚠️ El Investigador no exploró el workspace: forzando exploración…"
                })
                try:
                    context = yield from run_agent_turn(  # type: ignore[misc]
                        run, "researcher", 1,
                        build_researcher_prompt(run.task_text, plan, goal=run.goal) + "\n\n" + fp,
                        flush=False, max_steps=6,
                    )
                except Exception as exc:  # noqa: BLE001
                    run.transcript.append(
                        {"kind": "system",
                         "text": (f"⚠️ La exploración forzada falló "
                                  f"({type(exc).__name__}); se continúa sin informe ampliado.")}
                    )

        # v4.1 · 📋 /ultraplan: plan + contexto listos → ESPERAR aprobación.
        # No se escribe NI UN archivo: el usuario decide con el plan delante.
        if getattr(run, "plan_only", False):
            run.files_report = run.executor.list_workspace()
            run.iterations = 0
            run.elapsed = round(time.time() - started, 1)
            run.meta["plan"] = plan
            run.meta["context"] = context
            run.transcript.append(
                {"kind": "system",
                 "text": "📋 ULTRA PLAN listo: esperando aprobación del usuario "
                         "(no se ha escrito ningún archivo todavía)."}
            )
            _activity_finish("planned")
            yield sse(SseEvent.task_done, {
                "task_id": run.task_id, "mode": "chain", "approved": None,
                "iterations": 0, "files": run.files_report,
                "duration_s": run.elapsed, "review_verdict": "",
                "injected_agents": run.injected_agents,
                "plan_ready": True, "plan": plan, "context": context,
                "goal": run.goal,
            })
            return

        if injected:
                yield sse(SseEvent.delegate, {
                    "from": "researcher", "to": injected[0].id,
                    "text": f"🔬 → {injected[0].icon} Pasando el contexto al especialista {injected[0].nombre}",
                })

        for i, agent in enumerate(injected):
            prev = expert_reports[-1] if expert_reports else ""
            report = yield from run_agent_turn(  # type: ignore[misc]
                run, agent.id, 1, build_expert_prompt(run, agent, plan, context, prev)
            )
            if report and report.strip():
                expert_reports.append(report.strip())
            nxt = injected[i + 1].id if i + 1 < len(injected) else "developer"
            from backend.hooks import pre_agent_handoff
            pre_agent_handoff(agent.id, nxt, run.task_id)
            yield sse(SseEvent.delegate, {
                "from": agent.id, "to": nxt,
                "text": f"{agent.icon} → Delegando: informe de {agent.nombre} para la próxima fase",
            })

        feedback = None
        dev_iter = 1
        effective_rounds = run.max_rounds if start_idx <= 2 else 1
        for iteration in range(1, effective_rounds + 1):
            run.iterations = iteration
            if start_idx <= 2:
                dev_text = yield from run_agent_turn(  # type: ignore[misc]
                    run, "developer", dev_iter,
                    build_developer_prompt(run.task_text, plan, context, feedback,
                                           expert_reports, goal=run.goal),
                )
                # 🛟 v4.5 · RED DE RESCATE: si soltó código cercado como TEXTO
                # sin crear archivos, se guarda el bloque mayor ANTES de forzar.
                _rescued_dev = ""
                run._rescue_truncated = False
                if _should_rescue(dev_text or "",
                                  getattr(run, "_turn_tools", set())):
                    _rescued_dev = (yield from _rescue_code_from_text(
                        run, "developer", dev_text or "")) or ""  # type: ignore[misc]
                # v3.4 · OBLIGACIÓN: si no creó/modificó NI UN archivo → 1 paso
                # forzado de escritura (mata el "habla mucho, no hace nada").
                # 🛟 Si el rescate ya guardó los archivos COMPLETOS, no hace
                # falta forzar; si el rescate quedó TRUNCADO, sí se completa.
                fp = None if (_rescued_dev and not getattr(
                    run, "_rescue_truncated", False)) else _forced_step_prompt(
                    "developer", getattr(run, "_turn_tools", set()))
                if fp:
                    yield sse(SseEvent.system, {
                        "text": "⚠️ El Programador no creó ningún archivo: forzando escritura…"
                    })
                    try:
                        yield from run_agent_turn(  # type: ignore[misc]
                            run, "developer", dev_iter, fp, flush=False,
                            max_steps=8,
                        )
                    except Exception as exc:  # noqa: BLE001
                        run.transcript.append(
                            {"kind": "system",
                             "text": (f"⚠️ El paso forzado del Programador falló "
                                      f"({type(exc).__name__}); continúa el relevo.")}
                        )
                        yield sse(SseEvent.system, {
                            "text": "⚠️ El paso forzado no pudo ejecutarse; "
                                    "se pasa al Revisor."
                        })
            verdict = yield from run_agent_turn(  # type: ignore[misc]
                run, "reviewer", iteration, build_reviewer_prompt(run, plan)
            )
            if is_approved(verdict):
                run.approved = True
                break
            if not run.loop_mode:
                run.transcript.append(
                    {"kind": "system", "text": "ℹ️ Bucle infinito desactivado: el dictamen del Revisor es definitivo."}
                )
                break
            if iteration >= effective_rounds:
                yield sse(SseEvent.loop_exhausted, {"max": effective_rounds, "verdict": verdict[:500]})
                run.transcript.append(
                    {"kind": "system",
                     "text": f"🔁 Límite de seguridad alcanzado ({effective_rounds} rondas): quedan observaciones del Revisor."}
                )
                break
            dev_iter += 1
            yield sse(SseEvent.loop_iter, {"iteration": dev_iter, "max": effective_rounds, "infinite": run.loop_mode})
            run.transcript.append(
                {"kind": "system",
                 "text": f"🔄 Bucle infinito — ronda {dev_iter}: el Revisor (tests fallando) devuelve las correcciones al Programador."}
            )
            feedback = verdict

        run.files_report = run.executor.list_workspace()
        run.iterations = max(run.iterations, 1)
        run.elapsed = round(time.time() - started, 1)
        run.meta["files"] = run.files_report
        _memory_finish(run, "done")
        yield sse(SseEvent.task_done, {
            "task_id": run.task_id, "mode": "chain", "approved": run.approved,
            "iterations": run.iterations, "files": run.files_report,
            "duration_s": run.elapsed, "review_verdict": verdict,
            "injected_agents": run.injected_agents,
        })
        _activity_finish("done")
    except AbortRequested:
        run.transcript.append({"kind": "system", "text": "⏹ Misión abortada por el usuario."})
        _memory_finish(run, "aborted")
        yield sse(SseEvent.task_aborted, {"task_id": run.task_id})
        _activity_finish("aborted")
    except Exception as exc:  # noqa: BLE001 — el error viaja por el stream
        one = _log_uncaught(run, exc)
        run.transcript.append({"kind": "system", "text": f"❌ {one}"})
        _memory_finish(run, "error")
        detail = str(exc)[:800] or type(exc).__name__
        yield sse(SseEvent.task_error, {
            "message": f"Error: {type(exc).__name__}: {detail}",
            "error_type": type(exc).__name__,
            "detail": detail,
            "step": getattr(run, "_current_step", "") or "",
            "task_id": run.task_id,
        })
        _activity_finish("error")
    finally:
        run.meta["approved"] = run.approved
        run.meta["iterations"] = run.iterations
        run.meta["files"] = run.executor.list_workspace()
        run.meta["injected_agents"] = run.injected_agents
        run.meta["duration_s"] = round(time.time() - started, 1)
        try:
            save_session(run)       # JSON legacy
            save_session_to_db(run) # FASE 2 · SQLite + FTS5
        except Exception as _e:  # noqa: BLE001
            print(f"[WARN] Persistencia fallida para {run.task_id}: {_e}", flush=True)
        except Exception as _e:  # noqa: BLE001
            print(f"[WARN] Persistencia fallida para {run.task_id}: {_e}", flush=True)
