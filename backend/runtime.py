# OtterCode — estado vivo: RUN_LOCK, ACTIVE_RUN, ACTIVITY y bootstrap de arranque
from __future__ import annotations
import threading
import time
from typing import Any, Dict
from backend.runstate import OtterRun  # noqa: E402


class _RunGate:
    """Relevo 1 a 1. Permite soltar un lock huérfano (sin ACTIVE_RUN vivo)."""

    def __init__(self) -> None:
        self._cv = threading.Condition()
        self._held = False

    def locked(self) -> bool:
        with self._cv:
            return self._held

    def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
        deadline = None if timeout is None or timeout < 0 else (time.time() + float(timeout))
        with self._cv:
            while self._held:
                if not blocking:
                    return False
                if deadline is None:
                    self._cv.wait()
                else:
                    left = deadline - time.time()
                    if left <= 0:
                        return False
                    self._cv.wait(left)
            self._held = True
            return True

    def release(self) -> None:
        with self._cv:
            self._held = False
            self._cv.notify_all()

    def steal_if_stale(self) -> bool:
        """Si el relevo está cogido pero no hay misión viva, lo libera."""
        with self._cv:
            live = [
                r for r in ACTIVE_RUN.values()
                if r is not None and not getattr(r, "aborted", False)
            ]
            if self._held and not live:
                self._held = False
                self._cv.notify_all()
                return True
            return False


RUN_LOCK = _RunGate()          # relevo secuencial: UNA misión a la vez
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
