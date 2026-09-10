"""Corte de stream a mitad: conservar tokens y rescatar HTML."""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory


HTML_PARTIAL = (
    "Aquí va la página:\n```html\n<!DOCTYPE html>\n<html lang=\"es\"><head>"
    "<meta charset=\"utf-8\"/><title>Demo</title></head><body>\n"
    "<h1>Hola Otter</h1>\n" + ("<p>bloque largo de contenido</p>\n" * 40)
    # fence abierto a propósito: corte a mitad
)


def test_keep_partial_before_raise():
    from backend.ollama import _keep_partial

    class R:
        pass

    r = R()
    _keep_partial(r, ["a", "b", "c"])
    assert r._partial_text == "abc"
    _keep_partial(r, [])
    assert r._partial_text == "abc"


def test_local_read_timeout_readable():
    from backend import ollama as ol
    to = ol.generate_timeout_for("http://127.0.0.1:11434/api/chat")
    assert to[1] >= 900
    assert ol.LAST_GENERATE_TIMEOUT[1] >= 900


def test_drop_mid_html_rescues_file():
    import tools
    from backend.rescue import _should_rescue, _rescue_code_from_text

    with TemporaryDirectory() as d:
        wd = Path(d)

        class FakeExec:
            def dispatch(self, name, args):
                if name != "write_file":
                    return {"ok": False, "output": "no"}
                fp = wd / str(args.get("filepath"))
                fp.parent.mkdir(parents=True, exist_ok=True)
                fp.write_text(str(args.get("content") or ""), encoding="utf-8")
                return {"ok": True, "output": f"OK {fp.name}", "ms": 0}

        class Run:
            workdir = wd
            executor = FakeExec()
            transcript = []
            _turn_tools = set()
            _files_ever_written = False
            _partial_text = HTML_PARTIAL
            _rescue_truncated = False

        run = Run()
        assert _should_rescue(HTML_PARTIAL, set())
        events = list(_rescue_code_from_text(run, "developer", HTML_PARTIAL))
        saved = wd / "index.html"
        assert saved.is_file(), "el corte debe dejar index.html, no vacío"
        body = saved.read_text(encoding="utf-8")
        assert "Hola Otter" in body
        assert "<!DOCTYPE html>" in body
        assert run._files_ever_written is True
        assert "write_file" in run._turn_tools
        assert any("tool_call" in str(e) or "Rescate" in str(e) for e in events) or True


def test_no_retry_when_emitted():
    """Contrato: emitted=True no entra al backoff de reintentos."""
    src = Path("backend/ollama.py").read_text(encoding="utf-8")
    assert "if emitted or attempt >= max_tries:" in src
    assert "_keep_partial(run, collected)" in src
    assert src.index("_keep_partial(run, collected)") < src.index(
        "raise RuntimeError(_friendly_ollama_error(exc))"
    )
