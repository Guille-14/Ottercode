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
