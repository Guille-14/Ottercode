# OtterCode — constructores de prompts, extracción de JSON y herramientas
from __future__ import annotations
import io
import hmac
import json
import os
import queue
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import httpx
import requests
import tools
from fastapi import FastAPI, HTTPException, Request, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from events import SseEvent, sse, Route

from backend.config import (
    WORKSPACE_ROOT, REVIEWER_PER_FILE_LIMIT, REVIEWER_TOTAL_LIMIT, TOOL_RESULT_CONTEXT_LIMIT
)
from backend.runstate import OtterRun  # noqa: E402
from backend.config import REVIEWER_PER_FILE_LIMIT, REVIEWER_TOTAL_LIMIT, TOOL_RESULT_CONTEXT_LIMIT, WORKSPACE_ROOT  # noqa: E402
from backend.agents import Agent, DYNAMIC_AGENTS, _goal_block, get_agent  # noqa: E402
from backend.agents import (
    Agent, DYNAMIC_AGENTS, get_agent, _goal_block
)


# Extracción de JSON (skills y perfiles de la Fábrica)
# ---------------------------------------------------------------------------

_JSON_DECODER = json.JSONDecoder()
_FENCED_TOOL_RE = re.compile(r"```(?:json)?[ \t]*\r?\n?([{\[])", re.IGNORECASE)


def _coerce_tool_call(obj: Any) -> Optional[Dict[str, Any]]:
    """Normaliza CUALQUIER forma de llamada a herramienta a {tool, arguments}.

    Modelos reales emiten formatos dispares:
      - {tool, arguments}              → protocolo Otter (prompts)
      - {tool_name, parameters}        → Gemma/otros
      - {name, args} / {function:{name, arguments}}  → Function Calling nativo
      - argumentos como JSON string    → OpenRouter y variantes
    Un array (varias skills en una generación) se maneja en extract_tool_call.
    """
    if not isinstance(obj, dict):
        return None
    if isinstance(obj.get("function"), dict) and not obj.get("tool"):
        body = obj["function"]
    else:
        body = obj
    if not isinstance(body, dict):
        return None
    tool = (
        body.get("tool") or body.get("tool_name")
        or body.get("function_name") or body.get("name")
    )
    if not isinstance(tool, str) or not tool.strip() or len(tool.strip()) > 64:
        return None
    raw = body.get(
        "arguments",
        body.get("parameters", body.get("args", body.get("input"))),
    )
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}
    return {"tool": tool.strip(), "arguments": dict(raw)}


def _looks_like_tool(obj: Any) -> bool:
    return _coerce_tool_call(obj) is not None


def _tool_from_decoded(obj: Any) -> Optional[Dict[str, Any]]:
    """Primera llamada de herramienta válida de un objeto o lista decodificada."""
    items = obj if isinstance(obj, list) else [obj]
    for item in items:
        call = _coerce_tool_call(item)
        if call:
            return call
    return None


_HERMES_TOOL_RE = re.compile(
    r"<tool_call>\s*([\s\S]*?)</tool_call>|<function=([A-Za-z0-9_]+)>([\s\S]*?)</function>",
    re.IGNORECASE,
)


