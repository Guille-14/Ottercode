from datetime import datetime, timezone
from backend.cron.parser import parse_schedule, next_run_at
from backend.cron.jobs import create_job, list_jobs, remove_job, pause_job, resume_job

def test_parser_in_and_every():
    now = datetime(2026, 1, 1, tzinfo=timezone.utc)
    kind, nxt, rec = parse_schedule("in 30m", now)
    assert kind == "once" and not rec
    assert (nxt - now).total_seconds() == 1800
    kind, nxt, rec = parse_schedule("every 2h", now)
    assert rec and kind == "every"

def test_cron_lifecycle(tmp_path, monkeypatch):
    monkeypatch.setenv("OTTERCODE_HOME", str(tmp_path / "oc"))
    from backend import home as H
    H.HOME = tmp_path / "oc"
    import backend.cron.jobs as J
    J.JOBS_PATH = H.HOME / "cron" / "jobs.json"
    r = create_job({"schedule": "in 30m", "prompt": "ping", "name": "n1"})
    assert r["ok"]
    assert any(j["name"] == "n1" for j in list_jobs())
    pause_job("n1")
    resume_job("n1")
    remove_job("n1")
    assert not any(j["name"] == "n1" for j in list_jobs())
