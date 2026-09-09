# OtterCode — registro de agentes (núcleo + escuadrón preset + skills config)
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
    OLLAMA_BASE_URL, DEFAULT_MODEL, LLM_BACKEND, native_tools_enabled
)
from backend.config import LLM_BACKEND, OLLAMA_BASE_URL  # noqa: E402


def _chat_base() -> str:
    """Base de chat según el transporte (OpenAI-compat añade /v1)."""
    if LLM_BACKEND == "openai":
        b = OLLAMA_BASE_URL.rstrip("/")
        return b if b.endswith("/v1") else b + "/v1"
    return OLLAMA_BASE_URL

# ---------------------------------------------------------------------------
# Estructura de agentes DINÁMICOS (meta-orquestación)
# ---------------------------------------------------------------------------

@dataclass
class Agent:
    """Agente mutable de la balsa: núcleo de la meta-orquestación.

    Los 4 agentes core son instancias de esta misma clase (dynamic=False);
    los creados al vuelo por la Fábrica son instancias dynamic=True que
    viven en memoria durante la vida del proceso.
    """
    id: str
    nombre: str
    rol: str
    system_prompt: str
    icon: str
    color_neon: str
    tools_disponibles: List[str] = field(default_factory=list)
    dynamic: bool = False
    created_in: Optional[str] = None      # sesión donde se creó (auditoría)
    readonly: bool = False                # ejecutor solo lectura (sin writes_fs)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "nombre": self.nombre,
            "rol": self.rol,
            "system_prompt": self.system_prompt,
            "icon": self.icon,
            "color_neon": self.color_neon,
            "tools_disponibles": list(self.tools_disponibles),
            "dynamic": self.dynamic,
            "created_in": self.created_in,
            "readonly": self.readonly,
        }


def _agent_meta(agent: Agent) -> Dict[str, Any]:
    """Meta de UI para los eventos SSE (agent_start, vram_flush…)."""
    meta = {"name": agent.nombre, "icon": agent.icon, "role": agent.rol}
    if agent.dynamic:
        meta["color_neon"] = agent.color_neon
        meta["dynamic"] = True
    return meta


# ---------------------------------------------------------------------------
# System Prompts (restricciones negativas estrictas)
# ---------------------------------------------------------------------------

BASE_ARCHITECT: str = (
    "Eres el Arquitecto, el AGENTE PRINCIPAL de la balsa OtterCode. El usuario "
    "interactúa contigo: tu ÚNICO trabajo es analizar la petición, dividirla en "
    "subtareas y DELEGAR secuencialemente a los sub-agentes: 🔬 Investigador "
    "(contexto del proyecto), 💻 Programador (implementación) y 🔍 Revisor "
    "(auditoría y tests). REGLA DE HIERRO: TIENES ESTRICTAMENTE PROHIBIDO "
    "ESCRIBIR CÓDIGO (ni HTML, JS, Python, etc.) ni usar herramientas. Si escribes "
    "código, fracasarás. Tu salida es exclusivamente texto y listas.\n\n"
    "PROHIBICIÓN ABSOLUTA DE BLOQUES DE CÓDIGO: en tu respuesta NO puede aparecer "
    "NI UN SOLO bloque ``` ni fragmentos de HTML/CSS/JS/SQL/Python (ni siquiera "
    "ejemplos). El Programador es quien escribe el código: tú solo describes QUÉ "
    "construir, archivo por archivo, en lenguaje natural. Si te ves escribiendo "
    "etiquetas como <html>, def función, o selectores CSS, PARA: eso es trabajo "
    "del Programador, no tuyo.\n\n"
    "FORMATO OBLIGATORIO DE LA SALIDA (máximo 40 líneas en total):\n"
    "## Objetivo\n<una línea>\n"
    "## Subtareas y delegación\n"
    "1. 🔬 Investigador: <qué debe descubrir del workspace>\n"
    "2. 💻 Programador: <qué debe implementar, archivo por archivo, SIN código>\n"
    "3. 🔍 Revisor: <qué debe auditar y qué tests ejecutar>\n"
    "## Plan de acción\n1. <paso>\n2. <paso>\n"
)

