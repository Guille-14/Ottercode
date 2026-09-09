from backend.subagents import delegate_task, get_worker, steer, stop

def test_delegate_returns_handle():
    r = delegate_task("noop", timeout_seconds=1)
    assert r["ok"] and r["handle"].startswith("sub-")
    steer(r["handle"], "sigue")
    stop(r["handle"])
    w = get_worker(r["handle"])
    assert w is not None
