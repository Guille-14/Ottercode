"""Hooks fijos (no dependen de que el modelo se acuerde)."""
from __future__ import annotations

import ast
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.config import DB_PATH, WORKSPACE_ROOT


def post_code_generated(filepath: str, content: str) -> Dict[str, Any]:
    """Valida sintaxis básica antes de mostrar el archivo en artefactos."""
    path = (filepath or "").strip()
    src = content or ""
    issues: List[str] = []
    ext = Path(path).suffix.lower()
    if ext == ".py":
        try:
            ast.parse(src)
        except SyntaxError as exc:
            issues.append(f"Python SyntaxError L{exc.lineno}: {exc.msg}")
    elif ext in (".json",):
        try:
            json.loads(src)
        except json.JSONDecodeError as exc:
            issues.append(f"JSON inválido L{exc.lineno}: {exc.msg}")
    elif ext in (".js", ".mjs"):
        if src.count("{") != src.count("}"):
            issues.append("JS: llaves { } desbalanceadas")
        if src.count("(") != src.count(")"):
            issues.append("JS: paréntesis desbalanceados")
    elif ext in (".html", ".htm"):
        if re.search(r"<(html|div|span|p|ul|ol|table|script|style)\b", src, re.I):
            # etiquetas de apertura sin cierre grosero
            for tag in ("html", "body", "head", "div", "script", "style"):
                opens = len(re.findall(rf"<{tag}\b", src, re.I))
                closes = len(re.findall(rf"</{tag}>", src, re.I))
                if opens > closes + 1:
                    issues.append(f"HTML: <{tag}> posiblemente sin cerrar")
                    break
    ok = not issues
    return {"ok": ok, "path": path, "issues": issues}


def pre_agent_handoff(from_agent: str, to_agent: str, task_id: str = "") -> None:
    """Registra el traspaso de agente (log + SQLite si hay DB)."""
    line = (
        f"[handoff] {datetime.now().isoformat(timespec='seconds')} "
        f"{from_agent} → {to_agent} task={task_id}"
    )
    print(line, flush=True)
    try:
        import sqlite3

        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            """CREATE TABLE IF NOT EXISTS handoffs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT, from_agent TEXT, to_agent TEXT, task_id TEXT
            )"""
        )
        conn.execute(
            "INSERT INTO handoffs (ts, from_agent, to_agent, task_id) VALUES (?,?,?,?)",
            (datetime.now().isoformat(timespec="seconds"), from_agent, to_agent, task_id),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def remember_file_hook(workdir: Path, result: Dict[str, Any]) -> None:
    """Persiste el último lint por archivo en el workspace (panel de artefactos)."""
    if not workdir or not result.get("path"):
        return
    dest = workdir / ".otter_hooks.json"
    data: Dict[str, Any] = {}
    try:
        data = json.loads(dest.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, json.JSONDecodeError):
        data = {}
    data[str(result["path"])] = {
        "ok": bool(result.get("ok")),
        "issues": result.get("issues") or [],
    }
    try:
        dest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def load_file_hooks(workdir: Optional[Path]) -> Dict[str, Any]:
    if not workdir:
        return {}
    dest = workdir / ".otter_hooks.json"
    try:
        data = json.loads(dest.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}
