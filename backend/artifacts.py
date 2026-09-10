"""Galería de artefactos por sesión."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, List

from backend.config import WORKSPACE_ROOT
from backend.home import HOME, ensure_home

IMG = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
SKIP = {".git", "__pycache__", "node_modules", ".otter_rag.db"}


def index_session(session_id: str) -> List[Dict[str, Any]]:
    root = WORKSPACE_ROOT / session_id
    items: List[Dict[str, Any]] = []
    if not root.is_dir():
        return items
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP or part.startswith(".otter") for part in p.parts):
            continue
        if p.name == "ottercode_transcript.json":
            continue
        rel = str(p.relative_to(root))
        ext = p.suffix.lower()
        kind = "image" if ext in IMG else "file"
        try:
            st = p.stat()
        except OSError:
            continue
        items.append({
            "session_id": session_id,
            "path": rel,
            "type": kind,
            "size": st.st_size,
            "timestamp": st.st_mtime,
            "abs": str(p),
        })
    return items


def list_artifacts(session_id: str = "", limit: int = 50, q: str = "") -> Dict[str, Any]:
    ensure_home()
    out: List[Dict[str, Any]] = []
    sessions = [session_id] if session_id else (
        [d.name for d in WORKSPACE_ROOT.iterdir() if d.is_dir()] if WORKSPACE_ROOT.is_dir() else []
    )
    for sid in sessions[:40]:
        out.extend(index_session(sid))
    if q:
        ql = q.lower()
        out = [x for x in out if ql in x["path"].lower()]
    out.sort(key=lambda x: x.get("timestamp") or 0, reverse=True)
    return {"ok": True, "items": out[: max(1, min(int(limit or 50), 200))]}
