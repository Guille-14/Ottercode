# Pipeline de misión: run_task_stream.
from __future__ import annotations

import os
import re
import time
from typing import Any, Iterator, List, Optional

from events import SseEvent, sse
from backend.config import MAX_INJECTIONS
from backend.agents import AGENT_ORDER, Agent, DYNAMIC_AGENTS, get_agent
from backend.prompts import (
    build_architect_prompt, build_chat_prompt, build_developer_prompt,
    build_expert_prompt, build_researcher_prompt, build_reviewer_prompt,
    extract_injections, is_approved, _prev_conversation_block,
)
from backend.runstate import OtterRun, mark_checkpoint_closed
from backend.history import save_session
from backend.db import save_session_to_db
from backend.runstate import AbortRequested
from backend.runtime import _activity_finish
from backend.vault import _memory_finish
from backend.engine import (
    _WRITE_TOOLS, _EXPLORE_TOOLS, RescueStalled,
    _log_uncaught, _set_step, _should_flush_vram, _mark_gpu_model,
    _preflight_eval, _maybe_compact_messages,
)
from backend.turn import run_agent_turn
from backend.rescue import (
    _should_rescue, _rescue_code_from_text, _complete_rescued_file,
)
from backend.agents import _agent_meta
from backend.ollama import ContextOverflow, flush_vram, stream_llm
from backend.memory import harvest_memory
from backend.config import HACKER_SUFFIX
from backend.runtime import _activity_set
import backend.config as _otter_cfg
import backend.profiles as _otter_profiles

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
    if _should_flush_vram(run, True):
        flush = flush_vram(run.model)
        yield sse(SseEvent.vram_flush, {"agent": agent_id, "iteration": iteration, **flush})
        if not flush["ok"]:
            yield sse(SseEvent.system, {
                "text": f"⚠️ VRAM flush no confirmado ({flush.get('error', flush.get('detail'))})"
            })
    _mark_gpu_model(run.model)
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
                     "text": "📋 ULTRA PLAN listo: esperando aprobación del usuario."}
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
                                    "No se pudo completar; se pasa al Revisor."
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
