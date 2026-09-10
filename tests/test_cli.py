"""Tests unitarios del adaptador CLI (sin GPU / sin Ollama)."""
from __future__ import annotations

import json
from pathlib import Path

from ottercode_cli.sessions import list_sessions, load_session, save_session, sessions_dir
from ottercode_cli.slash import parse_slash


def test_argv_plain_after_chat():
    from ottercode_cli.main import _parse
    a = _parse(["chat", "--plain"])
    assert a.cmd == "chat" and a.plain
    b = _parse(["--plain", "chat"])
    assert b.plain and b.cmd == "chat"
    c = _parse(["edit", "foo.py"])
    assert c.cmd == "edit" and c.pos == ["foo.py"]


def test_parse_slash_help():
    c = parse_slash("/help")
    assert c and c.name == "help"


def test_parse_slash_run():
    c = parse_slash("/run pytest -q")
    assert c and c.name == "run" and c.arg == "pytest -q"


def test_parse_not_slash():
    assert parse_slash("hola") is None


def test_session_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("OTTERCODE_SESSIONS_DIR", str(tmp_path))
    p = save_session({"id": "abc", "task": "hola", "messages": [{"role": "user", "content": "x"}]})
    assert p.is_file()
    rec = load_session("abc")
    assert rec and rec["task"] == "hola"
    rows = list_sessions()
    assert any(r["id"] == "abc" for r in rows)


def test_esc_markup_brackets():
    from ottercode_cli.tui import _esc
    assert "\\[" in _esc("a[b]c")


def test_friendly_error_abort():
    from ottercode_cli.core_bridge import friendly_error
    from backend.runstate import AbortRequested
    assert "abort" in friendly_error(AbortRequested()).lower()


def test_abort_run_noop():
    from ottercode_cli.core_bridge import CliState, abort_run
    from pathlib import Path
    st = CliState(workdir=Path("."), model="x", session_id="s", task_id="t")
    assert abort_run(st) is False
    st.current_run = type("R", (), {"aborted": False})()
    assert abort_run(st) is True
    assert st.current_run.aborted is True


def test_vram_usage_tuple():
    from ottercode_cli.core_bridge import vram_usage
    u, t = vram_usage()
    assert t > 0
    assert u >= 0


def test_list_models_type():
    from ottercode_cli.core_bridge import list_models
    m = list_models()
    assert isinstance(m, list)


def test_slash_help_text():
    from ottercode_cli.slash import SLASH_HELP
    for name in ("/help", "/status", "/diff", "/apply", "/reject", "/run", "/explain"):
        assert name.split()[0] in SLASH_HELP or name[1:] in SLASH_HELP
