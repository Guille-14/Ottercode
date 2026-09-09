"""TUI OpenCode-like. Textual si hay; si no, curses (stdlib)."""
from __future__ import annotations

import time
from datetime import datetime
from pathlib import Path
from typing import List

from ottercode_cli.slash import SLASH_HELP, parse_slash

try:
    from textual import work
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.screen import ModalScreen
    from textual.widgets import DirectoryTree, Footer, Header, Input, Label, ListItem, ListView, Log, Static
    HAS_TEXTUAL = True
except ImportError:
    HAS_TEXTUAL = False
    App = object  # type: ignore[misc,assignment]
    work = None  # type: ignore[assignment]


SLASH_HINTS = [
    "/help", "/status", "/model", "/files", "/open ", "/edit ", "/diff",
    "/apply", "/reject", "/run ", "/test", "/explain", "/learn ", "/yolo",
    "/rag ", "/clear", "/history", "/resume ", "/permissions", "/mcp", "/tools",
]

_SKIP = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache",
    ".pytest_cache", "dist", "build", ".next", ".turbo", "coverage",
}


def _bar(frac: float, width: int = 18) -> str:
    frac = max(0.0, min(1.0, frac))
    n = int(round(frac * width))
    return "#" * n + "-" * (width - n)


def _status_markup(c: dict) -> str:
    ctx_f = (c["ctx_used"] / c["ctx_tot"]) if c["ctx_tot"] else 0
    vram_f = (c["vram_used"] / c["vram_tot"]) if c["vram_tot"] else 0
    mcp = "ok" if c["mcp"] else "off"
    return (
        f"Modelo   [cyan]{c['model']}[/cyan]\n"
        f"Ctx      {c['ctx_used']} / {c['ctx_tot']}\n"
        f"[cyan]{_bar(ctx_f)}[/cyan]\n"
        f"VRAM     {c['vram_used']:.1f} / {c['vram_tot']:.1f} GB\n"
        f"[green]{_bar(vram_f)}[/green]\n"
        f"Sandbox  {c['sandbox']}\n"
        f"MCP      {mcp}\n"
        f"Permisos {c['perms']}\n"
        f"Dir      {c['workdir']}\n"
        f"Sesión   {c['session']}"
    )


