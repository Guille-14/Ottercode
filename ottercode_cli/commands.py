"""Despacho de slash y modos CLI (Rich), reutilizable por la TUI."""
from __future__ import annotations

import os
from typing import Callable, Optional

from ottercode_cli.core_bridge import CliState, rag_query, read_file, run_prompt, sandbox_run, status_blob, tree
from ottercode_cli.sessions import list_sessions, load_session
from ottercode_cli.slash import SLASH_HELP, SlashCmd, parse_slash

PrintFn = Callable[[str], None]


def handle_slash(state: CliState, cmd: SlashCmd, printer: PrintFn) -> bool:
    """True si el comando consume el turno (no enviar al LLM)."""
    try:
        return _handle_slash(state, cmd, printer)
    except Exception as exc:
        printer(f"error /{cmd.name}: {exc}")
        return True


def _handle_slash(state: CliState, cmd: SlashCmd, printer: PrintFn) -> bool:
    n, a = cmd.name, cmd.arg
    if n == "help":
        printer(SLASH_HELP)
        return True
    if n == "status":
        printer(status_blob(state))
        return True
    if n == "model":
        if a:
            state.model = a
            printer(f"modelo={state.model}")
            return True
        from ottercode_cli.core_bridge import list_models
        names = list_models()
        printer(f"actual={state.model}\n" + ("\n".join(names) if names else "(Ollama no lista modelos)"))
        return True
    if n in ("context", "config"):
        keys = [
            "OTTERCODE_LLM_BACKEND", "OTTERCODE_OLLAMA", "OTTERCODE_MODEL",
            "OTTERCODE_NUM_CTX", "OTTERCODE_NUM_PREDICT", "OTTERCODE_ASK_PERMISSIONS",
            "OTTERCODE_SANDBOX_REQUIRED", "OTTERCODE_TOKEN",
        ]
        lines = [f"{k}={os.environ.get(k, '')}" for k in keys]
        printer("\n".join(lines) + f"\nnum_ctx_efectivo ver /status\nmodelo={state.model}")
        return True
    if n == "files":
        printer(tree(state))
        return True
    if n == "open" and a:
        printer(read_file(state, a)[:8000])
        return True
    if n == "edit" and a:
        printer(f"Enfoque de edición: {a}\n" + read_file(state, a)[:4000])
        return True
    if n == "diff":
        printer(state.pending_diff or "(sin diff pendiente)")
        return True
    if n == "reject":
        state.pending_diff = ""
        printer("Diff descartado (no se revierten archivos ya escritos; usa git).")
        return True
    if n == "apply":
        printer("Los diffs de edit_file ya se aplican en disco al ejecutarse la tool. Nada extra.")
        return True
    if n == "run" and a:
        printer(sandbox_run(state, a))
        return True
    if n == "test":
        printer(sandbox_run(state, "python -m pytest -q || npm test --silent"))
        return True
    if n == "explain":
        err = state.last_error or state.last_cmd_out or "(no hay error reciente)"
        run_prompt(state, f"Explica este error y cómo arreglarlo:\n{err[:4000]}", lambda *_: None)
        return True
    if n == "learn":
        state.learn = True
        state.learn_topic = a
        printer(f"Modo learn: {a or 'general'}")
        return True
    if n == "quiz":
        run_prompt(state, f"Hazme un ejercicio corto de {state.learn_topic or 'programación'} y espera mi respuesta.", None)
        return True
    if n == "clear":
        state.messages.clear()
        printer("Chat limpio.")
        return True
    if n == "history":
        rows = list_sessions()
        printer("\n".join(f"{r['id']}  {r.get('date')}  {r.get('task')}" for r in rows) or "(sin sesiones)")
        return True
    if n == "resume" and a:
        rec = load_session(a)
        if not rec:
            printer("Sesión no encontrada.")
            return True
        state.session_id = rec.get("id") or state.session_id
        state.messages = list(rec.get("messages") or [])
        printer(f"Reanudada {state.session_id}")
        return True
    if n == "yolo":
        state.yolo = True
        os.environ["OTTERCODE_ASK_PERMISSIONS"] = "0"
        printer("YOLO: permisos off.")
        return True
    if n == "permissions":
        printer(f"ASK={os.environ.get('OTTERCODE_ASK_PERMISSIONS','1')} yolo={state.yolo}")
        return True
    if n == "mcp":
        printer(status_blob(state))
        return True
    if n == "tools":
        import tools as _tools
        names = getattr(_tools, "TOOL_NAMES", None) or list(getattr(_tools, "TOOLS", {}) or [])
        printer(", ".join(str(x) for x in list(names)[:40]))
        return True
    if n == "rag" and a:
        printer(rag_query(state, a))
        return True
    if n == "memory":
        printer(status_blob(state))
        return True
    if n == "profile":
        try:
            from backend.user_profile import inject_profile_block
            printer(inject_profile_block(state.workdir) or "(sin perfil)")
        except Exception as exc:
            printer(str(exc))
        return True
    if n == "compact":
        printer("Compactación: el motor la aplica en el siguiente turno si el ctx está lleno.")
        return True
    return False



