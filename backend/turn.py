# Turno con skills: run_agent_turn y nativo.
from __future__ import annotations

import json
import subprocess
import time
import os
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, Iterator, Optional, List

import tools
from events import SseEvent, sse
from backend.config import HACKER_SUFFIX, MAX_TOOL_STEPS, NUM_CTX_DEFAULT, native_tools_enabled
from backend.agents import (
    ULTRAREVIEW_SUFFIX, _CORE_BASE_PROMPTS, _agent_meta, get_agent,
    skill_enabled, tool_protocol,
)
from backend.prompts import alias_args, extract_tool_call, format_tool_result, _looks_like_tool_attempt
from backend.ollama import ContextOverflow, flush_vram, stream_llm
from backend.runstate import OtterRun, append_checkpoint, note_stall
from backend.runstate import AbortRequested
from backend.runtime import _activity_set
from backend.memory import get_memory, harvest_memory
from backend.engine import (
    PENDING_PERMISSIONS, PERMISSION_RESPONSES, _needs_permission,
    _maybe_compact_messages, _preflight_eval,
    _hard_trim_messages, _strip_think, _salvage_cut_json, _salvage_before_finalize,
    _write_size_guard, _invalid_json_feedback,
    _should_flush_vram, _mark_gpu_model, _set_step,
    _WRITE_TOOLS, _TODO_IDLE_MAX,
)
import backend.config as _otter_cfg
import backend.profiles as _otter_profiles

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


def json_extract_allowed(native_on: bool) -> bool:
    """R1: native XOR JSON. Si native está ON, extract_tool_call no se usa."""
    return not native_on


