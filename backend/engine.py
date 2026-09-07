import threading
import uuid
from typing import Iterator, Optional, Dict, Any

# FASE 5 · Gate de permisos para herramientas destructivas
PENDING_PERMISSIONS: Dict[str, threading.Event] = {}
PERMISSION_RESPONSES: Dict[str, bool] = {}
ASK_PERMISSIONS = False

from backend.vault import memory_note_for_run  # noqa: E402
from backend.runtime import _activity_finish  # noqa: E402
from backend.runstate import OtterRun, _condense_entries  # noqa: E402
from backend.prompts import _looks_like_tool_attempt, _prev_conversation_block, alias_args, build_architect_prompt, build_chat_prompt, build_developer_prompt, build_expert_prompt, build_researcher_prompt, build_reviewer_prompt, extract_injections, extract_tool_call, format_tool_result, is_approved  # noqa: E402
from backend.ollama import _estimate_tokens, _ollama_ndjson_text, flush_vram, stream_llm  # noqa: E402
from backend.history import TOOL_CAPABLE_MODELS, save_session  # noqa: E402
from backend.db import save_session_to_db  # noqa: E402
from backend.config import HACKER_SUFFIX, LLM_BACKEND, MAX_INJECTIONS, MAX_TOOL_STEPS, NUM_CTX_DEFAULT, OLLAMA_BASE_URL  # noqa: E402
from backend.agents import AGENT_ORDER, Agent, DYNAMIC_AGENTS, ULTRAREVIEW_SUFFIX, _CORE_BASE_PROMPTS, _agent_meta, _chat_base, get_agent, skill_enabled, tool_protocol  # noqa: E402
from backend.agents import *  # noqa: F401,F403
from backend.prompts import *  # noqa: F401,F403
from backend.ollama import *  # noqa: F401,F403
from backend.runstate import *  # noqa: F401,F403
from backend.runtime import *  # noqa: F401,F403  (hen ATIVITY, ACTIVE_RUN)
from backend.runtime import _force_stop_run, _activity_set, _activity_finish  # noqa
from backend.history import *  # noqa: F401,F403
from backend.vault import _memory_finish, _memory_recall, memory_note_for_run  # noqa
from backend.memory import get_memory  # noqa




