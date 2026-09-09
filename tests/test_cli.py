"""Tests unitarios del adaptador CLI (sin GPU / sin Ollama)."""
from __future__ import annotations

import json
from pathlib import Path

from ottercode_cli.sessions import list_sessions, load_session, save_session, sessions_dir
from ottercode_cli.slash import parse_slash


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


def test_list_models_type():
    from ottercode_cli.core_bridge import list_models
    m = list_models()
    assert isinstance(m, list)


def test_slash_help_text():
    from ottercode_cli.slash import SLASH_HELP
    for name in ("/help", "/status", "/diff", "/apply", "/reject", "/run", "/explain"):
        assert name.split()[0] in SLASH_HELP or name[1:] in SLASH_HELP