BASE_RESEARCHER: str = (
    "Eres el Investigador de la balsa OtterCode. Recibes la misión y el plan del "
    "Arquitecto. Tu ÚNICO trabajo es analizar el CONTEXTO del workspace: árbol de "
    "directorios, archivos existentes y estructura del proyecto, y producir el "
    "INFORME DE CONTEXTO que será la entrada del Programador. REGLA DE HIERRO: NO "
    "escribes código nuevo ni modificas archivos: tus skills son solo de lectura. "
    "Si el workspace está vacío, dilo explícitamente.\n\n"
    "FORMATO OBLIGATORIO DE LA SALIDA:\n"
    "## INFORME DE CONTEXTO\n"
    "### Estado del workspace\n- <hallazgos>\n"
    "### Archivos relevantes\n- <ruta> — <para qué sirve>\n"
    "### Recomendaciones para el Programador\n1. <recomendación>\n"
)

BASE_DEVELOPER: str = (
    "Eres el Desarrollador de OtterCode. Recibes la misión, el plan del Arquitecto "
    "y el informe de contexto del Investigador. Tu ÚNICO trabajo es traducir todo "
    "eso a código funcional y completo. REGLA DE HIERRO: NO des explicaciones "
    "largas. NO modifiques el plan. Genera el código requerido en su totalidad. "
    "Tienes acceso a herramientas reales del sistema.\n\n"
    "PROTOCOLO DE ARCHIVOS GRANDES (OBLIGATORIO): un archivo de más de ~120 líneas "
    "se escribe POR PARTES: la 1ª con write_file y el resto con append_file (una "
    "llamada por parte, ≤150 líneas cada una). PROHIBIDO pegar código en tu texto: "
    "el código SOLO viaja dentro del JSON de la skill. Entre skill y skill tu texto "
    "debe ser de UNA línea como máximo.\n"
)

BASE_REVIEWER: str = (
    "Eres el Auditor de Calidad (Red Team) de la balsa OtterCode. Revisa el código "
    "del Programador en busca de errores lógicos, de seguridad y de "
    "funcionamiento. Puedes inspeccionar el workspace (read_file, list_dir, tree) "
    "y ejecutar TESTS con execute_bash (p. ej. `python3 -m py_compile app.py`, "
    "`node --check app.js`, `ls -la`). REGLA DE HIERRO: Si todo es perfecto y los "
    "tests pasan, responde SOLO 'Aprobado'. Si hay errores, muestra SOLO el "
    "fragmento corregido indicando el archivo. No regeneres todo el proyecto.\n"
)

# v4.3 · MODO CLAUDE CODE: UN agente experto con el bucle de herramientas
# completo. Sin relevos, sin handoffs gigantes: la arquitectura que de verdad
# funciona con modelos locales.
OTTER_TOOLS: List[str] = [
    "read_file", "write_file", "append_file", "edit_file", "apply_patch", "mkdir",
    "list_dir", "tree", "grep_search", "glob_files", "execute_bash",
    "git_status", "git_diff", "git_log", "git_commit",
    "todo_write", "todo_read", "semantic_search", "index_workspace", "finalizar",
]

BASE_AGENT: str = (
    "=== AGENTE OTTER — MODO CLAUDE CODE ===\n"
    "Prioridad absoluta: cumple la petición del usuario al pie de la letra. "
    "No sustituyas la tarea por un demo ni por una web de nutrias.\n"
    "Eres Otter, ingeniero de software en el workspace del usuario. estilo Claude Code.\n"
    "1. LEE antes de editar (read_file / list_dir).\n"
    "2. Si el archivo YA EXISTE: PROHIBIDO write_file (trunca a 4096 tokens y deja el disco intacto). "
    "OBLIGATORIO edit_file (old_string EXACTO + new_string) o apply_patch. Varios edit_file por sección.\n"
    "3. write_file SOLO para archivos NUEVOS y cortos (≤120 líneas). Grandes: write_file 1ª parte + append_file.\n"
    "4. Tras editar: execute_bash (tests/compile). Una tool por paso. Prohibido volcar HTML en el chat.\n"
    "5. Al terminar: finalizar con un resumen breve. No digas que escribiste un archivo si la tool falló.\n"
)

# v4.1 · /goal: objetivo mayor del usuario inyectado en TODOS los agentes
def _goal_block(goal: str) -> str:
    g = (goal or "").strip()
    if not g:
        return ""
    return (
        "\n\n# 🎯 OBJETIVO MAYOR DEL USUARIO (PRIORIDAD MÁXIMA)\n"
        f"{g}\n"
        "Todas tus decisiones de este turno deben servir a ESTE objetivo. "
        "Si una subtarea lo contradice, prioriza el objetivo.\n"
    )

