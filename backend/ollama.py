# OtterCode — transporte LLM: sesiones, VRAM flush, streaming, errores
from __future__ import annotations
from backend.config import *  # noqa: F401,F403
from backend.runstate import OtterRun  # noqa: E402
from backend.history import TOOL_CAPABLE_MODELS  # noqa: E402
from backend.config import FLUSH_WAIT_SECONDS, GENERATE_TIMEOUT, LLM_BACKEND, NUM_CTX_DEFAULT, NUM_PREDICT_DEFAULT, OLLAMA_BASE_URL, _ollama_httpx, _ollama_session  # noqa: E402
from backend.agents import _chat_base  # noqa: E402
from backend.runstate import AbortRequested  # noqa: E402  (abortos en stream_llm)
import backend.settings as _otter_settings  # noqa: E402


class _LlmSession:
    """Sesión mínima para stream_llm fuera de una misión (la Fábrica)."""

    def __init__(self, model: str):
        self.model = model
        self.aborted = False


# ---------------------------------------------------------------------------
# REGLA DE ORO — VRAM FLUSH
# ---------------------------------------------------------------------------

def flush_vram(model: str) -> Dict[str, Any]:
    """Fuerza la descarga del modelo de la VRAM (keep_alive: 0) y espera 1 s.

    Se invoca ANTES de cada turno de agente (core o dinámico) y antes de que
    la Fábrica genere perfiles: libera el KV-cache de la GPU (RTX 5060 8GB)
    para que los contextos masivos se respalden en la RAM sin crashear la GPU.

    Transporte OpenAI-compat (MLC Chat / llama.cpp en modo OpenAI): no existe
    keep_alive equivalente → el paso de testigo se registra como no-op (el
    contexto vivo del móvil se gestiona solo en la RAM del dispositivo).
    """
    if LLM_BACKEND == "openai":
        return {
            "ok": True, "ms": 0,
            "detail": "OpenAI-compat: sin descarga de VRAM (no-op; el modelo vive en la RAM del móvil)",
        }
    flush_timeout = int(os.environ.get("OTTERCODE_FLUSH_TIMEOUT", "60"))
    started = time.time()
    try:
        resp = _ollama_session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": model, "keep_alive": 0, "prompt": "", "stream": False},
            timeout=flush_timeout,
        )
        detail = _safe_detail(resp)
        ok = resp.status_code == 200
    except requests.RequestException as exc:
        return {
            "ok": False,
            "ms": int((time.time() - started) * 1000),
            "error": f"Ollama inaccesible en {OLLAMA_BASE_URL}: {exc}",
        }
    if ok:
        time.sleep(FLUSH_WAIT_SECONDS)  # ← la Regla de Oro exige esta pausa
    return {
        "ok": ok,
        "ms": int((time.time() - started) * 1000),
        "detail": detail if not ok else "KV-cache descargado de VRAM",
    }


def flush_all_vram() -> Dict[str, Any]:
    """Expulsa de la VRAM CUALQUIER modelo que esté cargado en ese momento.

    Consulta /api/ps, y para cada modelo cargado fuerza keep_alive:0 (en
    /api/generate y, si no responde, /api/chat). Devuelve la lista de modelos
    expulsados. Si /api/ps no se puede consultar, cae al flush del modelo por
    defecto (comportamiento anterior) para no dejar la GPU sin limpiar.
    """
    if LLM_BACKEND == "openai":
        return {
            "ok": True, "ms": 0, "expelled": [],
            "detail": "OpenAI-compat: sin descarga de VRAM (no-op)",
        }
    flush_timeout = int(os.environ.get("OTTERCODE_FLUSH_TIMEOUT", "60"))
    started = time.time()
    expelled: List[str] = []
    try:
        ps = _ollama_httpx.get("/api/ps", timeout=flush_timeout).json()
        models = [
            str(m.get("name", "")).strip()
            for m in ps.get("models", []) if isinstance(m, dict) and m.get("name")
        ]
    except Exception:
        models = []
    if not models:
        models = [DEFAULT_MODEL]  # fallback seguro
    ok = True
    errors: List[str] = []
    from backend.router import ROUTER_MODEL, is_router_model
    for model in models:
        if is_router_model(model) or model == ROUTER_MODEL:
            continue
        try:
            resp = _ollama_session.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={"model": model, "keep_alive": 0, "prompt": "", "stream": False},
                timeout=flush_timeout,
            )
            if resp.status_code == 200:
                expelled.append(model)
            else:
                # segundo intento vía /api/chat
                resp2 = _ollama_session.post(
                    f"{OLLAMA_BASE_URL}/api/chat",
                    json={"model": model, "keep_alive": 0, "messages": [], "stream": False},
                    timeout=flush_timeout,
                )
                if resp2.status_code == 200:
                    expelled.append(model)
                else:
                    errors.append(f"{model}: HTTP {resp.status_code}")
        except requests.RequestException as exc:
            ok = False
            errors.append(f"{model}: {exc}")
    if ok:
        time.sleep(FLUSH_WAIT_SECONDS)  # Regla de Oro: pausa tras descargar
    return {
        "ok": ok or bool(expelled),
        "ms": int((time.time() - started) * 1000),
        "expelled": expelled,
        "detail": f"Descargados de VRAM: {', '.join(expelled) or 'ninguno'}",
        "errors": errors,
    }


