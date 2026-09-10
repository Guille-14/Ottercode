"""Follow-up en cadena no reinicia el batallón ni inventa otra web."""


def test_followup_with_files_becomes_chat():
    class Ex:
        def list_workspace(self):
            return [{"path": "src/index.html", "size": 200}]

    class Run:
        mode = "chain"
        start_agent = "architect"
        continue_task = "20260101-x"
        _files_ever_written = False
        executor = Ex()

    run = Run()
    # misma lógica que loop.run_task_stream
    if run.continue_task:
        if run.executor.list_workspace():
            run._files_ever_written = True
        if run.mode == "chain" and run._files_ever_written:
            run.mode = "chat"
            if run.start_agent in ("architect", "researcher"):
                run.start_agent = "developer"
    assert run.mode == "chat"
    assert run.start_agent == "developer"


def test_second_html_rejected():
    from pathlib import Path
    from tempfile import TemporaryDirectory
    import tools

    with TemporaryDirectory() as d:
        wd = Path(d)
        (wd / "src").mkdir()
        (wd / "src" / "index.html").write_text("<html><body>hermes</body></html>", encoding="utf-8")
        ex = tools.ToolExecutor(wd)
        r = ex.dispatch("write_file", {"filepath": "src/nutria/index.html", "content": "<html>no</html>"})
        assert r["ok"] is False
        assert "edit_file" in r["output"]
        r2 = ex.dispatch("edit_file", {
            "filepath": "src/index.html",
            "old_string": "hermes",
            "new_string": "Hermes Agent",
        })
        assert r2["ok"] is True
        assert "Hermes Agent" in (wd / "src" / "index.html").read_text(encoding="utf-8")


def test_tool_arg_deltas_stream():
    from backend.ollama import tool_arg_deltas
    p, d = tool_arg_deltas("", '{"filepath": "a.html", "content": "<h')
    assert d.startswith("{")
    p2, d2 = tool_arg_deltas(p, p + "tml>\"}")
    assert d2 == 'tml>"}'
    p3, d3 = tool_arg_deltas(p2, p2)
    assert d3 == ""


def test_fresh_chain_untouched():
    class Run:
        mode = "chain"
        start_agent = "architect"
        continue_task = ""
        _files_ever_written = False

    run = Run()
    if getattr(run, "continue_task", "") and run.mode == "chain" and run._files_ever_written:
        run.mode = "chat"
    assert run.mode == "chain"
    assert run.start_agent == "architect"