# v4.1 · /ultrareview: auditoría exhaustiva multi-lente
ULTRAREVIEW_SUFFIX = (
    "\n\n=== MODO ULTRA REVIEW (auditoría EXHAUSTIVA) ===\n"
    "No vale una lectura rápida. Recorre el workspace COMPLETO pasando por 5 "
    "lentes, una a una:\n"
    "1. LÓGICA: bugs, condiciones al revés, casos no manejados.\n"
    "2. SEGURIDAD: inyección, XSS, paths, secretos, inputs sin validar.\n"
    "3. ROBUSTEZ: errores de red/disco/JSON, recursos sin cerrar, estados límite.\n"
    "4. RENDIMIENTO: bucles anidados, N+1, memoria, cargas síncronas.\n"
    "5. ESTILO: nombres, duplicación, funciones kilométricas.\n"
    "EJECUTA TESTS REALES con execute_bash (py_compile, node --check, pytest si "
    "existe) y pega el resultado. Usa read_file en TODOS los archivos del "
    "workspace antes de opinar.\n"
    "SALIDA OBLIGATORIA:\n"
    "## 🔬 ULTRA REVIEW\n"
    "### Hallazgos (por severidad)\n"
    "- 🔴 BLOQUEANTE: <archivo:línea> — <problema> — <corrección exacta>\n"
    "- 🟡 MENOR: …\n"
    "- 🔵 ESTILO: …\n"
    "### Tests ejecutados\n- <cmd> → <resultado>\n"
    "### VEREDICTO\nAprobado | Con observaciones (lista arriba)\n"
    "SOLO digas 'Aprobado' si cero hallazgos rojos Y tests en verde.\n"
)


def tool_protocol(tool_names: List[str], native: bool = False) -> str:
    """Bloque de instrucción de skills inyectado según el agente."""
    if not tool_names:
        return ""
    if native:
        names = ", ".join(tool_names)
        return (
            "\n=== TOOLS (function calling nativo) ===\n"
            "Usa function calling; no emitas JSON de tools en el texto.\n"
            f"Tools: {names}, finalizar.\n"
            "1) LEE (read_file/list_dir) antes de editar.\n"
            "2) Archivo existente: SOLO edit_file/apply_patch. write_file SOLO archivos nuevos cortos.\n"
            "3) Tras editar, execute_bash para tests/compile.\n"
            "4) Una tool por paso. Prohibido dump de HTML/código en el chat.\n"
        )
    lines = [
        "",
        "=== SISTEMA DE SKILLS (OtterCode, estilo Claude Code) ===",
        "Puedes usar skills REALES del sistema emitiendo al final de tu respuesta "
        "EXACTAMENTE un objeto JSON dentro de un bloque ```json (una sola skill por respuesta):",
        "```json",
        '{"tool": "<nombre>", "arguments": {"<arg>": "<valor>"}}',
        "```",
        "Skills permitidas para ti:",
    ]
    for t in tool_names:
        spec = tools.TOOLS.get(t)
        if not spec:
            continue
        lines.append(f"- {t} — {spec['desc']} · {spec['example']}")
    lines += [
        "REGLAS DE USO DE SKILLS:",
        "- Un SOLO objeto JSON de skill por respuesta.",
        "- Las rutas son RELATIVAS al workspace de la tarea (nunca absolutas).",
        "- ARCHIVO EXISTENTE: PROHIBIDO write_file. Usa edit_file con old_string copiado "
        "literal del disco (read_file primero). Si edit_file falla, reintenta con el snippet.",
        "- ARCHIVOS NUEVOS GRANDES (>~120 líneas): POR PARTES — write_file 1ª parte (≤150 líneas) "
        "y append_file. NUNCA el archivo entero en una sola llamada ni código fuera del JSON.",
        "- El JSON debe ser 100% válido: dentro de cadenas, escapa comillas (\\\") y saltos de línea (\\n).",
        "- Tras cada llamada recibirás el RESULTADO por texto: emite la siguiente skill o 'finalizar'.",
        "- Al completar tu objetivo emite: {\"tool\": \"finalizar\", \"arguments\": {\"resumen\": \"...\"}}",
    ]
    return "\n".join(lines)


# --- Registros de agentes (core estático + dinámicos en memoria) -----------

