"""CRUD de jobs en ~/.ottercode/cron/jobs.json (escritura atómica)."""
from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.cron.parser import is_recurring, next_run_at
from backend.home import HOME, ensure_home

JOBS_PATH = HOME / "cron" / "jobs.json"
_LOCK = threading.Lock()


def _empty() -> Dict[str, Any]:
    return {"jobs": []}


def load_jobs() -> List[Dict[str, Any]]:
    ensure_home()
    if not JOBS_PATH.exists():
        return []
    try:
        data = json.loads(JOBS_PATH.read_text(encoding="utf-8"))
        return list(data.get("jobs") or [])
    except (OSError, json.JSONDecodeError):
        return []


def _save(jobs: List[Dict[str, Any]]) -> None:
    ensure_home()
    JOBS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"jobs": jobs}, ensure_ascii=False, indent=2)
    fd, tmp = tempfile.mkstemp(dir=str(JOBS_PATH.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(payload)
        os.replace(tmp, JOBS_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get_job(ident: str) -> Optional[Dict[str, Any]]:
    key = (ident or "").strip().lower()
    for j in load_jobs():
        if str(j.get("id", "")).lower() == key or str(j.get("name", "")).lower() == key:
            return j
    return None


def list_jobs() -> List[Dict[str, Any]]:
    return load_jobs()


def create_job(fields: Dict[str, Any], *, from_agent: bool = False) -> Dict[str, Any]:
    from backend.home import cfg_get
    if from_agent and not cfg_get("cron", "allow_agent_scheduling", default=False):
        return {"ok": False, "error": "recursive scheduling bloqueado"}
    sched = str(fields.get("schedule") or "").strip()
    if not sched:
        return {"ok": False, "error": "schedule requerido"}
    try:
        nxt = next_run_at(sched)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    job = {
        "id": "job-" + uuid.uuid4().hex[:10],
        "name": str(fields.get("name") or fields.get("prompt") or "job")[:80],
        "schedule": sched,
        "prompt": str(fields.get("prompt") or ""),
        "skills": list(fields.get("skills") or []),
        "script": str(fields.get("script") or ""),
        "no_agent": bool(fields.get("no_agent")),
        "deliver": str(fields.get("deliver") or "origin"),
        "workdir": str(fields.get("workdir") or ""),
        "context_from": list(fields.get("context_from") or []),
        "continuity": bool(fields.get("continuity")),
        "model": str(fields.get("model") or ""),
        "provider": str(fields.get("provider") or ""),
        "reasoning_effort": str(fields.get("reasoning_effort") or ""),
        "enabled_toolsets": list(fields.get("enabled_toolsets") or []),
        "paused": bool(fields.get("paused")),
        "paused_reason": str(fields.get("paused_reason") or ""),
        "repeat": fields.get("repeat", 1 if not is_recurring(sched) else "forever"),
        "runs_left": fields.get("repeat", None),
        "next_run_at": nxt.isoformat(),
        "last_status": "",
        "last_delivery_error": "",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "pinned_model": bool(fields.get("model")),
        "from_agent": from_agent,
    }
    with _LOCK:
        jobs = load_jobs()
        jobs.append(job)
        _save(jobs)
    return {"ok": True, "job": job}


def update_job(ident: str, changes: Dict[str, Any]) -> Dict[str, Any]:
    with _LOCK:
        jobs = load_jobs()
        key = (ident or "").strip().lower()
        for i, j in enumerate(jobs):
            if str(j.get("id", "")).lower() == key or str(j.get("name", "")).lower() == key:
                j.update({k: v for k, v in changes.items() if v is not None})
                if "schedule" in changes and changes["schedule"]:
                    try:
                        j["next_run_at"] = next_run_at(str(changes["schedule"])).isoformat()
                    except ValueError as exc:
                        return {"ok": False, "error": str(exc)}
                jobs[i] = j
                _save(jobs)
                return {"ok": True, "job": j}
    return {"ok": False, "error": "job no encontrado"}


def remove_job(ident: str) -> Dict[str, Any]:
    with _LOCK:
        jobs = load_jobs()
        key = (ident or "").strip().lower()
        keep = [j for j in jobs if str(j.get("id", "")).lower() != key and str(j.get("name", "")).lower() != key]
        if len(keep) == len(jobs):
            return {"ok": False, "error": "job no encontrado"}
        _save(keep)
    return {"ok": True, "removed": ident}


def pause_job(ident: str, reason: str = "") -> Dict[str, Any]:
    return update_job(ident, {"paused": True, "paused_reason": reason})


def resume_job(ident: str) -> Dict[str, Any]:
    j = get_job(ident)
    if not j:
        return {"ok": False, "error": "job no encontrado"}
    try:
        nxt = next_run_at(j.get("schedule") or "")
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    return update_job(ident, {"paused": False, "paused_reason": "", "next_run_at": nxt.isoformat()})