_COMPACT_SYSTEM = (
    "Eres el compresor de memoria de OtterCode. Resume el estado del trabajo "
    "en ESPAÑOL, máximo 220 palabras, en este formato exacto:\n"
    "OBJETIVO: <una línea>\nHECHO: <lista compacta de logros>\n"
    "PENDIENTE: <lista compacta de lo que falta>\nDATOS CLAVE: <rutas de "
    "archivos, nombres y decisiones que NO deben perderse>."
)


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
                "options": {"num_ctx": int(num_ctx), "num_predict": 512},
            },
            timeout=(10, 120),
        )
        resp.raise_for_status()
        return _ollama_ndjson_text(resp.text)
    except Exception:  # noqa: BLE001 — la compactación jamás tumba un turno
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
    text, _stats = yield from stream_llm(run, agent_id, system_prompt, prompt)  # type: ignore[misc]
    run.transcript.append({"kind": "agent", "agent": agent_id, "iteration": iteration, "text": text})
    yield sse(SseEvent.agent_end, {"agent": agent_id, "iteration": iteration})
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
    
    _activity_set(agent=agent_id, agent_nombre=meta.get("nombre", ""),
                  agent_icon=meta.get("icon", ""), iteration=iteration,
                  last_tool=None, last_tool_ok=None)
    executor = (
        tools.ToolExecutor(run.workdir, readonly=True) if agent.readonly else run.executor
    )

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
        # ── v6.0 · GESTIÓN DE CONTEXTO (estimación de tokens + truncado) ──
        _limit_tok = int((getattr(run, "num_ctx", None) or NUM_CTX_DEFAULT) * 0.8)
        _est = (_estimate_tokens(system_prompt)
                + sum(_estimate_tokens(m.get("content", "")) for m in run.messages))
        if _est > _limit_tok and len(run.messages) > 6:
            _old = run.messages[1:-6]
            _cond = [(m.get("content", ""), "") for m in _old
                     if m.get("role") == "assistant"]
            yield sse(SseEvent.system, {
                "text": f"🗜️ Contexto compactado (~{_est:,} tokens estimados > "
                        f"{_limit_tok:,}): resumiendo {len(_old)} mensajes antiguos…"
            })
            _summ = _compact_context(run, agent_id, _cond) or "(historial antiguo truncado)"
            run.messages = (run.messages[:1]
                            + [{"role": "user",
                                "content": f"[RESUMEN DEL CONTEXTO ANTERIOR]\n{_summ}"}]
                            + run.messages[-6:])
            run.transcript.append(
                {"kind": "system",
                 "text": f"🗜️ {get_agent(agent_id).nombre}: contexto compactado "
                         f"({len(_old)} mensajes antiguos resumidos a {len(_summ)} chars)."}
            )
        steps += 1
        # v4.4 · cada generación arranca con el parcial a cero
        try:
            run._partial_text = ""
        except AttributeError:
            pass
        try:
            last_text, _stats = yield from stream_llm(run, agent_id, system_prompt, prompt)  # type: ignore[misc]
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
                        if event["kind"] == "start":
                            yield sse(SseEvent.tool_call, {"agent": agent_id, "iteration": iteration,
                                      "tool": event["name"], "args": event["args"], "title": f"🛠️ {event['name']}"})
                        elif event["kind"] == "result":
                            yield sse(SseEvent.tool_result, {"tool": event["name"], "ok": event["ok"], "output": event["output"]})
                            run.messages.append({
                                "role": "tool",
                                "tool_call_id": call.get("id"),
                                "content": str(event.get("output", ""))
                            })

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
                # 🛟 v4.8 · SALVAJE DE JSON CORTADO: si el JSON llevaba un archivo
                # grande, el contenido parcial está ÍNTEGRO — se escribe DIRECTO
                # en vez de pedir reemisión (que re-vuelca todo y re-se corta).
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
                        run._turn_salvaged_truncated = True
                        run._last_salvaged_file = s_nombre
                        break   # turno terminado con el archivo a salvo
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

        # FASE 5 · Gate de permisos
        if ASK_PERMISSIONS and tool_name in _WRITE_TOOLS:
            perm_id = str(uuid.uuid4())
            PENDING_PERMISSIONS[perm_id] = threading.Event()
            yield sse("perm_request", {
                "id": perm_id, "tool": tool_name, "args": args, "title": title
            })
            PENDING_PERMISSIONS[perm_id].wait()
            if not PERMISSION_RESPONSES.get(perm_id, False):
                error_msg = f"ACCESO DENEGADO: usuario denegó '{tool_name}'"
                yield sse(SseEvent.tool_result, {"id": call_id, "tool": tool_name, "ok": False, "output": error_msg, "ms": 0})
                del PENDING_PERMISSIONS[perm_id]
                PERMISSION_RESPONSES.pop(perm_id, None)
                continue
            del PENDING_PERMISSIONS[perm_id]
            PERMISSION_RESPONSES.pop(perm_id, None)

        yield sse(SseEvent.tool_call, {
            "id": call_id, "agent": agent_id, "iteration": iteration,
            "tool": tool_name, "args": args, "title": title,
        })
        _activity_set(last_tool=title, last_tool_ok=None)
        result = executor.dispatch(tool_name, args)
        yield sse(SseEvent.tool_result, {
            "id": call_id, "tool": tool_name, "ok": result["ok"],
            "output": result["output"], "ms": result["ms"],
        })
        _activity_set(last_tool_ok=result["ok"])
        if result.get("ok") and tool_name in _WRITE_TOOLS:
            run._files_ever_written = True   # v4.9 · FIX B
        run.transcript.append(
            {"kind": "tool", "tool": tool_name, "agent": agent_id, "iteration": iteration,
             "args": args, "ok": result["ok"], "output": result["output"]}
        )
        # Handoff dentro del turno: el resultado de la skill vuelve a la cola.
        run._turn_tools.add(tool_name)
        run.messages.append(
            {"role": "user", "content": format_tool_result(tool_name, result)})
    else:
        yield sse(SseEvent.system, {
            "text": f"⚠️ Límite de skills por turno alcanzado ({max_steps}); el turno termina."
        })

    yield sse(SseEvent.agent_end, {"agent": agent_id, "iteration": iteration, "steps": steps})
    return _strip_think(last_text)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# El relevo completo (AgentLoop) + meta-orquestación