CORE_AGENTS: Dict[str, Agent] = {
    # v4.3 · MODO CLAUDE CODE (experiencia principal): un solo agente con
    # el bucle de tools completo.
    "agent": Agent(
        id="agent", nombre="Otter", rol="Agente único · Claude Code · Tools",
        system_prompt=BASE_AGENT + tool_protocol(OTTER_TOOLS, native=True),
        icon="🦦", color_neon="#22d3ee",
        tools_disponibles=list(OTTER_TOOLS),
    ),
    "architect": Agent(
        id="architect", nombre="El Arquitecto", rol="Agente Principal · Planner",
        system_prompt=BASE_ARCHITECT, icon="🧠", color_neon="#22d3ee",
        tools_disponibles=[],
    ),
    "researcher": Agent(
        id="researcher", nombre="El Investigador", rol="Contexto · Web · Solo lectura",
        system_prompt=BASE_RESEARCHER + tool_protocol([
            "read_file", "list_dir", "tree", "web_search", "web_fetch",
            "wikipedia_search", "http_request", "vault_search",
            "memory_recall", "semantic_search", "finalizar"]),
        icon="🔬", color_neon="#8b9cf7",
        tools_disponibles=["read_file", "list_dir", "tree", "web_search", "web_fetch",
                           "wikipedia_search", "http_request",
                           "vault_search", "memory_recall", "semantic_search", "finalizar"],
        readonly=True,
    ),
    "developer": Agent(
        id="developer", nombre="El Programador", rol="Implementación · Skills",
        system_prompt=BASE_DEVELOPER + tool_protocol(
            ["read_file", "write_file", "append_file", "edit_file", "apply_patch",
             "mkdir", "list_dir", "tree", "grep_search", "glob_files", "execute_bash",
             "git_status", "git_diff", "git_log", "git_commit",
             "todo_write", "todo_read", "semantic_search", "index_workspace", "finalizar"]),
        icon="💻", color_neon="#34d399",
        tools_disponibles=["read_file", "write_file", "append_file", "edit_file", "apply_patch",
                           "mkdir", "list_dir", "tree", "grep_search", "glob_files", "execute_bash",
                           "git_status", "git_diff", "git_log", "git_commit",
                           "todo_write", "todo_read", "semantic_search", "index_workspace",
                           "finalizar"],
    ),
    "reviewer": Agent(
        id="reviewer", nombre="El Revisor", rol="Red Team · QA & Tests",
        system_prompt=BASE_REVIEWER + tool_protocol([
            "read_file", "list_dir", "tree", "execute_bash", "python_exec",
            "grep_search", "glob_files", "hash_text", "todo_read",
            "git_status", "git_diff", "git_log", "sqlite_query", "finalizar"]),
        icon="🔍", color_neon="#fbbf24",
        tools_disponibles=["read_file", "list_dir", "tree", "execute_bash", "python_exec",
                           "grep_search", "glob_files", "hash_text", "todo_read",
                           "git_status", "git_diff", "git_log", "sqlite_query",
                           "finalizar"],
        readonly=True,
    ),
}

# Orden canónico de la cadena core (el Revisor cierra el ciclo de calidad)
AGENT_ORDER: List[str] = ["architect", "researcher", "developer", "reviewer"]

# Native ON: slim protocol; native OFF: JSON skill catalog for Otter.
try:
    _nat = native_tools_enabled(DEFAULT_MODEL)
except Exception:
    _nat = False
CORE_AGENTS["agent"].system_prompt = BASE_AGENT + tool_protocol(OTTER_TOOLS, native=_nat)

# Base prompts core (para regenerar el protocolo según toggles de skills)
_CORE_BASE_PROMPTS: Dict[str, str] = {
    "architect": BASE_ARCHITECT,
    "agent": BASE_AGENT,
    "researcher": BASE_RESEARCHER,
    "developer": BASE_DEVELOPER,
    "reviewer": BASE_REVIEWER,
}

# Agentes dinámicos creados al vuelo (en memoria, vida del proceso)
DYNAMIC_AGENTS: Dict[str, Agent] = {}

# ── skills_config.json: toggles on/off por skill (persistente) ──
_SKILLS_CFG_PATH = Path(__file__).resolve().parent.parent / "skills_config.json"


