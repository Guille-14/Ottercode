import json
import os
from pathlib import Path
import threading
import traceback
import uuid
import tools
import re
import time
import subprocess
import requests
from typing import Iterator, Optional, Dict, Any, List, Tuple

# FASE 5 · Gate de permisos para herramientas destructivas
PENDING_PERMISSIONS: Dict[str, threading.Event] = {}
PERMISSION_RESPONSES: Dict[str, bool] = {}
ASK_PERMISSIONS = os.environ.get("OTTERCODE_ASK_PERMISSIONS", "1").strip().lower() not in ("0", "false", "no")
_DANGEROUS_TOOLS = {"python_exec", "execute_bash"}
_WRITE_FS_TOOLS = {"write_file", "append_file", "edit_file", "apply_patch", "mkdir", "git_commit"}


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

from backend.runstate import _condense_entries, append_checkpoint  # noqa: E402
from backend.ollama import _estimate_tokens, _ollama_ndjson_text  # noqa: E402
from backend.config import FLUSH_EVERY_TURN, LLM_BACKEND, NUM_CTX_DEFAULT, NUM_PREDICT_DEFAULT, OLLAMA_BASE_URL  # noqa: E402
from backend.agents import _chat_base  # noqa: E402




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

_LAST_GPU_MODEL = ""


def _should_flush_vram(run: Any, flush: bool) -> bool:
    """Flush solo al cambiar de modelo, si FLUSH_EVERY_TURN=1, o si el caller no pide skip."""
    global _LAST_GPU_MODEL
    if not flush:
        return False
    if FLUSH_EVERY_TURN:
        return True
    model = getattr(run, "model", "") or ""
    return (not _LAST_GPU_MODEL) or (_LAST_GPU_MODEL != model)


def _mark_gpu_model(model: str) -> None:
    global _LAST_GPU_MODEL
    _LAST_GPU_MODEL = model or _LAST_GPU_MODEL




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


_WRITE_TOOLS = {"write_file", "append_file", "edit_file", "apply_patch", "mkdir", "python_exec", "git_commit"}
_EXPLORE_TOOLS = {"tree", "list_dir", "read_file", "grep_search", "glob_files",
                  "web_search", "web_fetch", "wikipedia_search",
                  "execute_bash", "git_status"}

# Reexportes: el paquete y selftest siguen importando desde backend.engine
from backend.turn import run_agent_turn, _run_native_tool, _pending_todo_items, _save_partial_on_abort, _thermal_ease  # noqa: E402
from backend.rescue import (  # noqa: E402
    _should_rescue, _rescue_code_from_text, _complete_rescued_file,
    _append_continuation, _code_candidates, _code_blob_from_text,
)
from backend.loop import run_task_stream, _forced_step_prompt, _plan_has_code  # noqa: E402
