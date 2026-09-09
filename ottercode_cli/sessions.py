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


_TECH = (
    "error", "traceback", "exception", "failed", "errno", "http ",
    "file_exists", "old_string", ".py", ".html", "stack",
)


def compact_history(messages: List[Dict[str, Any]], tail_n: int = 6) -> List[Dict[str, Any]]:
    """T1-T2: resumen + cola reciente, preservando errores literales.

    No corta a ciegas. El modelo y el disco reciben el mismo recorte.
    """
    msgs = [m for m in (messages or []) if isinstance(m, dict)]
    if len(msgs) <= tail_n + 2:
        return list(msgs)
    tail = msgs[-tail_n:]
    old = msgs[:-tail_n]
    literals: List[str] = []
    for m in old:
        c = str(m.get("content") or "")
        low = c.lower()
        if any(k in low for k in _TECH):
            literals.append(c[:1200])
    blob = "\n".join(str(m.get("content") or "")[:400] for m in old)
    summary = (
        "[CHECKPOINT DE COMPACTACIÓN — resumen + cola reciente]\n"
        f"HECHO: {len(old)} mensajes anteriores compactados.\n"
        "ERRORES LITERALES:\n"
        + ("\n---\n".join(literals[-8:]) or "(ninguno)")
        + "\nDATOS CLAVE (extracto):\n"
        + blob[:1500]
        + "\nEl historial original sigue en sesión; el código está en disco."
    )
    return [{"role": "user", "content": summary}] + tail


def save_session(rec: Dict[str, Any]) -> Path:
    sid = rec.get("id") or new_session_id()
    rec["id"] = sid
    rec.setdefault("updated_at", datetime.now().isoformat(timespec="seconds"))
    msgs = rec.get("messages")
    if isinstance(msgs, list) and len(msgs) > 12:
        rec["messages"] = compact_history(msgs)
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
