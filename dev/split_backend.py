"""Refactorización de backend.py (monolito) a paquete backend/ moderno.

Lee backend.py (copia plana de ottercode-v2) y genera estos módulos sin
transcribir el cuerpo (solo cortes por rango de línea + cabeceras + @app→@router):

  backend/config.py     constantes/env/identidad (1-207)
  backend/db.py         SQLite + transcripción + historial SQL (209-455)
  backend/agents.py     Agent, núcleo, presets, skills, fábrica-prompts (456-1029)
  backend/ollama.py     transporte LLM + VRAM flush + stream (1034-1365, 1805-1830)
  backend/prompts.py    extracción JSON/tools + constructores de prompts (1366-1690)
  backend/runstate.py   OtterRun + condense (1693-1804, +AbortRequested 1030-1032)
  backend/engine.py     orquestador de turnos y misiones (1831-3079)
  backend/history.py    persistencia de sesiones (3097-3176)
  backend/profiles.py   perfiles de configuración (3178-3317)
  backend/vault.py      cerebro Obsidian + memoria automática (3084-3095, 3825-3985, 4082-4303)
  backend/runtime.py    estado vivo: RUN_LOCK/ACTIVITY + bootstrap (3363-3413)
  backend/routes.py     capa HTTP REST (/api vía APIRouter) (3476-3824, 3986-4081, 4304-4932)
  backend/main.py       fábrica de la app FastAPI + estáticos + PWA (3321-3362, 3414-3475)
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "backend.py.monolith"
OUT = ROOT / "backend"
OUT.mkdir(exist_ok=True)

lines = SRC.read_text(encoding="utf-8").splitlines(keepends=True)


def slice_range(start: int, end: int) -> str:
    """Rango de 1-based inclusivo → texto."""
    return "".join(lines[start - 1 : end])


HEADER: dict[str, list[str]] = {
    "config": ["# OtterCode — configuración, entorno e identidad (de backend.py)", ""],
    "db": [
        "# OtterCode — persistencia SQLite (chats, historial SQL, transcripciones)",
        "from __future__ import annotations",
        "from backend.config import *  # noqa: F401,F403  (json/os/re/time/uuid/Path + env)",
        "",
    ],
    "agents": [
        "# OtterCode — registro de agentes (núcleo + escuadrón preset + skills config)",
        "from __future__ import annotations",
        "from backend.config import *  # noqa: F401,F403",
        "",
    ],
    "ollama": [
        "# OtterCode — transporte LLM: sesiones, VRAM flush, streaming, errores",
        "from __future__ import annotations",
        "from backend.config import *  # noqa: F401,F403",
        "from backend.runstate import AbortRequested  # noqa: E402  (abortos en stream_llm)",
        "",
    ],
    "prompts": [
        "# OtterCode — constructores de prompts, extracción de JSON y herramientas",
        "from __future__ import annotations",
        "from backend.config import *  # noqa: F401,F403",
        "from backend.agents import *  # noqa: F401,F403",
        "",
    ],
    "runstate": [
        "# OtterCode — estado de ejecución (OtterRun) y control de aborto",
        "from __future__ import annotations",
        "from backend.config import *  # noqa: F401,F403",
        "from backend.agents import *  # noqa: F401,F403",
        "",
    ],
    "engine": [
        "# OtterCode — motor de orquestación: turnos, relevos, compactación y rescate",
        "from __future__ import annotations",
        "from backend.config import *  # noqa: F401,F403",
        "from backend.agents import *  # noqa: F401,F403",
        "from backend.prompts import *  # noqa: F401,F403",
        "from backend.ollama import *  # noqa: F401,F403",
        "from backend.runstate import *  # noqa: F401,F403",
        "from backend.runtime import *  # noqa: F401,F403  (hen ATIVITY, ACTIVE_RUN)",
        "from backend.runtime import _force_stop_run, _activity_set, _activity_finish  # noqa",
        "from backend.history import *  # noqa: F401,F403",
        "from backend.vault import _memory_finish, _memory_recall, memory_note_for_run  # noqa",
        "",
    ],
    "history": [
        "# OtterCode — persistencia de sesiones (historial lateral y limpieza)",
        "from __future__ import annotations",
        "from backend.config import *  # noqa: F401,F403",
        "from backend.db import *  # noqa: F401,F403",
        "",
        "HISTORY: list[dict] = []",
        "",
    ],
    "profiles": [
        "# OtterCode — perfiles de configuración (CRUD + activo)",
        "from __future__ import annotations",
        "from backend.config import *  # noqa: F401,F403",
        "",
    ],
    "vault": [
        "# OtterCode — cerebro Obsidian: vault, notas de misión, recall y memoria",
        "from __future__ import annotations",
        "from backend.config import *  # noqa: F401,F403",
        "from backend.ollama import *  # noqa: F401,F403",
        "from backend.runstate import *  # noqa: F401,F403",
        "from fastapi import APIRouter  # noqa",
        'router = APIRouter(tags=["vault"])',
        "",
    ],
    "runtime": [
        "# OtterCode — estado vivo: RUN_LOCK, ACTIVE_RUN, ACTIVITY y bootstrap de arranque",
        "from __future__ import annotations",
        "from backend.config import *  # noqa: F401,F403",
        "from backend.db import *  # noqa: F401,F403",
        "from backend.history import *  # noqa: F401,F403",
        "from backend.profiles import *  # noqa: F401,F403",
        "",
    ],
    "routes": [
        "# OtterCode — capa HTTP REST: todos los endpoints /api (vía APIRouter)",
        "from __future__ import annotations",
        "from backend.config import *  # noqa: F401,F403",
        "from backend.engine import *  # noqa: F401,F403",
        "from backend.agents import *  # noqa: F401,F403",
        "from backend.agents import _load_skills_cfg  # noqa",
        "from backend.ollama import *  # noqa: F401,F403",
        "from backend.runtime import *  # noqa: F401,F403",
        "from backend.runtime import _force_stop_run, _activity_set, _activity_finish  # noqa",
        "from backend.db import *  # noqa: F401,F403",
        "from backend.history import HISTORY  # noqa",
        "from backend.vault import *  # noqa: F401,F403",
        "from fastapi import APIRouter  # noqa",
        'router = APIRouter(tags=["api"])',
        "",
    ],
    "main": [
        "# OtterCode — fábrica de la aplicación FastAPI (estáticos, PWA, tokens)",
        "from __future__ import annotations",
        "from backend.config import *  # noqa: F401,F403",
        "from backend.config import _load_identity  # noqa",
        "from backend.db import *  # noqa: F401,F403",
        "from backend.history import *  # noqa: F401,F403",
        "from backend.profiles import *  # noqa: F401,F403",
        "from backend.profiles import _ensure_default_profiles, _load_active_profile  # noqa",
        "from backend.runtime import *  # noqa: F401,F403",
        "from backend.routes import router as api_router  # noqa",
        "from backend.vault import router as vault_router  # noqa",
        "",
        "# ── bootstrap de arranque (equivalente al módulo plano original) ──────────",
        "load_history()",
        "_n_cleaned = cleanup_empty_tasks()",
        "if _n_cleaned:",
        "    print(f\"🧹 Limpieza de arranque: {_n_cleaned} carpeta(s) de tareas vacías eliminada(s).\")",
        "_load_identity()",
        "init_db()",
        "_migrate_json_to_db()",
        "_ensure_default_profiles()",
        "_load_active_profile()",
        "",
    ],
}


def to_router(text: str) -> str:
    """Traduce @app.X(ruta) → @router.X(ruta) para los chunks de routes/vault."""
    return (
        text.replace("@app.get(", "@router.get(")
        .replace("@app.post(", "@router.post(")
        .replace("@app.delete(", "@router.delete(")
        .replace("@app.put(", "@router.put(")
    )


RANGES: dict[str, list[tuple[int, int]]] = {
    "config": [(1, 207)],
    "db": [(209, 455)],
    "agents": [(456, 1029)],
    # runstate incluye AbortRequested (1030-1032) + OtterRun/_condense (1693-1804)
    "runstate": [(1030, 1032), (1693, 1804)],
    "ollama": [(1034, 1365), (1805, 1833)],
    "prompts": [(1366, 1690)],
    "engine": [(1834, 3079)],
    "history": [(3096, 3168)],
    "profiles": [(3172, 3317)],
    "vault": [(3084, 3095), (3825, 3985), (4082, 4303)],
    "runtime": [(3363, 3399)],
    "routes": [(3476, 3824), (3986, 4081), (4304, 4932)],
    "main": [(3321, 3362), (3414, 3475)],
}

for name, header in HEADER.items():
    if name == "config":
        continue  # su cuerpo ya incluye sus imports
    header.append("")

for name, ranges in RANGES.items():
    body = "".join(slice_range(*r) for r in ranges)
    if name in ("routes", "vault"):
        body = to_router(body)
    content = "\n".join(HEADER[name]) + "\n" + body
    if name == "config":
        content = content.replace(
            "Path(__file__).resolve().parent",
            "Path(__file__).resolve().parent.parent",
        )
    if name in ("agents", "vault"):
        content = content.replace(
            "Path(__file__).resolve().parent",
            "Path(__file__).resolve().parent.parent",
        )
    if name == "main":
        content = content.replace(
            "Path(__file__).parent",
            "Path(__file__).resolve().parent.parent",
        )
        # Fase 2 · servir la UI React construida (static/index.html)
        content = content.replace(
            'Path(__file__).resolve().parent.parent / "index.html"',
            'STATIC_DIR / "index.html"',
        )
        content += (
            "\n"
            "app.include_router(api_router)\n"
            "app.include_router(vault_router)\n"
        )
    (OUT / f"{name}.py").write_text(content, encoding="utf-8")
    n = body.count("\n") + 1
    sys.stdout.write(f"  {name}.py   +{n} líneas\n")

(OUT / "__init__.py").write_text(
    '"""OtterCode — orquestador local de agentes sobre Ollama (motor modular)."""\n'
    "# Re-export del app y del namespace plano original para compatibilidad\n"
    "# con dev/selftest.py (atributos leídos y mutados a nivel de módulo).\n"
    "from backend.main import app\n"
    "from backend.config import *  # noqa: F401,F403\n"
    "from backend.agents import *  # noqa: F401,F403\n"
    "from backend.prompts import *  # noqa: F401,F403\n"
    "from backend.ollama import *  # noqa: F401,F403\n"
    "from backend.engine import *  # noqa: F401,F403\n"
    "from backend.runstate import *  # noqa: F401,F403\n"
    "from backend.history import *  # noqa: F401,F403\n"
    "from backend.vault import *  # noqa: F401,F403\n"
    "from backend.routes import TaskRequest  # noqa\n"
    "from backend.main import _auth_ok  # noqa\n"
    "from backend.prompts import _prev_conversation_block  # noqa\n"
    "from backend.ollama import _LlmSession, _llm_request, _ollama_ndjson_text  # noqa\n"
    "from backend.engine import (  # noqa\n"
    "    _strip_think, _salvage_before_finalize, _salvage_cut_json,\n"
    "    _save_partial_on_abort, _should_rescue, _rescue_code_from_text,\n"
    "    _plan_has_code, _write_size_guard, _WRITE_TOOLS,\n"
    "    _forced_step_prompt, _invalid_json_feedback,\n"
    ")\n"
    "from backend.runstate import _condense_entries  # noqa\n"
    "from backend.vault import _memory_recall  # noqa\n"
    "__all__ = [\"app\"]\n",
    encoding="utf-8",
)
print("✓ Paquete backend/ generado")