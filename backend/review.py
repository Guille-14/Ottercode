"""Background review post-turno. En Ollama local se difiere hasta GPU idle."""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from backend.home import HOME, cfg_get
from backend.skill_creator import maybe_create_from_run

_QUEUE: List[Dict[str, Any]] = []
_LOCK = threading.Lock()
_STARTED = False


def _review_cfg() -> Dict[str, Any]:
    return cfg_get("auxiliary", "background_review", default={}) or {}


def gpu_idle() -> bool:
    try:
        from backend.runtime import ACTIVE_RUN
        if ACTIVE_RUN:
            return False
    except Exception:
        pass
    return True


def enqueue_review(run: Any) -> None:
    cfg = _review_cfg()
    if not cfg.get("enabled", True):
        return
    payload = {
        "ts": time.time(),
        "task_id": getattr(run, "task_id", ""),
        "task": getattr(run, "task_text", ""),
        "transcript": list(getattr(run, "transcript", []) or [])[-40:],
        "run": run,
    }
    with _LOCK:
        _QUEUE.append(payload)
    _ensure_worker()


def _ensure_worker() -> None:
    global _STARTED
    with _LOCK:
        if _STARTED:
            return
        _STARTED = True
    threading.Thread(target=_loop, daemon=True, name="otter-review").start()


def _loop() -> None:
    max_age = float(_review_cfg().get("defer_max_age_s") or 1800)
    while True:
        time.sleep(4)
        item = None
        with _LOCK:
            if not _QUEUE:
                continue
            defer = str(_review_cfg().get("defer") or "auto")
            aged = time.time() - _QUEUE[0]["ts"] > max_age
            if defer in ("auto", "true", "1", True) and not gpu_idle() and not aged:
                continue
            item = _QUEUE.pop(0)
        try:
            _process(item)
        except Exception:
            pass


def _process(item: Dict[str, Any]) -> None:
    run = item.get("run")
    notes: List[str] = []
    created = None
    if run is not None:
        created = maybe_create_from_run(run)
        if created and created.get("ok"):
            notes.append(f"Skill '{created.get('name')}' creada")
            _notify(item.get("task_id"), f"💾 Skill '{created.get('name')}' creada")
    # Heurística de memoria (sin LLM extra si MEMORY_LLM=0 / GPU ocupada)
    if os.environ.get("OTTERCODE_MEMORY_LLM", "1") not in ("0", "false"):
        try:
            from backend.memory import harvest_memory
            if run is not None and gpu_idle():
                last = ""
                for ev in reversed(item.get("transcript") or []):
                    if ev.get("kind") == "agent" and ev.get("text"):
                        last = str(ev["text"])
                        break
                harvest_memory(run, "", last)
                notes.append("Memoria actualizada")
                _notify(item.get("task_id"), "💾 Memoria actualizada")
        except Exception:
            pass
    log = HOME / "cron" / "review.log"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), "notes": notes, "task": item.get("task_id")}, ensure_ascii=False) + "\n")
    except OSError:
        pass


def _notify(task_id: str, text: str) -> None:
    try:
        from backend.runtime import ACTIVE_RUN
        run = ACTIVE_RUN.get(task_id) if task_id else None
        if run is not None:
            run.transcript.append({"kind": "system", "text": text})
    except Exception:
        pass
    try:
        from events import SseEvent, sse
        # no hay cola SSE aquí; el chat verá el system en transcript al recargar
    except Exception:
        pass


def queue_size() -> int:
    with _LOCK:
        return len(_QUEUE)
