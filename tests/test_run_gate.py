"""Lock huérfano: steal_if_stale deja enviar otra misión."""
from backend.runtime import ACTIVE_RUN, RUN_LOCK


def test_steal_stale_lock():
    ACTIVE_RUN.clear()
    assert RUN_LOCK.acquire(blocking=False)
    # simula cliente que cortó el SSE sin finally
    assert RUN_LOCK.locked()
    assert RUN_LOCK.steal_if_stale() is True
    assert not RUN_LOCK.locked()
    assert RUN_LOCK.acquire(blocking=False)
    RUN_LOCK.release()


def test_no_steal_if_live_run():
    class R:
        aborted = False

    ACTIVE_RUN.clear()
    assert RUN_LOCK.acquire(blocking=False)
    ACTIVE_RUN["x"] = R()  # type: ignore[assignment]
    assert RUN_LOCK.steal_if_stale() is False
    assert RUN_LOCK.locked()
    ACTIVE_RUN.clear()
    RUN_LOCK.release()