def _finalize_needs_verify(run: Any) -> Optional[str]:
    used = set(getattr(run, "_turn_tools", set()) or set())
    writes = used & {"write_file", "append_file", "edit_file", "apply_patch"}
    if not writes:
        return None
    if "execute_bash" not in used and "python_exec" not in used:
        return (
            "no has verificado; corre execute_bash "
            "(py_compile / node --check / test existente) y luego finalizar"
        )
    return None


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

    name = tools.resolve_name(name)
    agent = get_agent(agent_id)
    allowed = [t for t in agent.tools_disponibles if skill_enabled(t)]
    extra = getattr(run, "_native_allowed", None)
    if extra:
        allowed = [t for t in allowed if t in extra]
    if name != "finalizar" and name not in allowed:
        msg = (
            f"ACCESO DENEGADO: '{name}' no está en tu lista de skills. "
            f"Disponibles: {', '.join(allowed + ['finalizar'])}."
        )
        if agent.readonly and name in _WRITE_TOOLS:
            msg = (
                f"ACCESO DENEGADO: eres SOLO-LECTURA y '{name}' escribe archivos."
            )
        yield {"kind": "result", "name": name, "ok": False, "output": msg}
        return {"ok": False, "output": msg}
    if not skill_enabled(name) and name != "finalizar":
        msg = f"Skill '{name}' deshabilitada."
        yield {"kind": "result", "name": name, "ok": False, "output": msg}
        return {"ok": False, "output": msg}
    args = alias_args(name, args)
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
    if name == "finalizar":
        blocked = _finalize_needs_verify(run)
        if blocked:
            yield {"kind": "result", "name": name, "ok": False, "output": blocked}
            return {"ok": False, "output": blocked}
        resumen = str(args.get("resumen", "") or "finalizado")
        yield {"kind": "result", "name": name, "ok": True, "output": resumen}
        return {"ok": True, "output": resumen}

    if name == "write_file":
        fp = str(args.get("filepath") or args.get("path") or "")
        content = str(args.get("content") or "")
        if fp and not _write_size_guard(run, fp, content):
            msg = f"RECHAZADO: no sobrescribas {fp} con una versión menor. Usa edit_file/append_file."
            yield {"kind": "result", "name": name, "ok": False, "output": msg}
            return {"ok": False, "output": msg}

    res = executor.dispatch(name, args)
    out = res.get("output", "")
    try:
        out = format_tool_result(name, res)
    except Exception:
        pass
    yield {"kind": "result", "name": name, "ok": res.get("ok", False), "output": out}
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
    run._native_allowed = list(allowed)
    _native_on = native_tools_enabled(run.model)
    # Regenera el protocolo de skills según los toggles activos (core agents)
    base = _CORE_BASE_PROMPTS.get(agent.id)
    # FASE 3 · Si el perfil activo tiene system_override, usarlo como base
    profile_override = _otter_profiles._ACTIVE_PROFILE.get("system_override", "")
    if profile_override:
        system_prompt = profile_override + "\n\n" + tool_protocol(allowed, native=_native_on)
    else:
        system_prompt = (
            base + tool_protocol(allowed, native=_native_on) if base else agent.system_prompt
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
    try:
        from backend.user_profile import inject_profile_block
        from backend.config import WORKSPACE_ROOT as _WR
        _pj = inject_profile_block(_WR)
        if _pj:
            system_prompt = system_prompt + _pj
    except Exception:
        pass
    
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
    if _should_flush_vram(run, flush):
        flush_info = flush_vram(run.model)
        yield sse(SseEvent.vram_flush, {"agent": agent_id, "iteration": iteration, **flush_info})
    _mark_gpu_model(run.model)

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
            try:
                from backend.ctx_bench import compact_pressure_hint
                _hint = compact_pressure_hint(run)
                if _hint:
                    yield sse(SseEvent.ctx_hint, _hint)
            except Exception:
                pass
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
        except RuntimeError as _rte:
            from backend.ollama import _truncated_tool_name
            _tn = _truncated_tool_name(str(_rte))
            if not _tn:
                raise
            last_text, _stats = "", {"_truncated_tool": _tn, "_truncated_err": str(_rte)[:400]}
        # v3.4 · el parsing y el historial usan el texto SIN bloques <think>
        parse_text = _strip_think(last_text)
        trunc_tool = str((_stats or {}).get("_truncated_tool") or "")
        if trunc_tool:
            fb = (
                f"ERROR: la llamada nativa a '{trunc_tool}' se CORTÓ "
                "(JSON de arguments inválido / unexpected end of JSON). "
                "PROHIBIDO reenviar el archivo entero con write_file. "
                "1) read_file del trozo a cambiar. "
                "2) edit_file con old_string CORTO (1-8 líneas exactas) + new_string. "
                "Una sección por llamada. Archivo nuevo pequeño: write_file ≤80 líneas."
            )
            yield sse(SseEvent.system, {
                "text": f"⚠️ {trunc_tool}: JSON de tool cortado. Pidiendo edit_file…",
            })
            run.messages.append({"role": "user", "content": fb})
            continue
        # FASE 5 · Native Function Calling
        if native_tools_enabled(run.model):
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
                            if event.get("ok") and event["name"] in ("write_file", "edit_file", "apply_patch", "append_file"):
                                _nfp = str((event.get("args") or {}).get("filepath") or (call.get("function") or {}).get("arguments") or "")
                                _args_n = event.get("args") if isinstance(event.get("args"), dict) else {}
                                _nfp = str(_args_n.get("filepath") or _args_n.get("path") or "")
                                if _nfp:
                                    yield sse(SseEvent.file_updated, {
                                        "path": _nfp, "tool": event["name"],
                                        "version": int(time.time() * 1000),
                                    })
                            if event.get("ok") and event["name"] in ("write_file", "edit_file", "apply_patch") and "```diff" in str(event.get("output") or ""):
                                yield sse(SseEvent.diff, {"path": "", "diff": event["output"], "tool": event["name"]})
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
                            if event["name"] == "finalizar":
                                run._native_done = True
                            if (event.get("ok") and event["name"] == "todo_write"
                                    and getattr(run, "plan_gate", False)
                                    and not getattr(run, "plan_approved", False)):
                                n_items = 0
                                try:
                                    _td = json.loads((Path(run.workdir) / ".otter_todo.json").read_text(encoding="utf-8"))
                                    n_items = len(_td) if isinstance(_td, list) else 0
                                except Exception:
                                    n_items = 0
                                if n_items >= 2:
                                    run._awaiting_plan = True
                                    run._native_done = True
                                    yield sse(SseEvent.system, {
                                        "text": "📋 Plan listo. Escribe /apply, /reject o feedback. Aún no se ha escrito código.",
                                    })
                # Native FC ya ejecutó las tools: no reparsear JSON/Hermes (doble dispatch).
                if getattr(run, "_native_done", False):
                    break
                continue
            # Native ON y sin tool_calls: XOR — no extraer JSON.
            run.transcript.append(
                {"kind": "agent", "agent": agent_id, "iteration": iteration, "text": parse_text}
            )
            run.messages.append({"role": "assistant", "content": parse_text})
            break

        run.transcript.append(
            {"kind": "agent", "agent": agent_id, "iteration": iteration, "text": parse_text}
        )
        run.messages.append({"role": "assistant", "content": parse_text})

        if not json_extract_allowed(_native_on):
            break
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
                        run._turn_salvaged_truncated = True
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
            blocked = _finalize_needs_verify(run)
            if blocked:
                yield sse(SseEvent.tool_call, {
                    "id": call_id, "agent": agent_id, "iteration": iteration,
                    "tool": tool_name, "args": args, "title": title,
                })
                yield sse(SseEvent.tool_result, {
                    "id": call_id, "tool": tool_name, "ok": False,
                    "output": blocked, "ms": 0,
                })
                run.messages.append({"role": "user", "content": blocked})
                continue
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
            if getattr(run, "plan_gate", False) and not getattr(run, "plan_approved", False):
                n_items = 0
                try:
                    _td = json.loads((Path(run.workdir) / ".otter_todo.json").read_text(encoding="utf-8"))
                    n_items = len(_td) if isinstance(_td, list) else 0
                except Exception:
                    n_items = 0
                if n_items >= 2:
                    run._awaiting_plan = True
                    yield sse(SseEvent.system, {
                        "text": "📋 Plan listo. Escribe /apply, /reject o feedback para editarlo. "
                                "Aún no se ha escrito código.",
                    })
                    break
        if result.get("ok") and tool_name in ("write_file", "edit_file", "apply_patch"):
            _out = str(result.get("output") or "")
            if "```diff" in _out:
                yield sse(SseEvent.diff, {
                    "path": str(args.get("filepath") or args.get("path") or ""),
                    "diff": _out,
                    "tool": tool_name,
                })
        if result.get("ok") and tool_name in ("write_file", "append_file", "edit_file", "apply_patch"):
            _fp = str(args.get("filepath") or args.get("path") or "")
            if _fp:
                yield sse(SseEvent.file_updated, {
                    "path": _fp,
                    "tool": tool_name,
                    "version": int(time.time() * 1000),
                })
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