def _safe_detail(resp: requests.Response) -> str:
    try:
        payload = resp.json()
        if isinstance(payload, dict):
            return str(payload.get("error") or json.dumps(payload, ensure_ascii=False))[:300]
        return str(payload)[:300]
    except Exception:  # noqa: BLE001
        return (resp.text or f"HTTP {resp.status_code}")[:300]


# P4.2 · Caché de model list (TTL 30s)
_models_cache: Optional[Tuple[float, List[str]]] = None

def fetch_models() -> Optional[List[str]]:
    """Lista los modelos disponibles (Ollama /api/tags o OpenAI /v1/models;
    None si el servicio está caído). Con caché configurable (TTL env)."""
    global _models_cache
    models_ttl = int(os.environ.get("OTTERCODE_MODELS_TTL", "30"))
    now = time.time()
    if _models_cache and (now - _models_cache[0]) < models_ttl:
        return _models_cache[1]
    try:
        if LLM_BACKEND == "openai":
            resp = _ollama_httpx.get(f"{_chat_base()}/models")
            resp.raise_for_status()
            payload = resp.json()
            result = [m["id"] for m in payload.get("data", []) if m.get("id")]
        else:
            resp = _ollama_httpx.get("/api/tags")
            resp.raise_for_status()
            payload = resp.json()
            result = [m["name"] for m in payload.get("models", []) if m.get("name")]
        _models_cache = (now, result)
        return result
    except (httpx.HTTPError, ValueError):
        _models_cache = None  # no cachear estado caído: re-intentar en el próximo tick
        return None


def invalidate_models_cache() -> None:
    """Invalida la caché de modelos (tras pull/create/delete/copy)."""
    global _models_cache
    _models_cache = None


# ---------------------------------------------------------------------------
# Errores amigables + stream de Ollama
# ---------------------------------------------------------------------------

def _friendly_ollama_error(exc: Exception) -> str:
    msg = str(exc)
    name = type(exc).__name__
    if "Connection" in name or "Connection refused" in msg:
        return (
            f"Ollama no responde en {OLLAMA_BASE_URL}. "
            f"¿Está corriendo? (arranca con: ollama serve)"
        )
    if "Timeout" in name:
        return (
            "Ollama tardó demasiado en responder. El modelo puede estar cargando a "
            "VRAM por primera vez; reintenta la misión."
        )
    return msg


def _with_model_hint(detail: str, model: str) -> str:
    if "not found" in detail.lower():
        return f"{detail} → solución: `ollama pull {model}`"
    return detail