def _hermes_tool_call(text: str) -> Optional[Dict[str, Any]]:
    """Protocolo Hermes / Nous-Hermes: <tool_call> JSON o <function=name>."""
    t = text or ""
    for m in _HERMES_TOOL_RE.finditer(t):
        body, fname, fargs = m.group(1), m.group(2), m.group(3)
        if fname:
            args: Dict[str, Any] = {}
            raw = (fargs or "").strip()
            if raw.startswith("{"):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        args = parsed
                except Exception:
                    args = {}
            else:
                for line in raw.splitlines():
                    if "=" in line:
                        k, v = line.split("=", 1)
                        args[k.strip()] = v.strip().strip('"')
            if fname.strip():
                return {"tool": fname.strip(), "arguments": args}
        blob = (body or "").strip()
        if not blob:
            continue
        try:
            obj = json.loads(blob)
        except Exception:
            obj = {}
            idx = blob.find("{")
            if idx != -1:
                try:
                    obj, _ = _JSON_DECODER.raw_decode(blob[idx:])
                except json.JSONDecodeError:
                    obj = {}
        call = _coerce_tool_call(obj) if obj else None
        if call:
            return call
        if isinstance(obj, dict) and obj.get("name"):
            raw_a = obj.get("arguments") or obj.get("parameters") or {}
            if isinstance(raw_a, str):
                try:
                    raw_a = json.loads(raw_a)
                except Exception:
                    raw_a = {}
            if not isinstance(raw_a, dict):
                raw_a = {}
            return {"tool": str(obj["name"]).strip(), "arguments": raw_a}
    return None


def _looks_like_tool_attempt(text: str) -> bool:
    """Heurística: el agente intentó emitir un JSON/XML de skill pero no fue parseable."""
    t = text or ""
    if "<tool_call>" in t.lower() or "<function=" in t.lower():
        return True
    if not (re.search(r'"tool"\s*:', t) or re.search(r'"tool_name"\s*:', t)):
        return False
    return any(name in t for name in tools.TOOL_NAMES + ("finalizar",))