if HAS_TEXTUAL:

    class WorkspaceTree(DirectoryTree):
        def filter_paths(self, paths):
            out = []
            for p in paths:
                name = p.name if hasattr(p, "name") else Path(p).name
                if name in _SKIP or name.startswith("."):
                    continue
                out.append(p)
            return out

    class ModelPicker(ModalScreen[str]):
        BINDINGS = [Binding("escape", "cancel", "Cerrar")]

        def __init__(self, models: List[str], current: str) -> None:
            super().__init__()
            self.models = models or [current]
            self.current = current

        def compose(self) -> ComposeResult:
            yield Label("Modelo · Enter elige · Esc cierra")
            items = [ListItem(Label(("* " if m == self.current else "  ") + m)) for m in self.models]
            yield ListView(*items, id="models")

        def on_list_view_selected(self, event: ListView.Selected) -> None:
            idx = event.list_view.index
            if idx is not None and 0 <= idx < len(self.models):
                self.dismiss(self.models[idx])

        def action_cancel(self) -> None:
            self.dismiss(self.current)

    class OtterTui(App):
        TITLE = "OtterCode Neo TUI"
        AUTO_FOCUS = "#in"
        CSS = """
        Screen { background: #0b1220; color: #c9d4e3; }
        Header { background: #0e1624; color: #86efac; }
        Footer { background: #0a1018; color: #64748b; }
        #col-files { width: 26; }
        #col-agent { width: 1fr; }
        #col-side { width: 36; }
        .panel-title { color: #64748b; text-style: bold; padding: 0 1; height: 1; }
        DirectoryTree { height: 1fr; border: solid #1e293b; background: #0b1220; color: #94a3b8; }
        #chat { height: 1fr; border: solid #1e293b; background: #0b1220; color: #e2e8f0; }
        #composer { height: auto; }
        Input { background: #111827; border: solid #334155; color: #e8edf5; }
        Input:focus { border: solid #22d3ee; }
        #hint { color: #475569; height: 1; padding: 0 1; }
        #status { height: auto; max-height: 16; border: solid #1e293b; padding: 1 1; }
        #diff { height: 1fr; border: solid #1e293b; padding: 1 1; color: #86efac; }
        """
        BINDINGS = [
            Binding("ctrl+q", "quit", "salir"),
            Binding("ctrl+c", "interrupt", "abortar"),
            Binding("escape", "interrupt", "abortar", show=False),
            Binding("f1", "help", "help"),
            Binding("f2", "pick_model", "modelo"),
            Binding("f3", "focus_tree", "archivos"),
            Binding("f4", "show_diff", "diff"),
            Binding("ctrl+s", "focus_prompt", "prompt"),
            Binding("ctrl+y", "yolo", "YOLO"),
            Binding("tab", "slash_complete", show=False, priority=True),
        ]

        def __init__(self, state, seed: str = "") -> None:
            super().__init__()
            self.state = state
            self.seed = seed
            self.busy = False
            self._stream_open = False

        def compose(self) -> ComposeResult:
            from ottercode_cli.core_bridge import cockpit
            yield Header(show_clock=True)
            with Horizontal():
                with Vertical(id="col-files"):
                    yield Static("ARCHIVOS", classes="panel-title")
                    yield WorkspaceTree(str(self.state.workdir), id="tree")
                with Vertical(id="col-agent"):
                    yield Static("AGENTE", classes="panel-title")
                    yield Log(id="chat", highlight=False, max_lines=400)
                    with Vertical(id="composer"):
                        yield Static("F1 help  F2 modelo  Tab /cmd  Esc aborta  Ctrl+Q sale", id="hint")
                        yield Input(placeholder="Escribe una instrucción o /help", id="in")
                with Vertical(id="col-side"):
                    yield Static("ESTADO", classes="panel-title")
                    yield Static(_status_markup(cockpit(self.state)), id="status", markup=True)
                    yield Static("TOOLS / SALIDA / DIFF", classes="panel-title")
                    yield VerticalScroll(Static("Listo.", id="diff", markup=False))
            yield Footer()

        def on_mount(self) -> None:
            chat = self.query_one("#chat", Log)
            chat.write_line("OtterCode Neo TUI")
            chat.write_line("Escribe en el prompt de abajo. /help · F2 cambia modelo.")
            self.sub_title = "Ollama local"
            self._refresh_status()
            self.query_one("#in", Input).focus()
            self.set_interval(5.0, self._refresh_status)
            if self.seed:
                self.call_after_refresh(lambda: self._dispatch(self.seed))

        def _chat(self) -> Log:
            return self.query_one("#chat", Log)

        def _refresh_status(self) -> None:
            from ottercode_cli.core_bridge import cockpit
            try:
                self.query_one("#status", Static).update(_status_markup(cockpit(self.state)))
            except Exception:
                return
            flag = "generando" if self.busy else "listo"
            self.sub_title = f"Ollama local · {self.state.model} · {flag}"

        def action_focus_tree(self) -> None:
            self.query_one("#tree", WorkspaceTree).focus()

        def action_focus_prompt(self) -> None:
            self.query_one("#in", Input).focus()

        def on_directory_tree_file_selected(self, event: DirectoryTree.FileSelected) -> None:
            p = Path(str(event.path))
            try:
                rel = p.relative_to(self.state.workdir)
            except Exception:
                rel = p
            try:
                from ottercode_cli.core_bridge import read_file
                body = read_file(self.state, str(rel))[:3500]
            except Exception as exc:
                body = str(exc)
            self.query_one("#diff", Static).update(f"{rel}\n{body}")
            self.action_focus_prompt()

        def action_interrupt(self) -> None:
            from ottercode_cli.core_bridge import abort_run
            if self.busy:
                abort_run(self.state)
                self._chat().write_line("(abortando…)")
                return
            inp = self.query_one("#in", Input)
            if inp.value:
                inp.value = ""
                return

        def action_slash_complete(self) -> None:
            inp = self.query_one("#in", Input)
            v = inp.value
            if v.startswith("/"):
                hits = [h for h in SLASH_HINTS if h.startswith(v)]
                if hits:
                    inp.value = hits[0]
                    inp.cursor_position = len(inp.value)
            inp.focus()

        def action_help(self) -> None:
            self._chat().write_line(SLASH_HELP)
            self.action_focus_prompt()

        def action_show_diff(self) -> None:
            d = self.state.pending_diff[:8000] or "(sin diff)"
            self.query_one("#diff", Static).update(d)
            self.action_focus_prompt()

        def action_yolo(self) -> None:
            from ottercode_cli.commands import handle_slash
            handle_slash(self.state, parse_slash("/yolo"), lambda s: None)  # type: ignore[arg-type]
            self._chat().write_line("YOLO on")
            self._refresh_status()

        def action_pick_model(self) -> None:
            from ottercode_cli.core_bridge import list_models
            models = list_models() or [self.state.model]

            def done(name: str | None) -> None:
                if name:
                    self.state.model = name
                    self._chat().write_line(f"Modelo → {name}")
                    self._refresh_status()
                self.action_focus_prompt()

            self.push_screen(ModelPicker(models, self.state.model), done)

        def on_input_changed(self, event: Input.Changed) -> None:
            v = event.value
            if v.startswith("/") and len(v) < 28:
                hits = [h for h in SLASH_HINTS if h.startswith(v)][:6]
                self.query_one("#hint", Static).update("  ".join(hits) or "/help")
            else:
                self.query_one("#hint", Static).update(
                    "F1 help  F2 modelo  Tab /cmd  Esc aborta  Ctrl+Q sale"
                )

        def on_input_submitted(self, event: Input.Submitted) -> None:
            line = event.value.strip()
            event.input.value = ""
            if not line:
                return
            if self.busy:
                self._chat().write_line("(espera o Esc para abortar)")
                return
            self._dispatch(line)

        def _push_text(self, chunk: str) -> None:
            if chunk:
                self._chat().write(chunk)

        def _dispatch(self, line: str) -> None:
            from ottercode_cli.commands import handle_slash
            sl = parse_slash(line)
            if sl and sl.name in ("explain", "quiz"):
                sl = None
            if sl:
                if sl.name == "model" and not sl.arg:
                    self.action_pick_model()
                    return
                if sl.name == "clear":
                    self._chat().clear()
                    self._chat().write_line("OtterCode Neo TUI")
                    return
                buf: list[str] = []
                handle_slash(self.state, sl, buf.append)
                self._chat().write_line("\n".join(buf) or f"/{sl.name}")
                if sl.name in ("diff", "apply", "reject"):
                    self.action_show_diff()
                self._refresh_status()
                self.action_focus_prompt()
                return
            self.busy = True
            ts = datetime.now().strftime("%H:%M:%S")
            self._chat().write_line(f"[{ts}] tú: {line}")
            self._stream_open = True
            self._refresh_status()
            self._run_agent(line)

        def _on_ev(self, name: str, data: dict) -> None:
            if name == "tool_call":
                self.query_one("#diff", Static).update(f"tool {data.get('tool')}")
            elif name == "tool_result":
                ok = "ok" if data.get("ok") else "err"
                out = str(data.get("output") or "")[:800]
                self.query_one("#diff", Static).update(f"[{ok}]\n{out}")
            elif name == "diff":
                self.state.pending_diff = str(data.get("diff") or "")
                self.query_one("#diff", Static).update(self.state.pending_diff[:8000] or "(sin diff)")
            elif name == "file_updated":
                pass

        def _agent_done(self, err: str) -> None:
            if self._stream_open:
                self._chat().write_line("")
            self._stream_open = False
            if err:
                self._chat().write_line(f"error: {err}")
            self.busy = False
            self._refresh_status()
            self.action_focus_prompt()

        @work(thread=True, exclusive=True)
        def _run_agent(self, line: str) -> None:
            from ottercode_cli.core_bridge import run_prompt
            err = ""
            buf: list[str] = []
            last = 0.0

            def flush() -> None:
                if not buf:
                    return
                chunk = "".join(buf)
                buf.clear()
                try:
                    self.call_from_thread(self._push_text, chunk)
                except Exception:
                    pass

            def on_ev(name: str, data: dict) -> None:
                nonlocal last
                if name == "token":
                    tok = str(data.get("token") or "")
                    if not tok:
                        return
                    buf.append(tok)
                    now = time.monotonic()
                    if now - last >= 0.06:
                        last = now
                        flush()
                    return
                flush()
                try:
                    self.call_from_thread(self._on_ev, name, data)
                except Exception:
                    pass

            try:
                run_prompt(self.state, line, on_ev)
            except Exception as exc:
                err = str(exc)
            flush()
            try:
                self.call_from_thread(self._agent_done, err)
            except Exception:
                pass


def run_tui(state, seed: str = "") -> int:
    if HAS_TEXTUAL:
        OtterTui(state, seed=seed).run()
        return 0
    from ottercode_cli.curses_tui import run_curses_tui
    return run_curses_tui(state, seed)