# ---------------------------------------------------------------------------

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
    if lang == "html" or low.startswith("<!doctype") or low.startswith("<html"):
        nombre = "index.html"
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
            if not getattr(run, "plan_only", False) and _should_rescue(
                    last_text_chat or "", getattr(run, "_turn_tools", set())):
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
                yield sse(SseEvent.system, {
                    "text": "🛟 El archivo rescatado estaba incompleto (generación "
                            "cortada): Otter lo va a completar por partes…"
                })
                corr_trunc = (
                    "COMPLETACIÓN OBLIGATORIA: el archivo principal se creó pero "
                    "quedó INCOMPLETO (la generación anterior se cortó a mitad). "
                    f"El archivo es EXACTAMENTE «{rescued or 'index.html'}»: usa "
                    f"append_file con filepath=\"{rescued or 'index.html'}\". "
                    "NO crees NI toques ningún otro archivo. "
                    "1) Continúalo con append_file POR PARTES (≤150 líneas por "
                    "llamada) hasta que el archivo termine correctamente "
                    "(p. ej. cerrando </html>). 2) Después emite finalizar. "
                    "NO lo reescribas desde cero: continúa lo que ya hay. "
                    "En este turno write_file está DESHABILITADO: solo puedes "
                    "append_file, read_file y finalizar."
                )
                # v4.8 · inyecta la COLA del archivo: sin esto el modelo entra
                # en bucle read_file→divagar→read_file y quema miles de tokens
                # sin escribir NUNCA. Con la cola en el prompt no hay excusa.
                try:
                    _tail = (run.workdir / _target_file).read_text(encoding="utf-8")[-1200:]
                    corr_trunc += (
                        f"\n\nAQUÍ ESTÁ EL FINAL ACTUAL DE «{_target_file}»"
                        " — continúa "
                        "EXACTAMENTE desde donde acaba (NO uses read_file, ya lo "
                        "tienes aquí; NO reescribas lo que existe):\n"
                        f"{_tail}\n"
                        f"Tu PRÓXIMA llamada es append_file con filepath="
                        f"\"{_target_file}\" y SOLO la siguiente "
                        "parte (≤150 líneas)."
                    )
                except OSError:
                    pass
                try:
                    yield from run_agent_turn(
                        run, run.start_agent, 1, corr_trunc,
                        flush=False, max_steps=12,
                        only_tools={"append_file", "read_file", "finalizar"})
                except Exception as exc:  # noqa: BLE001
                    run.transcript.append(
                        {"kind": "system",
                         "text": (f"⚠️ No se pudo completar el turno de completación "
                                  f"({type(exc).__name__}); el archivo rescatado se "
                                  f"conserva tal cual.")}
                    )
                    yield sse(SseEvent.system, {
                        "text": "⚠️ No se pudo completar el archivo rescatado "
                                "(modelo ocupado/lento); el archivo se conserva."
                    })
            # 🛟 v4.5 · CORRECCIÓN OBLIGATORIA (UNA sola vez): había código en
            # el texto pero el rescate no pudo guardar nada (p.ej. lenguaje
            # desconocido) → un turno correctivo que obliga a escribir archivos
            # de verdad con write_file/append_file.
            if (_rescue_needed_chat
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
        start_idx = AGENT_ORDER.index(run.start_agent)
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

        # 3) 🧩 Especialistas dinámicos (relé entre el contexto y el código):
        #    cada uno hereda plan + contexto (+ informe del anterior) y su
        #    output alimenta al siguiente. FLUSH DE VRAM por cada uno.
        for i, agent in enumerate(injected):
            prev = expert_reports[-1] if expert_reports else ""
            report = yield from run_agent_turn(  # type: ignore[misc]
                run, agent.id, 1, build_expert_prompt(run, agent, plan, context, prev)
            )
            if report and report.strip():
                expert_reports.append(report.strip())
            nxt = injected[i + 1].id if i + 1 < len(injected) else "developer"
            yield sse(SseEvent.delegate, {
                "from": agent.id, "to": nxt,
                "text": f"{agent.icon} → Delegando: informe de {agent.nombre} para la próxima fase",
            })

        # 4) 💻 Programador + 5) 🔍 Revisor (± Bucle Infinito de correcciones)
        feedback: Optional[str] = None
        dev_iter = 1
        # Si la cadena arranca en el Revisor no hay Programador que corrija: 1 ronda.
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
        run.transcript.append({"kind": "system", "text": f"❌ {type(exc).__name__}: {exc}"})
        _memory_finish(run, "error")
        # Sanitizar: no exponer paths internos ni traceback al cliente
        safe_msg = f"Error: {type(exc).__name__}"
        yield sse(SseEvent.task_error, {"message": safe_msg})
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