def extract_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Primer objeto JSON válido (bloques ```json o escaneo raw_decode).

    Lo usa la Fábrica para parsear perfiles de agente (cualquier dict).
    """
    for match in _FENCED_TOOL_RE.finditer(text):
        start = match.end() - 1
        try:
            obj, _ = _JSON_DECODER.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj:
            return obj
    idx = text.find("{")
    while idx != -1:
        try:
            obj, end = _JSON_DECODER.raw_decode(text[idx:])
        except json.JSONDecodeError:
            idx = text.find("{", idx + 1)
            continue
        if isinstance(obj, dict) and obj:
            return obj
        idx = text.find("{", idx + max(1, end))
    return None


def extract_tool_call(text: str) -> Optional[Dict[str, Any]]:
    """Encuentra la primera llamada de herramienta válida (objeto O array).

    Robustez: primero Hermes/XML, luego bloques ```json y raw_decode.
    Devuelve SIEMPRE {tool, arguments}.
    """
    hermes = _hermes_tool_call(text or "")
    if hermes:
        return hermes
    for match in _FENCED_TOOL_RE.finditer(text):
        start = match.end() - 1
        try:
            obj, _ = _JSON_DECODER.raw_decode(text[start:])
        except json.JSONDecodeError:
            continue
        call = _tool_from_decoded(obj)
        if call:
            return call
    idx = text.find("{")
    while idx != -1:
        try:
            obj, end = _JSON_DECODER.raw_decode(text[idx:])
        except json.JSONDecodeError:
            idx = text.find("{", idx + 1)
            continue
        call = _tool_from_decoded(obj)
        if call:
            return call
        idx = text.find("{", idx + max(1, end))
    idx = text.find("[")
    while idx != -1:
        try:
            obj, end = _JSON_DECODER.raw_decode(text[idx:])
        except json.JSONDecodeError:
            idx = text.find("[", idx + 1)
            continue
        call = _tool_from_decoded(obj)
        if call:
            return call
        idx = text.find("[", idx + max(1, end))
    return None


_FILE_TOOLS = {"read_file", "write_file", "append_file", "edit_file"}
_BASH_TOOLS = {"execute_bash"}


def alias_args(tool: str, args: Dict[str, Any]) -> Dict[str, Any]:
    """Traduce los nombres de argumento que usan los modelos (path/command/…)
    a los canónicos del ejecutor (filepath/cmd/…)."""
    args = dict(args or {})
    if tool in _FILE_TOOLS and "filepath" not in args and "path" in args:
        args["filepath"] = args["path"]
    elif tool in _BASH_TOOLS and "cmd" not in args and "command" in args:
        args["cmd"] = args["command"]
    return args


def extract_injections(plan: str) -> Tuple[List[str], str]:
    """Extrae la petición de inyección del Arquitecto:
    {"inject_agents": ["dyn-xxxx"], "reason": "..."}. Robusto a ruido:
    regex sobre el campo (el modelo a veces envuelve el JSON con texto).
    """
    m = re.search(r'"inject_agents"\s*:\s*\[([^\]]*)\]', plan)
    if not m:
        return [], ""
    ids = re.findall(r'["\']([A-Za-z0-9\-]{3,40})["\']', m.group(1))
    reason = ""
    rm = re.search(r'"reason"\s*:\s*"([^"]*)"', plan[m.start():])
    if rm:
        reason = rm.group(1)
    return ids, reason


# ---------------------------------------------------------------------------
# Constructores de prompt (handoff: el output de A es el contexto de B)
# ---------------------------------------------------------------------------

def build_dynamic_roster() -> str:
    """Lista de agentes dinámicos disponibles para el prompt del Arquitecto."""
    if not DYNAMIC_AGENTS:
        return "Ninguno disponible (no se han creado agentes dinámicos en esta balsa)."
    return "\n".join(
        f"- {a.id} :: {a.nombre} — {a.rol}" for a in DYNAMIC_AGENTS.values()
    )


def build_architect_prompt(task_text: str, roster: Optional[str] = None,
                           memory: str = "", goal: str = "") -> str:
    parts = [
        f"MISIÓN DEL EQUIPO (recibida del usuario):\n{task_text}",
        "La misión es EXACTAMENTE lo que pidió el usuario. No la sustituyas "
        "por un demo, una web de nutrias, ni un proyecto de marca OtterCode.",
        f"[AGENTES DINÁMICOS DISPONIBLES]\n{roster if roster is not None else build_dynamic_roster()}",
    ]
    goal_block = _goal_block(goal)
    # 🧠 Memoria persistente del cerebro Obsidian (v3.3): contexto de misiones
    # anteriores y del perfil del usuario, para que la balsa "sepa más de ti".
    if memory:
        parts.append(
            "# MEMORIA PERSISTENTE (tu cerebro Obsidian)\n"
            "Recuerda esto de misiones y conversaciones anteriores con el "
            "usuario; úsalo para ser coherente con sus preferencias y trabajo "
            "previo (NO lo menciones literalmente salvo que sea relevante):\n"
            + memory
        )
    if goal_block:
        parts.append(goal_block)
    parts.append(
        "Analiza la misión, divide las subtareas y delega. Recuerda: "
        "prohibido escribir código."
    )
    if DYNAMIC_AGENTS:
        parts.append(
            "META-ORQUESTACIÓN: si la misión lo justifica, puedes INYECTAR a uno "
            "o varios de los agentes dinámicos disponibles en la cadena: entrarán "
            "entre la investigación y la implementación, y su informe pasará al "
            "Programador. Para inyectar, al FINAL de tu plan añade UN bloque "
            "```json con: {\"inject_agents\": [\"<id>\"], \"reason\": \"<por qué>\"}. "
            "Si no los necesitas, NO emitas ese bloque."
        )
    return "\n\n".join(parts)


def build_researcher_prompt(task_text: str, plan: str, goal: str = "") -> str:
    parts = [
        "# INVESTIGACIÓN DE CONTEXTO — BALSA OTTERCODE",
        f"[MISIÓN DEL USUARIO]\n{task_text}",
    ]
    if goal:
        parts.append(_goal_block(goal))
    if plan:
        parts.append(f"[PLAN Y DELEGACIÓN DEL ARQUITECTO]\n{plan}")
    parts.append(
        "FASE DE CONTEXTO (NO de implementación): tu único trabajo es explorar "
        "el workspace (tree, list_dir, read_file) y producir el INFORME DE "
        "CONTEXTO. ESTÁ PROHIBIDO: escribir archivos (no tienes write_file ni "
        "append_file: te serán DENEGADOS), generar el código de la solución, "
        "ni 'hacer tú la tarea'. El Programador construye: tú solo informas. "
        "Cuando tengas el informe, emite finalizar con él como resumen."
    )
    return "\n\n".join(parts)


def build_expert_prompt(run: "OtterRun", agent: Agent, plan: str, context: str,
                        prev_report: str) -> str:
    """Prompt de un agente DINÁMICO inyectado en la cadena (tras el contexto)."""
    parts = [
        f"# COMISIÓN DEL ESPECIALISTA — {agent.nombre.upper()} — BALSA OTTERCODE",
        f"Eres {agent.nombre}, {agent.rol}. El Arquitecto te ha inyectado en esta "
        "misión: trabaja tu especialidad con tus skills y entrega un informe "
        "claro (y archivos, si tienes write_file) que pasará al Programador.",
        f"[MISIÓN DEL USUARIO]\n{run.task_text}",
    ]
    if plan:
        parts.append(f"[PLAN DEL ARQUITECTO]\n{plan}")
    if context:
        parts.append(f"[INFORME DE CONTEXTO DEL INVESTIGADOR]\n{context}")
    if prev_report:
        parts.append(f"[INFORME DEL ESPECIALISTA ANTERIOR]\n{prev_report}")
    parts.append(
        "Instrucciones: empieza emitiendo el JSON de la primera skill. Cuando "
        "tengas listo tu informe, emite finalizar con el resumen que pasará al "
        "Programador."
    )
    return "\n\n".join(parts)


def build_developer_prompt(
    task_text: str, plan: str, context: str, feedback: Optional[str],
    expert_reports: Optional[List[str]] = None, goal: str = "",
) -> str:
    if feedback:
        parts = [
            "# MODO CORRECCIÓN — BALSA OTTERCODE",
            "El Revisor RECHAZÓ la implementación: te devuelve SOLO los fragmentos "
            "corregidos (y el resultado de sus tests). Aplica las correcciones con "
            "write_file (contenido COMPLETO del archivo afectado). No regeneres "
            "archivos ajenos a las correcciones.",
            f"[MISIÓN DEL USUARIO]\n{task_text}",
        ]
        if goal:
            parts.append(_goal_block(goal))
        if plan:
            parts.append(f"[PLAN DEL ARQUITECTO]\n{plan}")
        parts.append(f"[CORRECCIONES DEL REVISOR]\n{feedback}")
        parts.append("Empieza emitiendo el JSON de la primera skill.")
        return "\n\n".join(parts)
    parts = [
        "# MISIÓN — BALSA OTTERCODE",
        "Eres el Programador de la balsa. Implementa la misión escribiendo los "
        "archivos con write_file (usa mkdir para estructura y tree/list_dir si "
        "necesitas revisar el estado). El tema lo marca el usuario: si pidió "
        "Hermes Agent, NO hagas una web de nutrias ni un demo de OtterCode.",
        f"[MISIÓN DEL USUARIO]\n{task_text}",
    ]
    if goal:
        parts.append(_goal_block(goal))
    if plan:
        parts.append(f"[PLAN DEL ARQUITECTO]\n{plan}")
    if context:
        parts.append(f"[INFORME DE CONTEXTO DEL INVESTIGADOR]\n{context}")
    for i, report in enumerate(expert_reports or [], start=1):
        parts.append(f"[INFORME DEL ESPECIALISTA {i} (inyectado por el Arquitecto)]\n{report}")
    parts.append(
        "Instrucciones: empieza emitiendo el JSON de la primera skill "
        "(p. ej. tree, o write_file del primer archivo). Cuando todo esté "
        "escrito, emite finalizar."
    )
    return "\n\n".join(parts)


def build_reviewer_prompt(run: "OtterRun", plan: str) -> str:
    """No volcar el workspace entero: eso llena n_ctx (48k vs 18k)."""
    inventory: List[str] = []
    try:
        for f in run.executor.list_workspace()[:24]:
            p = f.get("path") if isinstance(f, dict) else str(f)
            sz = f.get("size") if isinstance(f, dict) else 0
            if not p or str(p).startswith("."):
                continue
            inventory.append(f"- {p} ({sz} B)")
    except Exception:
        pass
    parts = [
        "# AUDITORÍA — BALSA OTTERCODE",
        f"[MISIÓN DEL USUARIO]\n{(run.task_text or '')[:800]}",
    ]
    if getattr(run, "goal", ""):
        parts.append(_goal_block(run.goal))
    if plan:
        parts.append(f"[PLAN DEL ARQUITECTO]\n{plan[:1500]}")
    parts.append(
        "[ARCHIVOS EN DISCO — lee con read_file lo que necesites; "
        "NO se pega el código completo aquí]\n"
        + ("\n".join(inventory) if inventory else "(vacío)")
    )
    parts.append(
        "Inspecciona con tree/read_file (trozos). Dictamina breve. "
        "No copies archivos enteros en tu respuesta."
    )
    return "\n\n".join(parts)


def _workspace_inventory(run: "OtterRun", limit: int = 32) -> str:
    lines: List[str] = []
    try:
        for f in run.executor.list_workspace()[:limit]:
            p = f.get("path") if isinstance(f, dict) else str(f)
            sz = f.get("size") if isinstance(f, dict) else 0
            if not p or str(p).startswith("."):
                continue
            lines.append(f"- {p} ({sz} B)")
    except Exception:
        pass
    return "\n".join(lines) if lines else "(workspace vacío)"


def _workspace_inventory_lines(run: "OtterRun", limit: int = 32) -> str:
    return _workspace_inventory(run, limit=limit)


def build_chat_prompt(run: "OtterRun", task_text: Optional[str] = None) -> str:
    start = get_agent(run.start_agent)
    txt = task_text if task_text is not None else run.task_text
    hdr = ("# AGENTE OTTER — MODO CLAUDE CODE" if run.start_agent == "agent"
           else "# CHAT DIRECTO — BALSA OTTERCODE")
    parts = [
        hdr,
        f"Estás en modo directo: el usuario te habla a ti, {start.nombre}.",
        "Responde de forma directa y útil. Si necesitas inspeccionar o modificar el "
        "workspace, usa tus skills.",
        f"USUARIO:\n{txt}",
    ]
    inv_always = _workspace_inventory(run)
    if inv_always and inv_always != "(workspace vacío)":
        parts.append(
            "# ARCHIVOS EN DISCO (YA EXISTEN: edítalos, no los recrees)\n"
            f"{inv_always}\n"
            "write_file SOLO si el path no existe. Si existe: edit_file/append_file."
        )
    if getattr(run, "continue_task", ""):
        inv = _workspace_inventory(run)
        orig = ""
        try:
            meta_path = WORKSPACE_ROOT / str(run.continue_task) / "ottercode_transcript.json"
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                orig = str((data.get("meta") or {}).get("task") or "")[:800]
        except Exception:
            orig = ""
        tails: List[str] = []
        try:
            for f in run.executor.list_workspace()[:12]:
                p = str(f.get("path") if isinstance(f, dict) else f)
                if not p or p.startswith(".") or not p.lower().endswith(
                    (".html", ".htm", ".js", ".css", ".py", ".md")
                ):
                    continue
                try:
                    body = (run.workdir / p).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if len(body) < 40:
                    continue
                tails.append(f"--- cola de {p} ({len(body)} chars) ---\n{body[-900:]}")
        except Exception:
            pass
        parts.append(
            "# HILO CONTINUADO — EDITA, NO REGENERES\n"
            "Los archivos YA EXISTEN. PROHIBIDO write_file sobre ellos "
            "(borra el trabajo y gasta tokens). "
            "• Línea mal / trozo concreto → edit_file (old_string exacto).\n"
            "• Añadir al final / seguir el HTML → append_file (≤150 líneas).\n"
            "• Añadir al principio → edit_file del bloque inicial, no reescribir todo.\n"
            "NO crees index.html nuevo ni una web de nutrias.\n"
            + (f"MISIÓN ORIGINAL:\n{orig}\n" if orig else "")
            + f"ARCHIVOS EN DISCO:\n{inv}\n"
            + ("\n".join(tails) if tails else "")
        )
    if getattr(run, "memory_block", ""):
        parts.append(
            "# MEMORIA PERSISTENTE (tu cerebro Obsidian)\n"
            + run.memory_block
        )
    if getattr(run, "goal", ""):
        parts.append(_goal_block(run.goal))
    return "\n\n".join(parts)


def _prev_conversation_block(task_id: str, max_entries: int = 16,
                             max_chars: int = 700) -> str:
    """🧵 Continuidad: el JSON en disco es {meta, transcript}, no una lista."""
    path = WORKSPACE_ROOT / str(task_id) / "ottercode_transcript.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return ""
    meta: Dict[str, Any] = {}
    entries: List[Any] = []
    if isinstance(data, dict):
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        raw = data.get("transcript")
        entries = raw if isinstance(raw, list) else []
    elif isinstance(data, list):
        entries = data
    else:
        return ""
    conv = [e for e in entries
            if isinstance(e, dict) and e.get("kind") in ("user", "agent")]
    lines = ["# CONVERSACIÓN PREVIA CON ESTE USUARIO "
             "(mismo proyecto; los archivos ya están en tu workspace)"]
    orig = str(meta.get("task") or "").strip()
    if orig:
        lines.append(f"<MISIÓN ORIGINAL>: {orig[:800]}")
    if not conv and not orig:
        return ""
    for e in conv[-max_entries:]:
        who = "<USUARIO>" if e.get("kind") == "user" else "<OTTER>"
        txt = str(e.get("text") or e.get("content") or "").strip()
        if not txt:
            continue
        if len(txt) > max_chars:
            txt = txt[:max_chars] + "…"
        lines.append(f"{who}: {txt}")
    lines.append(
        "El usuario continúa: itera sobre lo que ya hay. "
        "no empieces de cero. NO inventes una web de nutrias ni un demo."
    )
    return "\n".join(lines)


def build_continue_prompt(agent_id: str, seed: str, entries: List[Tuple[str, str]]) -> str:
    label = get_agent(agent_id).nombre.upper()
    parts = [
        f"# CONTINUACIÓN DE TU TURNO — {label} — BALSA OTTERCODE",
        seed,
    ]
    for i, (model_output, tool_result) in enumerate(entries, start=1):
        parts.append(f"--- PASO {i}: TU SALIDA ANTERIOR ---\n{model_output}")
        parts.append(f"--- PASO {i}: RESULTADO DE LA SKILL ---\n{tool_result}")
    parts.append(
        "CONTINÚA. Emite el JSON de la siguiente skill, o 'finalizar' si tu "
        "objetivo está completo."
    )
    return "\n\n".join(parts)


def format_tool_result(tool_name: str, result: Dict[str, Any]) -> str:
    head = f"RESULTADO {tool_name} → {'OK' if result['ok'] else 'ERROR'}"
    return head + "\n" + result["output"][:TOOL_RESULT_CONTEXT_LIMIT]


def is_approved(verdict: str) -> bool:
    cleaned = re.sub(r"[`'\"*\[\]()!_.\s]+", "", verdict.lower())
    return cleaned == "aprobado"


# ---------------------------------------------------------------------------
# Estado de una ejecución
