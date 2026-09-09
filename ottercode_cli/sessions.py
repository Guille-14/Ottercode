"""Sesiones persistentes en ~/.ottercode/sessions/."""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


def sessions_dir() -> Path:
    raw = os.environ.get("OTTERCODE_SESSIONS_DIR", "").strip()
    if raw:
        p = Path(raw).expanduser()
    else:
        p = Path.home() / ".ottercode" / "sessions"
    p.mkdir(parents=True, exist_ok=True)
    return p


def new_session_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]


def save_session(rec: Dict[str, Any]) -> Path:
    sid = rec.get("id") or new_session_id()
    rec["id"] = sid
    rec.setdefault("updated_at", datetime.now().isoformat(timespec="seconds"))
    path = sessions_dir() / f"{sid}.json"
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_session(sid: str) -> Optional[Dict[str, Any]]:
    path = sessions_dir() / f"{sid}.json"
    if not path.is_file():
        # prefijo
        matches = sorted(sessions_dir().glob(f"{sid}*.json"))
        if not matches:
            return None
        path = matches[-1]
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_sessions(limit: int = 30) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for p in sorted(sessions_dir().glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        rows.append({
            "id": d.get("id", p.stem),
            "date": d.get("updated_at") or d.get("created_at"),
            "model": d.get("model"),
            "task": (d.get("task") or "")[:80],
        })
        if len(rows) >= limit:
            break
    return rows
