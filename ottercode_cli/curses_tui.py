"""TUI stdlib (curses): 3 columnas. Funciona sin Textual."""
from __future__ import annotations

import curses
import os
import sys
from datetime import datetime
from typing import List

from ottercode_cli.slash import SLASH_HELP, parse_slash


def _clip(s: str, n: int) -> str:
    s = s.replace("\t", " ")
    return s[:n] if len(s) <= n else s[: n - 1] + "…"


def _fill(win, lines: List[str], h: int, w: int, attr: int = 0) -> None:
    win.erase()
    for i, line in enumerate(lines[-(h - 1) :]):
        if i >= h - 1:
            break
        try:
            win.addnstr(i, 0, _clip(line, w - 1), w - 1, attr)
        except curses.error:
            pass
    win.noutrefresh()


def run_curses_tui(state, seed: str = "") -> int:
    if not sys.stdout.isatty() or not sys.stdin.isatty():
        print("No hay TTY. Usa un terminal real, no una tubería.")
        return 2
    os.environ.setdefault("ESCDELAY", "25")
    return curses.wrapper(lambda stdscr: _loop(stdscr, state, seed))


def _loop(stdscr, state, seed: str) -> int:
    from ottercode_cli.commands import handle_slash
    from ottercode_cli.core_bridge import cockpit, list_models, run_prompt, tree

    curses.curs_set(1)
    curses.use_default_colors()
    if curses.has_colors():
        curses.init_pair(1, curses.COLOR_CYAN, -1)
        curses.init_pair(2, curses.COLOR_GREEN, -1)
        curses.init_pair(3, curses.COLOR_YELLOW, -1)
        curses.init_pair(4, curses.COLOR_WHITE, -1)
    cyan = curses.color_pair(1)
    green = curses.color_pair(2)
    yellow = curses.color_pair(3)

    log: List[str] = [
        "OtterCode Neo TUI",
        f"[{datetime.now().strftime('%H:%M:%S')}] /help  F2 modelo  F4 diff  Ctrl+Y YOLO",
    ]
    diff_lines = ["TOOLS / SALIDA / DIFF", "Listo. Escribe /help."]
    buf = ""
    models: List[str] = []
    pick = -1  # -1 = no picker
    busy = False

    def files_text() -> List[str]:
        try:
            return ["workspace"] + (tree(state) or "").splitlines()[:200]
        except Exception as exc:
            return ["workspace", str(exc)]

    def status_lines() -> List[str]:
        try:
            c = cockpit(state)
        except Exception as exc:
            return ["ESTADO", str(exc)]
        ctx_f = (c["ctx_used"] / c["ctx_tot"]) if c["ctx_tot"] else 0
        vf = (c["vram_used"] / c["vram_tot"]) if c["vram_tot"] else 0
        bar = lambda f: "#" * int(f * 16) + "-" * (16 - int(f * 16))
        return [
            "ESTADO",
            f"Modelo  {c['model']}",
            f"Ctx     {c['ctx_used']}/{c['ctx_tot']} ({ctx_f*100:.0f}%)",
            bar(ctx_f),
            f"VRAM    {c['vram_used']:.1f}/{c['vram_tot']:.1f} GB",
            bar(vf),
            f"Sandbox {c['sandbox']}",
            f"MCP     {'conectado' if c['mcp'] else 'off'}",
            f"Memoria {c['memory']}",
            f"Workdir {c['workdir']}",
            f"Permisos {c['perms']}",
            f"Sesión  {c['session']}",
        ]

    flines = files_text()

    def send(line: str) -> None:
        nonlocal busy, flines, diff_lines
        sl = parse_slash(line)
        if sl:
            if sl.name == "model" and not sl.arg:
                return open_picker()
            out: list[str] = []
            handle_slash(state, sl, out.append)
            log.extend((("\n".join(out) or f"/{sl.name}")).splitlines())
            if sl.name in ("diff", "apply", "reject"):
                diff_lines = (state.pending_diff or "(sin diff)").splitlines()[:80]
            if sl.name in ("files", "open"):
                flines = files_text()
            return
        busy = True
        log.append(f"[{datetime.now().strftime('%H:%M:%S')}] > {line}")
        acc: list[str] = []

        def on_ev(name: str, data: dict) -> None:
            nonlocal diff_lines
            if name == "token":
                acc.append(str(data.get("token") or ""))
            elif name == "tool_call":
                diff_lines = [f"tool {data.get('tool')}"]
                log.append(f"  {data.get('tool')}")
            elif name == "tool_result":
                ok = "ok" if data.get("ok") else "err"
                out = str(data.get("output") or "")[:600]
                diff_lines = f"[{ok}]\n{out}".splitlines()
                log.append(f"[{ok}] {out[:160]}")
            elif name == "diff":
                state.pending_diff = str(data.get("diff") or "")
                diff_lines = state.pending_diff.splitlines()[:80]

        try:
            run_prompt(state, line, on_ev)
        except Exception as exc:
            log.append(f"error: {exc}")
        if acc:
            log.extend("".join(acc).splitlines()[-40:])
        busy = False
        flines = files_text()

    def open_picker() -> None:
        nonlocal models, pick
        models = list_models() or [state.model]
        pick = 0
        for i, m in enumerate(models):
            if m == state.model:
                pick = i

    if seed:
        send(seed)

    while True:
        h, w = stdscr.getmaxyx()
        left = max(18, min(28, w // 5))
        right = max(24, min(36, w // 4))
        mid = max(20, w - left - right)
        stdscr.erase()
        title = f" OtterCode Neo TUI  ·  Ollama local  ·  {state.model} "
        try:
            stdscr.addnstr(0, 0, _clip(title, w), w, green | curses.A_BOLD)
        except curses.error:
            pass
        foot = " F1 help  F2 modelo  F3 archivos  F4 diff  Ctrl+Y YOLO  Ctrl+Q salir "
        try:
            stdscr.addnstr(h - 1, 0, _clip(foot, w), w, cyan)
        except curses.error:
            pass
        prompt_y = h - 2
        try:
            stdscr.addnstr(prompt_y, 0, _clip("> " + buf, w - 1), w - 1, yellow)
        except curses.error:
            pass
        body_h = h - 3
        lw = curses.newwin(body_h, left, 1, 0)
        mw = curses.newwin(body_h, mid, 1, left)
        rw = curses.newwin(body_h // 2, right, 1, left + mid)
        dw = curses.newwin(body_h - body_h // 2, right, 1 + body_h // 2, left + mid)
        _fill(lw, ["ARCHIVOS"] + flines, body_h, left, yellow)
        _fill(mw, ["AGENTE"] + log[-200:], body_h, mid)
        _fill(rw, status_lines(), body_h // 2, right, cyan)
        _fill(dw, diff_lines, body_h - body_h // 2, right, green)

        if pick >= 0:
            pw, ph = min(40, w - 4), min(len(models) + 2, h - 4)
            pan = curses.newwin(ph, pw, 2, (w - pw) // 2)
            pan.box()
            pan.addnstr(0, 2, " modelo ", pw - 4, green)
            for i, m in enumerate(models[: ph - 2]):
                a = curses.A_REVERSE if i == pick else 0
                pan.addnstr(i + 1, 1, _clip(("* " if m == state.model else "  ") + m, pw - 2), pw - 2, a)
            pan.noutrefresh()

        curses.doupdate()
        stdscr.move(prompt_y, min(len(buf) + 2, w - 2))
        stdscr.timeout(200)
        ch = stdscr.getch()
        if ch == -1:
            continue
        if pick >= 0:
            if ch in (27, ord("q")):
                pick = -1
            elif ch in (curses.KEY_UP, ord("k")):
                pick = max(0, pick - 1)
            elif ch in (curses.KEY_DOWN, ord("j")):
                pick = min(len(models) - 1, pick + 1)
            elif ch in (10, 13, curses.KEY_ENTER):
                if models:
                    state.model = models[pick]
                    log.append(f"Modelo → {state.model}")
                pick = -1
            continue
        if ch in (curses.KEY_F1,):
            log.extend(SLASH_HELP.splitlines())
        elif ch in (curses.KEY_F2,):
            open_picker()
        elif ch in (curses.KEY_F3,):
            flines = files_text()
        elif ch in (curses.KEY_F4,):
            diff_lines = (state.pending_diff or "(sin diff)").splitlines()[:80]
        elif ch == 25:  # Ctrl+Y
            handle_slash(state, parse_slash("/yolo"), lambda s: None)  # type: ignore
            log.append("YOLO on")
        elif ch in (17, 3):  # Ctrl+Q / Ctrl+C
            return 0
        elif ch in (curses.KEY_BACKSPACE, 127, 8):
            buf = buf[:-1]
        elif ch in (10, 13, curses.KEY_ENTER):
            line = buf.strip()
            buf = ""
            if line and not busy:
                send(line)
        elif 32 <= ch < 127:
            buf += chr(ch)
    return 0
