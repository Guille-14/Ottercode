"""Subagentes en background. Con Ollama local: cola GPU (no paralelo)."""
from __future__ import annotations

import threading
import time
import uuid
from typing import Any, Dict, List, Optional

_LOCK = threading.Lock()
WORKERS: Dict[str, Dict[str, Any]] = {}


def list_workers() -> List[Dict[str, Any]]:
    with _LOCK:
        return [dict(v) for v in WORKERS.values()]


def get_worker(hid: str) -> Optional[Dict[str, Any]]:
    with _LOCK:
        w = WORKERS.get(hid)
        return dict(w) if w else None


def delegate_task(
    task: str,
    context: str = "",
    model: str = "",
    skills: Optional[List[str]] = None,
    tools: Optional[List[str]] = None,
    timeout_seconds: int = 300,
    parent_id: str = "",
) -> Dict[str, Any]:
    hid = "sub-" + uuid.uuid4().hex[:10]
    w = {
        "id": hid,
        "task": task,
        "context": context,
        "model": model,
        "skills": skills or [],
        "tools": tools or [],
        "status": "queued",
        "started": time.time(),
        "elapsed": 0,
        "activity": "queued",
        "result": "",
        "steer": "",
        "stop": False,
        "parent_id": parent_id,
        "timeout": int(timeout_seconds or 300),
    }
    with _LOCK:
        WORKERS[hid] = w
    threading.Thread(target=_run, args=(hid,), daemon=True).start()
    return {"ok": True, "handle": hid}


def steer(hid: str, guidance: str) -> Dict[str, Any]:
    with _LOCK:
        if hid not in WORKERS:
            return {"ok": False, "error": "unknown"}
        WORKERS[hid]["steer"] = guidance
        WORKERS[hid]["activity"] = "steered"
    return {"ok": True}


def stop(hid: str) -> Dict[str, Any]:
    with _LOCK:
        if hid not in WORKERS:
            return {"ok": False, "error": "unknown"}
        WORKERS[hid]["stop"] = True
        WORKERS[hid]["status"] = "stopping"
    return {"ok": True}


def _run(hid: str) -> None:
    with _LOCK:
        w = WORKERS[hid]
        w["status"] = "waiting_gpu"
        w["activity"] = "waiting GPU"
    deadline = time.time() + int(WORKERS[hid]["timeout"])
    while time.time() < deadline:
        if WORKERS[hid]["stop"]:
            WORKERS[hid]["status"] = "stopped"
            return
        busy = False
        try:
            from backend.runtime import ACTIVE_RUN
            busy = bool(ACTIVE_RUN)
        except Exception:
            busy = False
        if not busy:
            break
        time.sleep(1)
    with _LOCK:
        WORKERS[hid]["status"] = "running"
        WORKERS[hid]["activity"] = "generating"
    prompt = WORKERS[hid]["task"] + "\n" + WORKERS[hid]["context"]
    if WORKERS[hid]["steer"]:
        prompt += "\n\nGuidance: " + WORKERS[hid]["steer"]
    text = _gen(WORKERS[hid].get("model") or "", prompt)
    with _LOCK:
        WORKERS[hid]["result"] = text
        WORKERS[hid]["status"] = "done"
        WORKERS[hid]["activity"] = "done"
        WORKERS[hid]["elapsed"] = time.time() - WORKERS[hid]["started"]
        parent = WORKERS[hid].get("parent_id")
    if parent:
        try:
            from backend.runtime import ACTIVE_RUN
            run = ACTIVE_RUN.get(parent)
            if run is not None:
                run.transcript.append({
                    "kind": "system",
                    "text": f"[subagente {hid}] {text[:1500]}",
                })
        except Exception:
            pass


def _gen(model: str, prompt: str) -> str:
    try:
        import requests
        from backend.config import DEFAULT_MODEL, OLLAMA_BASE_URL
        from backend.ollama import _ollama_ndjson_text
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": model or DEFAULT_MODEL,
                "prompt": prompt[:4000],
                "stream": False,
                "options": {"num_ctx": 2048, "num_predict": 500, "num_gpu": 99},
            },
            timeout=(8, 180),
        )
        resp.raise_for_status()
        return (_ollama_ndjson_text(resp.text) or "")[:8000]
    except Exception as exc:
        return f"(subagente sin GPU/LLM: {exc})"
