"""Tick cada 60s con file lock. GPU exclusiva: no lanza agente si hay misión."""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from backend.cron.executions import finish, latest_output, record
from backend.cron.jobs import get_job, load_jobs, update_job
from backend.cron.parser import next_run_at
from backend.home import HOME, cfg_get, ensure_home

LOCK = HOME / "cron" / ".tick.lock"
_THREAD = False


def start_scheduler() -> None:
    global _THREAD
    if not cfg_get("cron", "enabled", default=True):
        return
    if _THREAD:
        return
    _THREAD = True
    threading.Thread(target=_loop, daemon=True, name="otter-cron").start()


def _loop() -> None:
    while True:
        try:
            tick_once()
        except Exception:
            pass
        time.sleep(60)


def tick_once() -> int:
    ensure_home()
    ran = 0
    fd = None
    try:
        fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        try:
            age = time.time() - LOCK.stat().st_mtime
            if age > 120:
                LOCK.unlink(missing_ok=True)
                fd = os.open(str(LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            else:
                return 0
        except OSError:
            return 0
    try:
        now = datetime.now(timezone.utc)
        for job in load_jobs():
            if job.get("paused"):
                continue
            nxt = job.get("next_run_at") or ""
            try:
                when = datetime.fromisoformat(str(nxt).replace("Z", "+00:00"))
                if when.tzinfo is None:
                    when = when.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
            if when > now:
                continue
            if _run_job(job):
                ran += 1
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass
        LOCK.unlink(missing_ok=True)
    return ran


def _run_job(job: Dict[str, Any], *, force: bool = False) -> bool:
    if cfg_get("cron", "preflight", default=True):
        blocked = _preflight(job)
        if blocked:
            update_job(job["id"], {"last_status": "blocked_config"})
            return False
    if cfg_get("cron", "model_drift_guard", default=True) and not job.get("pinned_model"):
        default = str(cfg_get("model", "default", default="") or "")
        if job.get("_seen_default") and job["_seen_default"] != default:
            update_job(job["id"], {"last_status": "blocked_config", "paused": True, "paused_reason": "model drift"})
            return False
        update_job(job["id"], {"_seen_default": default})
    eid = record(job["id"], "claimed")
    record(job["id"], "running")
    out = ""
    err = ""
    state = "completed"
    try:
        script_out, wake = _maybe_script(job)
        if job.get("no_agent"):
            out = script_out
            if not out.strip():
                state = "completed"
            finish(eid, state, out, "")
            _deliver(job, out)
            _advance(job)
            return True
        if wake is False:
            finish(eid, "completed", script_out, "")
            _advance(job)
            return True
        # GPU: no invocar LLM si hay misión activa
        try:
            from backend.runtime import ACTIVE_RUN
            if ACTIVE_RUN and not force:
                record(job["id"], "unknown")
                return False
        except Exception:
            pass
        ctx = _context_block(job)
        prompt = (job.get("prompt") or "") + (("\n\n" + ctx) if ctx else "")
        out = _agent_stub(job, prompt)
        _save_output(job["id"], out)
        _deliver(job, out)
    except Exception as exc:
        state = "failed"
        err = str(exc)
    finish(eid, state, out, err)
    update_job(job["id"], {"last_status": state, "last_delivery_error": ""})
    _advance(job)
    return True


def run_now(ident: str) -> Dict[str, Any]:
    j = get_job(ident)
    if not j:
        return {"ok": False, "error": "job no encontrado"}
    threading.Thread(target=_run_job, args=(j,), kwargs={"force": True}, daemon=True).start()
    return {"ok": True, "started": j["id"]}


def _preflight(job: Dict[str, Any]) -> Optional[str]:
    script = job.get("script") or ""
    if script:
        p = Path(script)
        if not p.is_absolute():
            p = HOME / "scripts" / script
        if not p.is_file():
            return "script missing"
    return None


def _maybe_script(job: Dict[str, Any]) -> tuple[str, Optional[bool]]:
    script = job.get("script") or ""
    if not script:
        return "", None
    p = Path(script)
    if not p.is_absolute():
        p = HOME / "scripts" / script
    env = {k: v for k, v in os.environ.items() if "KEY" not in k.upper() and "TOKEN" not in k.upper() and "SECRET" not in k.upper()}
    try:
        proc = subprocess.run(
            ["bash", str(p)] if p.suffix == ".sh" else ["python3", str(p)],
            cwd=str(job.get("workdir") or HOME),
            capture_output=True, text=True, timeout=120, env=env,
        )
    except Exception as exc:
        raise RuntimeError(f"script: {exc}")
    out = (proc.stdout or "").strip()
    if proc.returncode != 0:
        raise RuntimeError(f"script exit {proc.returncode}: {(proc.stderr or '')[:400]}")
    wake: Optional[bool] = None
    last = out.splitlines()[-1] if out else ""
    try:
        obj = json.loads(last)
        if isinstance(obj, dict) and "wakeAgent" in obj:
            wake = bool(obj["wakeAgent"])
            out = "\n".join(out.splitlines()[:-1]).strip()
    except json.JSONDecodeError:
        pass
    return out, wake


def _context_block(job: Dict[str, Any]) -> str:
    chunks = []
    if job.get("continuity"):
        chunks.append("## Continuity\n" + latest_output(job["id"])[:2000])
    for oid in job.get("context_from") or []:
        chunks.append(f"## From {oid}\n" + latest_output(str(oid))[:2000])
    return "\n\n".join(c for c in chunks if c.strip())


def _save_output(job_id: str, text: str) -> None:
    d = HOME / "cron" / "output" / job_id
    d.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    (d / f"{ts}.md").write_text(text or "", encoding="utf-8")


def _agent_stub(job: Dict[str, Any], prompt: str) -> str:
    """Generación corta Ollama; si falla, deja el prompt como output."""
    model = job.get("model") or cfg_get("model", "default", default="qwen2.5-coder:7b")
    try:
        import requests
        from backend.config import OLLAMA_BASE_URL
        from backend.ollama import _ollama_ndjson_text
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": model,
                "prompt": prompt[:4000],
                "stream": False,
                "options": {"num_ctx": 2048, "num_predict": 400, "num_gpu": 99},
            },
            timeout=(5, 120),
        )
        resp.raise_for_status()
        return (_ollama_ndjson_text(resp.text) or prompt)[:8000]
    except Exception:
        return prompt[:8000]


def _deliver(job: Dict[str, Any], text: str) -> None:
    try:
        from backend.delivery import deliver
        deliver(str(job.get("deliver") or "local"), text, meta={"job": job.get("id")})
    except Exception as exc:
        update_job(job["id"], {"last_delivery_error": str(exc)})


def _advance(job: Dict[str, Any]) -> None:
    left = job.get("runs_left")
    if left not in (None, "forever") and str(left).isdigit():
        n = int(left) - 1
        if n <= 0:
            update_job(job["id"], {"paused": True, "paused_reason": "repeat exhausted", "runs_left": 0})
            return
        update_job(job["id"], {"runs_left": n})
    try:
        nxt = next_run_at(job.get("schedule") or "")
        update_job(job["id"], {"next_run_at": nxt.isoformat()})
    except ValueError:
        update_job(job["id"], {"paused": True, "paused_reason": "bad schedule"})
