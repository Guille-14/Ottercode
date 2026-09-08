"""Carpeta de proyecto del usuario (no solo workspace/<task_id>)."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, Optional

from backend.config import WORKSPACE_ROOT

_CFG = WORKSPACE_ROOT / "project.json"


def load_project() -> Dict[str, Any]:
    env = os.environ.get("OTTERCODE_PROJECT", "").strip()
    if env:
        p = Path(env).expanduser().resolve()
        if p.is_dir():
            return {"path": str(p), "ok": True, "source": "env"}
    try:
        data = json.loads(_CFG.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("path"):
            p = Path(str(data["path"])).expanduser().resolve()
            if p.is_dir():
                return {"path": str(p), "ok": True, "source": "file"}
    except (OSError, json.JSONDecodeError):
        pass
    return {"path": "", "ok": False, "source": ""}


def save_project(path: str) -> Dict[str, Any]:
    raw = (path or "").strip()
    if not raw:
        try:
            if _CFG.exists():
                _CFG.unlink()
        except OSError:
            pass
        return {"ok": True, "path": ""}
    p = Path(raw).expanduser().resolve()
    if not p.is_dir():
        raise ValueError(f"No es un directorio: {p}")
    if str(p) in ("/", "/etc", "/usr", "/bin"):
        raise ValueError("Ruta de sistema no permitida")
    _CFG.parent.mkdir(parents=True, exist_ok=True)
    _CFG.write_text(json.dumps({"path": str(p)}, indent=2), encoding="utf-8")
    return {"ok": True, "path": str(p)}


def resolve_workdir(task_id: str, project_root: Optional[str] = None) -> Path:
    """Si hay proyecto de usuario, trabaja ahí; si no, workspace/<task_id>."""
    if project_root:
        p = Path(project_root).expanduser().resolve()
        if p.is_dir():
            return p
    cfg = load_project()
    if cfg.get("ok") and cfg.get("path"):
        return Path(str(cfg["path"]))
    d = WORKSPACE_ROOT / task_id
    d.mkdir(parents=True, exist_ok=True)
    return d
