"""Punto de entrada: ottercode / python -m ottercode_cli."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def _parse(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="ottercode", description="Agente de código local (Ollama) en terminal")
    p.add_argument("--tui", action="store_true", help="forzar TUI")
    p.add_argument("--plain", action="store_true", help="REPL sin TUI")
    p.add_argument("--workdir", default="", help="workspace (default: cwd)")
    opt, rest = p.parse_known_args(argv)
    cmd = rest[0] if rest else "chat"
    pos = rest[1:] if rest else []
    opt.cmd = cmd
    opt.pos = pos
    return opt


def _launch(state, args: argparse.Namespace, seed: str = "") -> int:
    if args.plain:
        return _repl(state, seed)
    from ottercode_cli.tui import run_tui
    try:
        return run_tui(state, seed)
    except Exception as exc:
        print(f"TUI falló ({exc}). REPL:", file=sys.stderr)
        return _repl(state, seed)


def _repl(state, first: str = "") -> int:
    from ottercode_cli.commands import handle_slash
    from ottercode_cli.core_bridge import run_prompt, status_blob
    from ottercode_cli.slash import parse_slash

    print("OtterCode CLI · /help · Ctrl-D sale")
    try:
        print(status_blob(state).split("\n")[0])
    except Exception as exc:
        print(f"(status: {exc})")
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
                print(f"\n  {data.get('tool')}")
            elif name == "tool_result":
                ok = "ok" if data.get("ok") else "err"
                print(f"\n[{ok}] {(str(data.get('output') or ''))[:400]}")

        print()
        try:
            run_prompt(state, line, on_ev)
        except Exception as exc:
            print(f"error: {exc}")
        print()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _parse(argv)
    from ottercode_cli.core_bridge import new_state, sandbox_run
    from ottercode_cli.sessions import list_sessions, load_session
    from ottercode_cli.slash import SLASH_HELP

    wd = Path(args.workdir).resolve() if args.workdir else None
    state = new_state(workdir=wd)

    cmd = args.cmd or "chat"
    pos = list(args.pos or [])

    if cmd == "sessions":
        for r in list_sessions():
            print(f"{r['id']}\t{r.get('date')}\t{r.get('task')}")
        return 0
    if cmd == "resume":
        sid = pos[0] if pos else ""
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
        state.learn_topic = " ".join(pos)
        os.environ["OTTERCODE_LEARNING_MODE"] = "1"
        seed = f"Quiero aprender {state.learn_topic or 'programación'}. Empieza con un plan corto y el primer ejercicio."
        return _launch(state, args, seed)
    if cmd == "run":
        c = " ".join(pos)
        if not c:
            print("Uso: ottercode run <comando>")
            return 2
        print(sandbox_run(state, c))
        return 0
    if cmd == "test":
        print(sandbox_run(state, "python -m pytest -q"))
        return 0
    if cmd == "edit":
        if not pos:
            print("Uso: ottercode edit <archivo>")
            return 2
        seed = f"Lee {pos[0]} y mejóralo con edit_file (no write_file si ya existe)."
        return _launch(state, args, seed)
    if cmd == "explain":
        return _launch(state, args, "Explica el último error de este workspace o de git/pytest.")
    if cmd == "review":
        return _launch(state, args, "Revisa el workspace: tree + read_file de lo importante y un veredicto breve.")
    if cmd == "chat":
        return _launch(state, args)
    print(SLASH_HELP)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
