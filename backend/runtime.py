# OtterCode — estado vivo: RUN_LOCK, ACTIVE_RUN, ACTIVITY y bootstrap de arranque
from __future__ import annotations
import threading
import time
from typing import Any, Dict
from backend.runstate import OtterRun  # noqa: E402


RUN_LOCK = threading.Lock()          # relevo secuencial: UNA misión a la vez
ACTIVE_RUN: Dict[str, OtterRun] = {}


def _force_stop_run(run: OtterRun) -> None:
    """Abort inmediato: marca el flag Y cierra el socket de generación para no
    esperar al read-timeout de 300 s (v3.3 · abort agresivo)."""
    run.aborted = True
    resp = getattr(run, "_active_resp", None)
    if resp is not None:
        try:
            resp.close()
        except Exception:  # noqa: BLE001
            pass

# ---------------------------------------------------------------------------
# Actividad en vivo (vista Agentes): snapshot del estado de la balsa
# ---------------------------------------------------------------------------

ACTIVITY_LOCK = threading.Lock()
ACTIVITY: Dict[str, Any] = {"running": False, "task_id": None, "task": "",
                            "mode": "", "loop_mode": False,
                            "started_at": None, "elapsed_s": 0.0,
                            "agent": None, "agent_nombre": "", "agent_icon": "",
                            "iteration": 0, "last_tool": None, "last_tool_ok": None}


def _activity_set(**kv: Any) -> None:
    with ACTIVITY_LOCK:
        ACTIVITY.update(kv)


def _activity_finish(status: str) -> None:
    with ACTIVITY_LOCK:
        started = ACTIVITY.get("started_at")
        elapsed = round(time.time() - started, 1) if isinstance(started, (int, float)) else 0.0
        ACTIVITY.update(running=False, status=status, elapsed_s=elapsed)
