"""Punto de entrada: ottercode / python -m ottercode_cli."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from ottercode_cli.commands import handle_slash
from ottercode_cli.core_bridge import CliState, new_state, run_prompt, sandbox_run, status_blob
from ottercode_cli.sessions import list_sessions, load_session
from ottercode_cli.slash import SLASH_HELP, parse_slash


def _repl(state: CliState, first: str = "") -> int:
    """REPL Rich/plain si no hay TUI o --plain."""
    print("OtterCode CLI · /help para comandos · Ctrl-D para salir")
    print(status_blob(state).split("\n")[0])
    pending = first
    while True:
        if pending:
            line, pending = pending, ""
        else:
            try:
                line = input("otter> ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
        if not line:
            continue
        sl = parse_slash(line)
        if sl:
            handle_slash(state, sl, print)
            continue
        def on_ev(name: str, data: dict) -> None:
            if name == "token":
                sys.stdout.write(str(data.get("token") or ""))
                sys.stdout.flush()
            elif name == "tool_call":
                print(f"\n⚙ {data.get('tool')}")
            elif name == "tool_result":
                ok = "ok" if data.get("ok") else "err"
                print(f"\n[{ok}] {(str(data.get('output') or ''))[:400]}")
        print()
        run_prompt(state, line, on_ev)
        print()
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="ottercode", description="Agente de código local (Ollama) en terminal")
    sub = p.add_subparsers(dest="cmd")
    sub.add_parser("chat", help="sesión conversacional")
    pe = sub.add_parser("edit", help="editar archivo")
    pe.add_argument("archivo")
    pr = sub.add_parser("run", help="comando en sandbox")
    pr.add_argument("comando", nargs=argparse.REMAINDER)
    pl = sub.add_parser("learn", help="modo tutor")
    pl.add_argument("tema", nargs="*")
    sub.add_parser("explain", help="explicar último error")
    sub.add_parser("review", help="revisar workspace")
    sub.add_parser("test", help="ejecutar tests")
    pres = sub.add_parser("resume", help="reanudar sesión")
    pres.add_argument("session_id", nargs="?")
    sub.add_parser("sessions", help="listar sesiones")
    p.add_argument("--tui", action="store_true", help="forzar TUI Textual")
    p.add_argument("--plain", action="store_true", help="REPL sin Textual")
    p.add_argument("--workdir", default="", help="workspace (default: cwd)")
    args = p.parse_args(argv)

    wd = Path(args.workdir).resolve() if args.workdir else None
    state = new_state(workdir=wd)

    cmd = args.cmd or "chat"
    if cmd == "sessions":
        for r in list_sessions():
            print(f"{r['id']}\t{r.get('date')}\t{r.get('task')}")
        return 0
    if cmd == "resume":
        sid = getattr(args, "session_id", None) or ""
        rec = load_session(sid) if sid else (list_sessions()[:1] or [None])[0]
        if isinstance(rec, dict) and rec.get("id") and "messages" not in rec:
            rec = load_session(str(rec["id"]))
        if not rec:
            print("No hay sesión.")
            return 1
        state.session_id = rec.get("id") or state.session_id
        state.messages = list(rec.get("messages") or [])
        print(f"Reanudada {state.session_id}")
        cmd = "chat"
    if cmd == "learn":
        state.learn = True
        state.learn_topic = " ".join(getattr(args, "tema", []) or [])
        os.environ["OTTERCODE_LEARNING_MODE"] = "1"
        seed = f"Quiero aprender {state.learn_topic or 'programación'}. Empieza con un plan corto y el primer ejercicio."
        if args.plain or not args.tui:
            return _repl(state, seed)
        from ottercode_cli.tui import run_tui
        return run_tui(state, seed)
    if cmd == "run":
        c = " ".join(getattr(args, "comando", []) or [])
        if not c:
            print("Uso: ottercode run <comando>")
            return 2
        print(sandbox_run(state, c))
        return 0
    if cmd == "test":
        print(sandbox_run(state, "python -m pytest -q"))
        return 0
    if cmd == "edit":
        path = args.archivo
        seed = f"Lee {path} y mejóralo con edit_file (no write_file si ya existe)."
        if args.plain or not args.tui:
            return _repl(state, seed)
        from ottercode_cli.tui import run_tui
        return run_tui(state, seed)
    if cmd == "explain":
        return _repl(state, "Explica el último error de este workspace o de git/pytest.")
    if cmd == "review":
        return _repl(state, "Revisa el workspace: tree + read_file de lo importante y un veredicto breve.")
    if cmd == "chat":
        if args.tui and not args.plain:
            from ottercode_cli.tui import run_tui
            return run_tui(state)
        return _repl(state)
    print(SLASH_HELP)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