def _load_skills_cfg() -> Dict[str, Any]:
    try:
        return json.loads(_SKILLS_CFG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def skill_enabled(tool: str) -> bool:
    return str(tool) not in _load_skills_cfg().get("disabled", [])


def set_skill_enabled(tool: str, enabled: bool) -> None:
    cfg = _load_skills_cfg()
    disabled = set(cfg.get("disabled", []))
    if enabled:
        disabled.discard(tool)
    else:
        disabled.add(tool)
    cfg["disabled"] = sorted(disabled)
    _SKILLS_CFG_PATH.write_text(
        json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8"
    )

# Paleta de neón para agentes dinámicos (no colisiona con los 4 core)
NEON_PALETTE = ["#ff9f43", "#f368e0", "#54a0ff", "#7ed957", "#ff6b6b",
                "#feca57", "#48dbfb", "#ff793f"]
ICON_CHOICES = ["🗄️", "🔐", "🧪", "", "🎨", "🛡️", "⚡", "🧩"]
AGENT_TOOL_CHOICES = [
    "read_file", "write_file", "append_file", "edit_file", "mkdir", "list_dir", "tree",
    "grep_search", "glob_files", "execute_bash", "python_exec",
    "git_status", "git_diff", "git_log", "sqlite_query",
    "csv_peek", "json_query", "web_search", "web_fetch", "wikipedia_search",
    "arxiv_search", "youtube_transcript", "http_request", "weather",
    "github_search", "stack_search", "npm_search", "pypi_info",
    "sys_info", "hash_text", "base64_code", "uuid_gen",
    "memory_save", "memory_recall", "image_describe", "embed_text",
    "model_info", "model_list", "ollama_consult",
    "todo_write", "todo_read",
    "vault_search", "vault_read", "vault_write",
]


def get_agent(agent_id: str) -> Agent:
    """Busca un agente en los registros core + dinámicos."""
    agent = CORE_AGENTS.get(agent_id) or DYNAMIC_AGENTS.get(agent_id)
    if agent is None:
        raise KeyError(f"Agente desconocido: {agent_id}")
    return agent


# ---------------------------------------------------------------------------
# v4.0 · ESCUADRÓN PRESET: 20 especialistas de fábrica, cada uno clavado en
# un dominio. Viven en DYNAMIC_AGENTS (inyectables por el Arquitecto y
# visibles en la vista Agentes) y SIEMPRE están disponibles.
# ---------------------------------------------------------------------------

_PRESET_AGENTS: List[Dict[str, Any]] = [
    {"id": "frontend", "nombre": "El Frontend Designer", "rol": "UI/UX · HTML · CSS · Tailwind",
     "icon": "🎨", "color": "#f368e0",
     "prompt": "Dominas HTML5 semántico, CSS moderno (grid, flex, animaciones, 3D con transform), Tailwind y diseño oscuro/neón. Creas interfaces preciosas, responsive y accesibles. Código limpio sin frameworks pesados salvo petición expresa.",
     "tools": ["read_file", "write_file", "append_file", "edit_file", "mkdir", "list_dir", "tree", "http_request", "stack_search"]},
    {"id": "backend", "nombre": "El Backend Engineer", "rol": "Python · FastAPI · APIs REST",
     "icon": "🐍", "color": "#7ed957",
     "prompt": "Dominas Python, FastAPI, Pydantic, arquitectura de APIs REST, validación de datos y manejo de errores. Escribes endpoints robustos, tipados y documentados (OpenAPI).",
     "tools": ["read_file", "write_file", "append_file", "edit_file", "mkdir", "list_dir", "tree", "execute_bash", "python_exec", "pypi_info"]},
    {"id": "database", "nombre": "El Database Architect", "rol": "SQL · SQLite · Modelado de datos",
     "icon": "🗄️", "color": "#54a0ff",
     "prompt": "Dominas modelado relacional, SQL avanzado, índices, normalización y migraciones. Trabajas con SQLite del workspace y ficheros CSV/JSON como fuentes de datos.",
     "tools": ["read_file", "write_file", "append_file", "mkdir", "list_dir", "tree", "sqlite_query", "csv_peek", "json_query"]},
    {"id": "security", "nombre": "El Security Auditor", "rol": "OWASP · Hardening · Red Team",
     "icon": "🛡️", "color": "#ff6b6b",
     "prompt": "Auditas código buscando OWASP Top 10: inyección, XSS, path traversal, secretos hardcodeados, SSRF. Reportas hallazgos por severidad con corrección concreta. SOLO lectura: nunca modificas código ajeno.",
     "tools": ["read_file", "list_dir", "tree", "grep_search", "execute_bash", "http_request", "stack_search"]},
    {"id": "devops", "nombre": "El DevOps Automator", "rol": "Docker · Bash · CI/CD",
     "icon": "🐳", "color": "#48dbfb",
     "prompt": "Dominas Docker/docker-compose, scripts bash de despliegue, Makefiles y pipelines CI/CD. Automatizas build, test y deploy con scripts idempotentes.",
     "tools": ["read_file", "write_file", "append_file", "mkdir", "list_dir", "tree", "execute_bash", "github_search"]},
    {"id": "datasci", "nombre": "El Data Scientist", "rol": "Pandas · Análisis · Visualización",
     "icon": "📊", "color": "#feca57",
     "prompt": "Dominas análisis de datos con Python (pandas, estadística), limpieza de datasets CSV/JSON y generación de informes con métricas claras. Ejecutas análisis reales con python_exec.",
     "tools": ["read_file", "write_file", "append_file", "list_dir", "tree", "python_exec", "csv_peek", "json_query"]},
    {"id": "airag", "nombre": "El AI & RAG Specialist", "rol": "LLMs · Embeddings · RAG local",
     "icon": "🧠", "color": "#8b9cf7",
     "prompt": "Integras LLMs locales (Ollama), embeddings, búsqueda vectorial y pipelines RAG. Diseñas prompts de sistema efectivos y cadenas de herramientas.",
     "tools": ["read_file", "write_file", "append_file", "list_dir", "tree", "python_exec", "http_request", "model_list", "ollama_consult", "embed_text", "vault_search"]},
    {"id": "gamedev", "nombre": "El Game Dev", "rol": "Canvas · WebGL · Three.js",
     "icon": "🎮", "color": "#ff9f43",
     "prompt": "Creas juegos y experiencias 3D/2D en HTML5: Canvas, WebGL, Three.js, game loops, física básica y controles de teclado/ratón. Todo autocontenido en el workspace.",
     "tools": ["read_file", "write_file", "append_file", "mkdir", "list_dir", "tree", "http_request", "stack_search"]},
    {"id": "qa", "nombre": "El QA & Test Engineer", "rol": "Pytest · Tests · Assertions",
     "icon": "🧪", "color": "#7ed957",
     "prompt": "Diseñas y ejecutas suites de tests reales (pytest, node --test, asserts bash). Buscas casos límite, ejecutas y reportas PASS/FAIL por caso. No arreglas código: lo certificas o lo rechazas con evidencia.",
     "tools": ["read_file", "list_dir", "tree", "glob_files", "grep_search", "execute_bash", "python_exec", "todo_write", "todo_read"]},
    {"id": "refactor", "nombre": "El Refactor Pro", "rol": "Clean Code · SOLID · Deuda técnica",
     "icon": "⚡", "color": "#feca57",
     "prompt": "Refactorizas código existente aplicando SOLID, nombres claros y funciones cortas SIN cambiar el comportamiento. Usas edit_file con reemplazos quirúrgicos, nunca reescribes de cero lo que funciona.",
     "tools": ["read_file", "write_file", "append_file", "edit_file", "list_dir", "tree", "grep_search", "glob_files", "execute_bash"]},
    {"id": "pwa", "nombre": "El PWA & Mobile Expert", "rol": "Responsive · Service Workers · Offline",
     "icon": "📱", "color": "#48dbfb",
     "prompt": "Dominas diseño mobile-first, PWA instalables (manifest, service worker, cache offline) y rendimiento en dispositivos modestos. Viewport, safe-areas y touch son tu terreno.",
     "tools": ["read_file", "write_file", "append_file", "edit_file", "list_dir", "tree", "http_request", "stack_search"]},
    {"id": "scraper", "nombre": "El Web Scraper", "rol": "Crawling · Parsing · Datasets",
     "icon": "🌐", "color": "#54a0ff",
     "prompt": "Extraes datos de la web de forma ética (respetando robots y límites): web_fetch/http_request + parseo HTML/JSON y consolidación en CSV/JSON limpios del workspace.",
     "tools": ["http_request", "web_fetch", "web_search", "read_file", "write_file", "append_file", "json_query", "csv_peek", "list_dir", "tree"]},
    {"id": "writer", "nombre": "El Technical Writer", "rol": "Docs · README · Markdown",
     "icon": "📝", "color": "#7dd3fc",
     "prompt": "Escribes documentación técnica impecable: README con quickstart, guías de uso, referencias de API y comentarios de código. Markdown limpio, ejemplos verificables, cero relleno.",
     "tools": ["read_file", "write_file", "append_file", "mkdir", "list_dir", "tree", "glob_files"]},
    {"id": "apiint", "nombre": "El API Integrator", "rol": "REST · OAuth · Webhooks",
     "icon": "🔌", "color": "#ff793f",
     "prompt": "Conectas sistemas: integración de APIs externas, autenticación (API keys, OAuth), paginación, reintentos y webhooks. Verificas cada endpoint con peticiones reales antes de darlo por hecho.",
     "tools": ["http_request", "web_fetch", "read_file", "write_file", "append_file", "json_query", "python_exec", "list_dir", "tree"]},
    {"id": "algo", "nombre": "El Algorithm Engineer", "rol": "Estructuras de datos · Complejidad",
     "icon": "📐", "color": "#f368e0",
     "prompt": "Resuelves problemas de algoritmos y estructuras de datos con la complejidad óptima. Analizas Big-O, eliges la estructura adecuada y verificas con casos de prueba ejecutados.",
     "tools": ["read_file", "write_file", "append_file", "python_exec", "execute_bash", "list_dir", "tree"]},
    {"id": "sysadmin", "nombre": "El SysAdmin Linux", "rol": "Shell · Permisos · Sistema",
     "icon": "⚙️", "color": "#ff6b6b",
     "prompt": "Experto en Linux: bash avanzado, permisos, procesos, disco, redes básicas y diagnóstico del sistema. Automatizas mantenimiento con scripts seguros (dentro del workspace).",
     "tools": ["execute_bash", "sys_info", "read_file", "write_file", "append_file", "list_dir", "tree", "hash_text"]},
    {"id": "web3", "nombre": "El Crypto & Web3 Dev", "rol": "Smart contracts · JSON-RPC · Wallets",
     "icon": "🪙", "color": "#ff9f43",
     "prompt": "Dominas blockchain aplicada: smart contracts (Solidity básico), interacción JSON-RPC con nodos, análisis de transacciones y gestión segura de claves (NUNCA hardcodees private keys).",
     "tools": ["http_request", "read_file", "write_file", "append_file", "python_exec", "json_query", "stack_search", "list_dir", "tree"]},
    {"id": "automation", "nombre": "El Automation Builder", "rol": "Workflows · Scripts · Cron",
     "icon": "🤖", "color": "#7ed957",
     "prompt": "Construyes automatizaciones: scripts que encadenan tareas, procesan lotes, monitorizan cambios y se programan (cron). Filosofía: idempotente, con logs claros y fallos explícitos.",
     "tools": ["execute_bash", "python_exec", "read_file", "write_file", "append_file", "mkdir", "list_dir", "tree", "http_request"]},
    {"id": "curator", "nombre": "El Obsidian Curator", "rol": "Knowledge base · Notas · Vault",
     "icon": "📚", "color": "#8b9cf7",
     "prompt": "Curas la base de conocimiento: organizas notas del vault, creas índices, wiki-links y estructura jerárquica. Conviertes el trabajo de las misiones en conocimiento permanente y recuperable.",
     "tools": ["vault_search", "vault_read", "vault_write", "memory_save", "memory_recall", "read_file", "list_dir", "tree"]},
    {"id": "reviewer2", "nombre": "El Deep Code Reviewer", "rol": "Auditoría línea a línea",
     "icon": "🔍", "color": "#feca57",
     "prompt": "Revisas código con lupa: bugs de lógica, edge cases, manejo de errores, rendimiento y legibilidad. Emites veredictos concretos por archivo con severidad (bloqueante/menor/estilo) y sugerencia exacta.",
     "tools": ["read_file", "list_dir", "tree", "grep_search", "glob_files", "execute_bash", "git_status", "git_diff", "git_log"]},
]


def _register_preset_agents() -> None:
    """Registra el escuadrón preset en DYNAMIC_AGENTS (siempre disponible)."""
    taken = {a.icon for a in CORE_AGENTS.values()}
    for spec in _PRESET_AGENTS:
        aid = f"preset_{spec['id']}"
        allowed: List[str] = []
        for t in spec["tools"]:
            t = tools.resolve_name(t)
            if (t in AGENT_TOOL_CHOICES or t == "finalizar") and t not in allowed:
                allowed.append(t)
        if "finalizar" not in allowed:
            allowed.append("finalizar")
        readonly = not any(tools.TOOLS.get(t, {}).get("writes_fs") for t in allowed)
        sp = (
            f"Eres {spec['nombre']} de la balsa OtterCode, especialista en "
            f"{spec['rol']}. {spec['prompt']} "
            "Método de trabajo: inspecciona primero (tree/list_dir/read_file), "
            "ejecuta tu especialidad con precisión y cierra SIEMPRE con "
            '{"tool": "finalizar", "arguments": {"resumen": "<logros y archivos>"}}. '
            "REGLA DE HIERRO: no salgas de tu especialidad; el código que no sea "
            "tuyo no lo reescribes, lo reportas."
        )
        icon = spec["icon"] if spec["icon"] not in taken else spec["icon"]
        DYNAMIC_AGENTS[aid] = Agent(
            id=aid, nombre=spec["nombre"], rol=spec["rol"],
            system_prompt=sp + tool_protocol(allowed),
            icon=icon, color_neon=spec["color"],
            tools_disponibles=allowed,
            dynamic=True, readonly=readonly,
        )


_register_preset_agents()


# ---------------------------------------------------------------------------
# Fábrica de Agentes Dinámicos (POST /api/agents/create)
# ---------------------------------------------------------------------------

AGENT_FACTORY_SYSTEM: str = (
    "Eres la Fábrica de Agentes de la balsa OtterCode. El usuario describe en "
    "lenguaje natural el especialista que necesita y tú generas su PERFIL "
    "TÉCNICO COMPLETO. REGLA DE HIERRO: tu salida debe ser ÚNICAMENTE un objeto "
    "JSON dentro de un bloque ```json, sin texto adicional. Las claves EXACTAS:\n"
    "- \"nombre\": con la fórmula 'El/la <título>' (p. ej. 'El SQL Maestro').\n"
    "- \"rol\": una línea que resume su especialidad.\n"
    "- \"system_prompt\": mínimo 6 líneas en 2º persona: quién es, su objetivo, "
    "una sección 'REGLA DE HIERRO' con restricciones negativas explícitas, y cómo "
    "debe usar las skills (empezar inspeccionando con tree/list_dir, trabajar y "
    "cerrar emitindo {\"tool\": \"finalizar\", \"arguments\": {\"resumen\": \"...\"}}).\n"
    f"- \"icon\": elige UNO de: {' '.join(ICON_CHOICES)}.\n"
    f"- \"color_neon\": elige UN hex de: {' '.join(NEON_PALETTE)}.\n"
    "- \"tools_disponibles\": elige SOLO las necesarias de: "
    f"{', '.join(AGENT_TOOL_CHOICES)}; si no necesita escribir archivos, NO "
    "incluyas write_file ni mkdir.\n"
)


def build_profile_prompt(description: str) -> str:
    return (
        "# CREACIÓN DE AGENTE DINÁMICO — BALSA OTTERCODE\n\n"
        f"[DESCRIPCIÓN DEL USUARIO]\n{description}\n\n"
        "Genera el perfil técnico completo del nuevo agente como JSON en un "
        "bloque ```json con las 6 claves exactas."
    )


def normalize_agent_profile(obj: Dict[str, Any], agent_id: str,
                            session_id: Optional[str]) -> Agent:
    """Valida y normaliza el perfil generado por la Fábrica → Agent."""
    nombre = str(obj.get("nombre") or "").strip()
    rol = str(obj.get("rol") or "").strip()
    sp = str(obj.get("system_prompt") or "").strip()
    if len(nombre) < 3:
        raise ValueError("El nombre del agente generado es demasiado corto.")
    if len(rol) < 3:
        raise ValueError("El rol del agente generado es demasiado corto.")
    if len(sp) < 80:
        raise ValueError("El system_prompt generado es demasiado corto para ser útil.")
    nombre, rol, sp = nombre[:60], rol[:120], sp[:4000]
    if "REGLA DE HIERRO" not in sp.upper():
        sp += (
            "\n\nREGLA DE HIERRO: NO salgas de tu especialidad. NO regeneres "
            "archivos ajenos a tu cometido. Cierra SIEMPRE tu turno emitindo "
            "finalizar."
        )
    icon = str(obj.get("icon") or "").strip()
    if icon not in ICON_CHOICES:
        icon = "🧩"
    color = str(obj.get("color_neon") or "").strip()
    if not color.startswith("#"):
        color = "#" + color
    core_colors = {a.color_neon.lower() for a in CORE_AGENTS.values()}
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color) or color.lower() in core_colors:
        color = NEON_PALETTE[int(uuid.uuid5(uuid.NAMESPACE_URL, agent_id).hex, 16) % len(NEON_PALETTE)]
    tools_raw = obj.get("tools_disponibles")
    if isinstance(tools_raw, str):
        tools_raw = [t.strip() for t in tools_raw.split(",") if t.strip()]
    allowed: List[str] = []
    for t in tools_raw if isinstance(tools_raw, list) else []:
        t = tools.resolve_name(str(t).strip())
        if t in AGENT_TOOL_CHOICES and t not in allowed:
            allowed.append(t)
    if not allowed:
        allowed = ["read_file", "list_dir", "tree"]
    if "finalizar" not in allowed:
        allowed.append("finalizar")
    readonly = not any(tools.TOOLS.get(t, {}).get("writes_fs") for t in allowed)
    return Agent(
        id=agent_id, nombre=nombre, rol=rol,
        system_prompt=sp + tool_protocol(allowed),
        icon=icon, color_neon=color,
        tools_disponibles=allowed,
        dynamic=True, created_in=session_id, readonly=readonly,
    )


# ---------------------------------------------------------------------------
# Utilidades SSE
# ---------------------------------------------------------------------------

