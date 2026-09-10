"""Historial SQLite de ejecuciones cron."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Dict, List

from backend.home import HOME, ensure_home

DB = HOME / "cron" / "executions.db"


def _conn() -> sqlite3.Connection:
    ensure_home()
    DB.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(str(DB))
    c.execute(
        """CREATE TABLE IF NOT EXISTS executions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id TEXT, state TEXT, started_at TEXT, ended_at TEXT,
            output TEXT, error TEXT
        )"""
    )
    return c


def record(job_id: str, state: str, output: str = "", error: str = "") -> int:
    c = _conn()
    cur = c.execute(
        "INSERT INTO executions (job_id, state, started_at, output, error) VALUES (?,?,?,?,?)",
        (job_id, state, datetime.now(timezone.utc).isoformat(), output[:8000], error[:2000]),
    )
    c.commit()
    rid = int(cur.lastrowid or 0)
    c.close()
    return rid


def finish(eid: int, state: str, output: str = "", error: str = "") -> None:
    c = _conn()
    c.execute(
        "UPDATE executions SET state=?, ended_at=?, output=?, error=? WHERE id=?",
        (state, datetime.now(timezone.utc).isoformat(), output[:8000], error[:2000], eid),
    )
    c.commit()
    c.close()


def latest_output(job_id: str) -> str:
    c = _conn()
    row = c.execute(
        "SELECT output FROM executions WHERE job_id=? AND state='completed' ORDER BY id DESC LIMIT 1",
        (job_id,),
    ).fetchone()
    c.close()
    return (row[0] if row else "") or ""


def list_for(job_id: str, limit: int = 20) -> List[Dict[str, Any]]:
    c = _conn()
    c.row_factory = sqlite3.Row
    rows = c.execute(
        "SELECT * FROM executions WHERE job_id=? ORDER BY id DESC LIMIT ?",
        (job_id, limit),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]