def stream_llm(
    run: Any, agent_id: str, system_prompt: str, prompt: str
) -> Iterator[str]:
    """Stream del modelo. Yields eventos SSE 'token'; devuelve (texto, stats).

    Soporta dos transportes:
      - "ollama"  : POST /api/generate (stream NDJSON) — Ollama, o llama.cpp
                    (llama-server) en su modo API-Ollama sobre el móvil.
      - "openai"  : POST /v1/chat/completions (stream SSE) — MLC Chat,
                    llama.cpp en modo OpenAI, LM Studio…

    Robustez: si la conexión cae ANTES del primer token, reintenta UNA vez
    (nunca a mitad de stream, para no duplicar tokens en la UI).
    `run` solo aporta .model y .aborted (OtterRun o _LlmSession).
    """
    attempt = 0
    while True:
        attempt += 1
        # v6.0 · el transporte puede conmutarse a mitad de misión (fallback)
        url, payload = _llm_request(run, system_prompt, prompt)
        _is_chat = "/api/chat" in url
        collected: List[str] = []
        stats: Dict[str, Any] = {}
        emitted = False
        _chat_tool_calls: List[Any] = []
        try:
            with _ollama_session.post(
                url, json=payload, stream=True, timeout=GENERATE_TIMEOUT,
            ) as resp:
                # Abort agresivo: api_abort cierra este socket y el stream muere
                # al instante (antes el abort esperaba al timeout de 300 s).
                try:
                    run._active_resp = resp
                except AttributeError:
                    pass
                if resp.status_code != 200:
                    _detail = _safe_detail(resp)
                    # v6.0 · Fase 1: servidor sin /api/chat → fallback generate
                    if (resp.status_code == 404
                            and _is_chat and LLM_BACKEND == "ollama"
                            and getattr(run, "_transport", "chat") == "chat"):
                        run._transport = "generate"
                        yield sse(SseEvent.system, {
                            "text": "ℹ️ Este servidor no soporta /api/chat: "
                                    "cambiando a /api/generate (compatibilidad)."
                        })
                        continue
                    raise RuntimeError(
                        _with_model_hint(
                            f"El modelo devolvió HTTP {resp.status_code}: {_detail}",
                            run.model,
                        )
                    )
                for raw in resp.iter_lines():
                    if run.aborted:
                        # v4.4 · conserva lo generado antes del aborto
                        try:
                            run._partial_text = "".join(collected)
                        except AttributeError:
                            pass
                        raise AbortRequested()
                    if not raw:
                        continue
                    text_line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
                    if LLM_BACKEND == "openai":
                        line = text_line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            stats = {"tokens": None, "seconds": 0.0}
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if chunk.get("error"):
                            raise RuntimeError(
                                _with_model_hint(f"Modelo: {chunk['error']}", run.model))
                        choices = chunk.get("choices") or [{}]
                        token = (choices[0].get("delta") or {}).get("content") or ""
                        if token:
                            collected.append(token)
                            emitted = True
                            yield sse(SseEvent.token, {"agent": agent_id, "token": token})
                        continue
                    try:
                        data = json.loads(text_line)
                    except json.JSONDecodeError:
                        continue
                    if data.get("error"):
                        raise RuntimeError(_with_model_hint(f"Ollama: {data['error']}", run.model))
                    if data.get("done"):
                        stats = {
                            "tokens": data.get("eval_count"),
                            "seconds": round((data.get("eval_duration") or 0) / 1e9, 2),
                        }
                        # v6.0 · los tool_calls nativos llegan en frames INTERMEDIOS
                        # (Gemma/otros), NO en el frame final "done" → se acumulan.
                        msg = data.get("message") or {}
                        tc = msg.get("tool_calls") or []
                        if tc:
                            _chat_tool_calls.extend(tc)
                        if _chat_tool_calls:
                            stats["message"] = {"role": "assistant", "content": "",
                                                "tool_calls": _chat_tool_calls}
                        elif msg:
                            stats["message"] = msg
                        break
                    msg = data.get("message") or {}
                    if _is_chat and msg.get("tool_calls"):
                        _chat_tool_calls.extend(msg["tool_calls"])
                        continue
                    token = msg.get("content", "") if _is_chat else data.get("response", "")
                    if token:
                        collected.append(token)
                        emitted = True
                        yield sse(SseEvent.token, {"agent": agent_id, "token": token})
            if getattr(run, "_active_resp", None) is resp:
                run._active_resp = None
            return "".join(collected), stats
        except (AttributeError, requests.exceptions.ChunkedEncodingError) as exc:
            # v3.4 · carrera del ABORT AGRESIVO: api_abort cerró el socket
            # mientras el worker leía ('NoneType' has no 'read', chunk roto…).
            # Con abort solicitado es un final LIMPIO, nunca un task_error.
            if run.aborted:
                # v4.4 · conserva lo generado antes del aborto
                try:
                    run._partial_text = "".join(collected)
                except AttributeError:
                    pass
                raise AbortRequested()
            raise RuntimeError(_friendly_ollama_error(exc))
        except (requests.ConnectionError, requests.ConnectTimeout,
                requests.ReadTimeout) as exc:
            if run.aborted:
                # v4.4 · conserva lo generado antes del aborto
                try:
                    run._partial_text = "".join(collected)
                except AttributeError:
                    pass
                raise AbortRequested()
            if emitted or attempt > 2:
                raise RuntimeError(_friendly_ollama_error(exc))
            # v4.8 · hasta 3 intentos sin primer token: el swap de modelos en
            # Ollama (otro modelo residente) + prefill puede rozar los 420 s.
            yield sse(SseEvent.system, {
                "text": f"⏳ Ollama está cargando/ocupado (intento {attempt}/3); "
                        f"reintentando en 3 s…"
            })
            time.sleep(3)


