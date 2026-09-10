# Rescate de código pegado / JSON cortado.
from __future__ import annotations

import os
import re
import time
import uuid
from typing import Any, Iterator, List, Optional, Tuple

from events import SseEvent, sse
from backend.runstate import OtterRun, note_stall
from backend.runstate import AbortRequested
from backend.runtime import _activity_set
from backend.engine import (
    RescueStalled, _WRITE_TOOLS, _write_size_guard, _set_step, _log_uncaught,
    _strip_think, _RESCUE_MAX_TRIES,
)
from backend.turn import run_agent_turn

_RESCUE_FENCE_RE = re.compile(r"```([\w+-]*)\n?([\s\S]*?)```")
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

