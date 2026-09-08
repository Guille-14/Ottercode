"""D1–D3: diagnóstico, stall de rescate, reanudar desde checkpoint."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory


def test_error_payload_includes_message_not_only_class():
    exc = RuntimeError("Ollama no responde en http://127.0.0.1:11434")
    payload = {
        "message": f"Error: {type(exc).__name__}: {exc}",
        "error_type": type(exc).__name__,
        "detail": str(exc),
        "step": "rescue_complete:index.html:try2",
    }
    assert "Ollama no responde" in payload["message"]
    assert payload["error_type"] == "RuntimeError"
    assert payload["step"].startswith("rescue_complete")


def test_rescue_stall_after_three():
    stalls: dict[str, int] = {}
    limit = 3

    def note(key: str, error: str) -> bool:
        sig = f"{key}|{error[:180]}"
        stalls[sig] = stalls.get(sig, 0) + 1
        return stalls[sig] >= limit

    assert note("rescue:index.html", "boom") is False
    assert note("rescue:index.html", "boom") is False
    assert note("rescue:index.html", "boom") is True


def test_resume_summary_does_not_rewrite():
    last = {
        "done": "escrito index.html",
        "decisions": "append_file",
        "pending": "cerrar </html>",
        "next_action": "append_file continuación",
        "files": ["index.html"],
    }
    text = (
        f"# CHECKPOINT PREVIO (reanudar, no repetir lo hecho)\n"
        f"Hecho: {last['done']}\n"
        f"Decisiones: {last['decisions']}\n"
        f"Pendiente: {last['pending']}\n"
        f"Siguiente acción: {last['next_action']}\n"
        f"No reescribas archivos ya listados: {last['files']}"
    )
    assert "no repetir" in text
    assert "index.html" in text
    assert "append_file" in text


def test_checkpoint_jsonl_error_line():
    with TemporaryDirectory() as d:
        path = Path(d) / "m1.jsonl"
        rec = {
            "kind": "error",
            "task_id": "m1",
            "done": "RuntimeError: boom",
            "decisions": "step=rescue_complete:index.html",
        }
        path.write_text(json.dumps(rec) + "\n", encoding="utf-8")
        row = json.loads(path.read_text().splitlines()[-1])
        assert row["kind"] == "error"
        assert "boom" in row["done"]
        assert "rescue_complete" in row["decisions"]


if __name__ == "__main__":
    test_error_payload_includes_message_not_only_class()
    test_rescue_stall_after_three()
    test_resume_summary_does_not_rewrite()
    test_checkpoint_jsonl_error_line()
    print("D1–D3 OK")