def _llm_request(run: Any, system_prompt: str, prompt: str) -> Tuple[str, Dict[str, Any]]:
    """(url, payload) según el transporte configurado.

    v6.0 · Fase 1: transporte primario /api/chat con messages[] por roles
    (memoria conversacional nativa); fallback /api/generate para servidores
    que no lo soporten (run._transport = "generate" lo conmuta stream_llm).
    Envía SIEMPRE options (num_ctx/num_predict) y keep_alive.
    """
    num_ctx = getattr(run, "num_ctx", None) or NUM_CTX_DEFAULT
    keep = os.environ.get("OTTERCODE_KEEP_ALIVE", "15m")
    transport = getattr(run, "_transport", "chat")
    # FASE 3 · resolver temperature/top_p del run (viene del perfil)
    _temp = getattr(run, "temperature", None) or 0.7
    _top = getattr(run, "top_p", None) or 0.9
    if LLM_BACKEND == "openai" or transport == "openai":
        return (
            f"{_chat_base()}/chat/completions",
            {
                "model": run.model,
                "stream": True,
                "max_tokens": NUM_PREDICT_DEFAULT,
                "temperature": _temp,
                "top_p": _top,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
            },
        )
    if transport == "generate":
        _prompt = _flatten_messages(getattr(run, "messages", None)) or prompt
        return (
            f"{OLLAMA_BASE_URL}/api/generate",
            {
                "model": run.model, "prompt": _prompt, "system": system_prompt,
                "stream": True,
                "keep_alive": keep,
                "options": _otter_settings.build_options(run),
            },
        )
    _messages: List[Dict[str, str]] = []
    if system_prompt:
        _messages.append({"role": "system", "content": system_prompt})
    _hist = getattr(run, "messages", None)
    if _hist:
        _messages.extend(_hist)
    else:
        _messages.append({"role": "user", "content": prompt})
    # FASE 5 · Native Function Calling: enviar tools solo si el modelo es capaz
    _tools = tools.get_ollama_tools() if any(m in run.model for m in TOOL_CAPABLE_MODELS) else None
    
    return (
        f"{OLLAMA_BASE_URL}/api/chat",
        {
            "model": run.model, "messages": _messages, "stream": True,
            "keep_alive": keep,
            "tools": _tools,
            "options": _otter_settings.build_options(run),
        },
    )


def _estimate_tokens(text: str) -> int:
    """Estimación ligera de tokens (~4 chars/token, cero dependencias)."""
    return max(1, len(text or "") // 4)


def _flatten_messages(messages: Optional[List[Dict[str, str]]]) -> str:
    """Aplana la cola de mensajes a un prompt plano (fallback /api/generate)."""
    if not messages:
        return ""
    return "\n\n".join(
        f"[{(m.get('role') or 'user').upper()}]\n{m.get('content', '')}"
        for m in messages)


# ---------------------------------------------------------------------------
def _ollama_ndjson_text(raw: str) -> Optional[str]:
    """Extrae el texto completo de una respuesta Ollama.

    Acepta JSON puro (stream:false real) y NDJSON por trozos
    (mock, llama.cpp modo Ollama): acumula TODOS los fragmentos
    'response' en orden. Devuelve None si no hay nada parseable."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:                                    # JSON único completo
        data = json.loads(raw)
        if isinstance(data, dict) and "response" in data:
            return str(data.get("response") or "") or None
    except json.JSONDecodeError:
        pass
    pieces: List[str] = []
    for line in raw.splitlines():           # NDJSON: acumula fragmentos
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            piece = data.get("response")
            if piece:
                pieces.append(str(piece))
    return "".join(pieces) or None
