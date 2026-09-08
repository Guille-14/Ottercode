#!/usr/bin/env python3
"""Tests unitarios sin GPU (Neo §9)."""
from __future__ import annotations

import ast
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tools
from backend.config import APP_VERSION, _resolve_llm_backend, native_tools_enabled


def test_version():
    assert APP_VERSION == "3.0.0"


def test_schemas_allowed():
    g = tools.get_ollama_tools(["read_file"])
    names = [t["function"]["name"] for t in g]
    assert "weather" not in names
    assert "read_file" in names
    rf = next(t for t in g if t["function"]["name"] == "read_file")
    props = rf["function"]["parameters"]["properties"]
    assert "filepath" in props
    ed = tools.get_ollama_tools(["edit_file", "apply_patch"])
    n2 = [t["function"]["name"] for t in ed]
    assert "apply_patch" in n2 and "weather" not in n2
    ap = next(t for t in ed if t["function"]["name"] == "apply_patch")
    assert "patch" in ap["function"]["parameters"]["properties"]


def test_api_url_ignored():
    with mock.patch.dict(os.environ, {"OTTERCODE_API": "http://127.0.0.1:8099", "OTTERCODE_LLM_BACKEND": ""}, clear=False):
        # re-resolve with current env via the function
        assert _resolve_llm_backend() in ("ollama", "openai")


def test_native_gate():
    os.environ.pop("OTTERCODE_NATIVE_TOOLS", None)
    assert native_tools_enabled("qwen2.5-coder:7b") is True
    os.environ["OTTERCODE_NATIVE_TOOLS"] = "off"
    assert native_tools_enabled("qwen2.5-coder:7b") is False
    os.environ["OTTERCODE_NATIVE_TOOLS"] = "on"
    assert native_tools_enabled("tinyllama") is True
    os.environ.pop("OTTERCODE_NATIVE_TOOLS", None)


def test_llm_request_allowlist():
    os.environ["OTTERCODE_NATIVE_TOOLS"] = "on"
    from backend.ollama import _llm_request
    from backend.runstate import OtterRun
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        run = OtterRun("t", "x", "qwen2.5-coder:7b", False, "chat", "agent", Path(d))
        run._native_allowed = ["read_file", "write_file"]
        url, payload = _llm_request(run, "sys", "hola", agent_id="agent")
        assert "/api/chat" in url
        names = [t["function"]["name"] for t in payload.get("tools") or []]
        assert "read_file" in names
        assert "weather" not in names
        assert payload["options"]["temperature"] == 0.2
    os.environ.pop("OTTERCODE_NATIVE_TOOLS", None)


def test_gitignore_glob():
    with tempfile.TemporaryDirectory() as d:
        Path(d, ".gitignore").write_text("secret.txt\nnode_modules\n")
        Path(d, "secret.txt").write_text("x")
        Path(d, "ok.py").write_text("y")
        (Path(d) / "node_modules").mkdir()
        (Path(d) / "node_modules" / "a.js").write_text("z")
        ex = tools.ToolExecutor(d)
        r = ex.dispatch("glob_files", {"pattern": "**/*"})
        assert r["ok"]
        assert "ok.py" in r["output"]
        assert "secret.txt" not in r["output"]


def test_developer_has_patch():
    from backend.agents import get_agent
    d = get_agent("developer")
    assert "apply_patch" in d.tools_disponibles
    assert "git_commit" in d.tools_disponibles


def test_apply_patch_search_replace():
    with tempfile.TemporaryDirectory() as d:
        ex = tools.ToolExecutor(d)
        ex.dispatch("write_file", {"filepath": "a.py", "content": "def foo():\n    return 1\n"})
        r = ex.dispatch("apply_patch", {
            "filepath": "a.py",
            "patch": "<<<<<<< SEARCH\n    return 1\n=======\n    return 2\n>>>>>>> REPLACE",
        })
        assert r["ok"], r
        assert "return 2" in Path(d, "a.py").read_text()
        assert "```diff" in r["output"]


def test_read_file_paging():
    with tempfile.TemporaryDirectory() as d:
        ex = tools.ToolExecutor(d)
        ex.dispatch("write_file", {"filepath": "b.txt", "content": "l1\nl2\nl3\nl4\n"})
        r = ex.dispatch("read_file", {"filepath": "b.txt", "offset": 2, "limit": 2})
        assert r["ok"] and "l2" in r["output"] and "l4" not in r["output"]


def test_git_commit():
    with tempfile.TemporaryDirectory() as d:
        ex = tools.ToolExecutor(d)
        ex.dispatch("write_file", {"filepath": "x.txt", "content": "hola\n"})
        import subprocess
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.email", "t@t"], cwd=d, check=True)
        subprocess.run(["git", "config", "user.name", "t"], cwd=d, check=True)
        r = ex.dispatch("git_commit", {"message": "feat: hola"})
        assert r["ok"], r


def test_sandbox_fail_closed():
    os.environ["OTTERCODE_SANDBOX_REQUIRED"] = "1"
    import sandbox
    with tempfile.TemporaryDirectory() as d:
        s = sandbox.SandboxExecutor(d)
        s._available = False
        s._bwrap_path = None
        res = s.run("echo hi", timeout=5)
        assert res["returncode"] != 0
        assert "SANDBOX_REQUIRED" in (res.get("stderr") or "")
    os.environ["OTTERCODE_SANDBOX_REQUIRED"] = "0"


def test_otter_no_weather():
    from backend.agents import get_agent
    a = get_agent("agent")
    assert "weather" not in a.tools_disponibles
    assert "uuid_gen" not in a.tools_disponibles
    assert "apply_patch" in a.tools_disponibles


if __name__ == "__main__":
    fails = []
    for name, fn in list(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print("PASS", name)
            except Exception as e:
                print("FAIL", name, e)
                fails.append(name)
    sys.exit(1 if fails else 0)
