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
    from textual.widgets import DirectoryTree, Footer, Header, Input, Label, ListItem, ListView, Static
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


def _bar(frac: float, width: int = 22) -> str:
    frac = max(0.0, min(1.0, frac))
    n = int(round(frac * width))
    return "#" * n + "-" * (width - n)


def _status_markup(c: dict) -> str:
    ctx_f = (c["ctx_used"] / c["ctx_tot"]) if c["ctx_tot"] else 0
    vram_f = (c["vram_used"] / c["vram_tot"]) if c["vram_tot"] else 0
    mcp = "conectado" if c["mcp"] else "off"
    return (
        "[b]ESTADO[/b]\n"
        f"Modelo     [cyan]{c['model']}[/cyan]\n"
        f"Contexto   {c['ctx_used']} / {c['ctx_tot']} ({ctx_f*100:.1f}%)\n"
        f"[cyan]{_bar(ctx_f)}[/cyan]\n"
        f"VRAM       {c['vram_used']:.1f} GB / {c['vram_tot']:.1f} GB\n"
        f"[green]{_bar(vram_f)}[/green]\n"
        f"Sandbox    [green]{c['sandbox']}[/green]\n"
        f"MCP        [green]{mcp}[/green]\n"
        f"Memoria    {c['memory']}\n"
        f"Workdir    {c['workdir']}\n"
        f"Permisos   [green]{c['perms']}[/green]\n"
        f"Sesión     {c['session']}"
    )


if HAS_TEXTUAL:

    class ModelPicker(ModalScreen[str]):
        BINDINGS = [Binding("escape", "cancel", "Cerrar")]

        def __init__(self, models: List[str], current: str) -> None:
            super().__init__()
            self.models = models or [current]
            self.current = current

        def compose(self) -> ComposeResult:
            yield Label("Modelo (Enter) · Esc cierra")
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
        CSS = """
        Screen { background: #0b1220; color: #c9d4e3; }
        Header { background: #0e1624; color: #86efac; }
        Footer { background: #0a1018; color: #64748b; }
        #col-files { width: 28; }
        #col-agent { width: 1fr; }
        #col-side { width: 38; }
        .panel-title { color: #64748b; text-style: bold; padding: 0 1; height: 1; }
        #files { height: 1fr; border: solid #1e293b; padding: 0 1; color: #94a3b8; }
        DirectoryTree { height: 1fr; border: solid #1e293b; background: #0b1220; color: #94a3b8; }
        #chat { height: 1fr; border: solid #1e293b; padding: 1 1; color: #e2e8f0; }
        #ready { dock: right; width: 12; color: #86efac; text-align: right; padding: 0 1; }
        #composer { dock: bottom; height: auto; }
        #prompt-wrap { height: auto; border: solid #1e293b; }
        Input { background: #0b1220; border: none; color: #e8edf5; }
        #hint { color: #475569; height: 1; padding: 0 1; }
        #status { height: auto; border: solid #1e293b; padding: 1 1; }
        #diff { height: 1fr; border: solid #1e293b; padding: 1 1; color: #86efac; }
        """
        BINDINGS = [
            Binding("ctrl+c", "interrupt", "abortar"),
            Binding("ctrl+q", "quit", "Salir"),
            Binding("escape", "interrupt", "abortar", show=False),
            Binding("f1", "help", "help"),
            Binding("f2", "pick_model", "modelo"),
            Binding("f3", "refresh_files", "archivos"),
            Binding("f4", "show_diff", "diff"),
            Binding("ctrl+s", "focus_prompt", "prompt"),
            Binding("ctrl+y", "yolo", "YOLO"),
            Binding("tab", "slash_complete", "slash", show=False),
        ]

        def __init__(self, state, seed: str = "") -> None:
            super().__init__()
            self.state = state
            self.seed = seed
            ts = datetime.now().strftime("%H:%M:%S")
            self._log: List[str] = [
                "OtterCode Neo TUI",
                f"[{ts}] Sistema: /help  /model  /edit  /run  /diff  /yolo",
            ]
            self.busy = False
            self._stream = ""
            self._last_paint = 0.0

        def compose(self) -> ComposeResult:
            from ottercode_cli.core_bridge import cockpit
            yield Header(show_clock=True)
            with Horizontal():
                with Vertical(id="col-files"):
                    yield Static("ARCHIVOS", classes="panel-title")
                    yield DirectoryTree(str(self.state.workdir), id="tree")
                with Vertical(id="col-agent"):
                    yield Static("AGENTE", classes="panel-title")
                    yield VerticalScroll(Static("\n".join(self._log), id="chat", markup=False))
                    with Vertical(id="composer"):
                        yield Static("/help  /model  /run  /diff  /yolo", id="hint")
                        with Horizontal(id="prompt-wrap"):
                            yield Input(placeholder="Escribe una instrucción o /help", id="in")
                with Vertical(id="col-side"):
                    yield Static(_status_markup(cockpit(self.state)), id="status", markup=True)
                    yield Static("TOOLS / SALIDA / DIFF", classes="panel-title")
                    yield VerticalScroll(Static(
                        "Panel de tools, diffs y salida.\nListo. Escribe /help.",
                        id="diff",
                        markup=False,
                    ))
            yield Footer()

        def on_mount(self) -> None:
            self._refresh_status()
            self.action_refresh_files()
            self.sub_title = "Ollama local"
            self.query_one("#in", Input).focus()
            self.set_interval(2.0, self._refresh_status)
            if self.seed:
                self.call_after_refresh(lambda: self._dispatch(self.seed))

        def _set_ready(self, text: str) -> None:
            try:
                self.query_one("#ready", Static).update(text)
            except Exception:
                pass

        def _refresh_status(self) -> None:
            from ottercode_cli.core_bridge import cockpit
            self.query_one("#status", Static).update(_status_markup(cockpit(self.state)))
            self.sub_title = f"Ollama local · {self.state.model}"
            self._set_ready("generando" if self.busy else "listo")

        def action_refresh_files(self) -> None:
            try:
                self.query_one("#tree", DirectoryTree).reload()
            except Exception:
                pass

        def on_directory_tree_file_selected(self, event) -> None:
            path = getattr(event, "path", None) or getattr(event, "node", None)
            p = Path(str(path))
            try:
                rel = p.relative_to(self.state.workdir)
            except Exception:
                rel = p
            try:
                from ottercode_cli.core_bridge import read_file
                body = read_file(self.state, str(rel))[:4000]
            except Exception as exc:
                body = str(exc)
            self.query_one("#diff", Static).update(f"{rel}\n{body}")

        def action_interrupt(self) -> None:
            from ottercode_cli.core_bridge import abort_run
            if self.busy and abort_run(self.state):
                self._append("(abortado)")
                self._set_ready("abortando")
                return
            if not self.busy:
                self.exit()

        def action_slash_complete(self) -> None:
            inp = self.query_one("#in", Input)
            v = inp.value
            if not v.startswith("/"):
                return
            hits = [h for h in SLASH_HINTS if h.startswith(v)]
            if hits:
                inp.value = hits[0]
                inp.cursor_position = len(inp.value)

        def action_help(self) -> None:
            self._append("\n" + SLASH_HELP)

        def action_show_diff(self) -> None:
            d = self.state.pending_diff[:8000] or "Panel de tools, diffs y salida.\nListo. Escribe /help."
            self.query_one("#diff", Static).update(d)
            self.query_one("#in", Input).focus()

        def action_focus_prompt(self) -> None:
            self.query_one("#in", Input).focus()

        def action_yolo(self) -> None:
            from ottercode_cli.commands import handle_slash
            handle_slash(self.state, parse_slash("/yolo"), lambda s: None)  # type: ignore[arg-type]
            self._append("YOLO on")
            self._refresh_status()

        def action_pick_model(self) -> None:
            from ottercode_cli.core_bridge import list_models
            models = list_models() or [self.state.model]

            def done(name: str | None) -> None:
                if name:
                    self.state.model = name
                    self._append(f"Modelo -> {name}")
                    self._refresh_status()

            self.push_screen(ModelPicker(models, self.state.model), done)

        def on_input_changed(self, event: Input.Changed) -> None:
            v = event.value
            if v.startswith("/") and len(v) < 24:
                hits = [h for h in SLASH_HINTS if h.startswith(v)][:8]
                self.query_one("#hint", Static).update("  ".join(hits) or "/help")
            else:
                self.query_one("#hint", Static).update("F1 help  F2 modelo  F3 archivos  F4 diff  Ctrl+Y YOLO")

        def on_input_submitted(self, event: Input.Submitted) -> None:
            line = event.value.strip()
            event.input.value = ""
            if not line or self.busy:
                return
            self._dispatch(line)

        def _scroll_chat(self) -> None:
            try:
                self.query_one("#chat").parent.scroll_end(animate=False)
            except Exception:
                pass

        def _append(self, text: str) -> None:
            self._log.append(text)
            self._log = self._log[-80:]
            self.query_one("#chat", Static).update("\n".join(self._log)[-14000:])
            self._scroll_chat()

        def _paint_stream(self) -> None:
            self.query_one("#chat", Static).update(
                "\n".join(self._log) + "\n" + self._stream[-6000:]
            )
            self._scroll_chat()

        def _dispatch(self, line: str) -> None:
            from ottercode_cli.commands import handle_slash
            sl = parse_slash(line)
            if sl:
                if sl.name == "model" and not sl.arg:
                    self.action_pick_model()
                    return
                buf: list[str] = []
                handle_slash(self.state, sl, buf.append)
                self._append("\n".join(buf) or f"/{sl.name}")
                if sl.name in ("diff", "apply", "reject"):
                    self.action_show_diff()
                if sl.name in ("files", "open"):
                    self.action_refresh_files()
                if sl.name == "clear":
                    self._log = ["OtterCode Neo TUI"]
                    self.query_one("#chat", Static).update(self._log[0])
                self._refresh_status()
                return
            self.busy = True
            self._set_ready("generando")
            ts = datetime.now().strftime("%H:%M:%S")
            self._append(f"\n[{ts}] > {line}\n")
            self._stream = ""
            self._run_agent(line)

        def _on_ev(self, name: str, data: dict) -> None:
            if name == "token":
                self._stream += str(data.get("token") or "")
                now = time.monotonic()
                if now - self._last_paint >= 0.05:
                    self._last_paint = now
                    self._paint_stream()
            elif name == "tool_call":
                self.query_one("#diff", Static).update(f"tool {data.get('tool')}")
                self._append(f"  {data.get('tool')}")
            elif name == "tool_result":
                ok = "ok" if data.get("ok") else "err"
                out = str(data.get("output") or "")[:800]
                self.query_one("#diff", Static).update(f"[{ok}]\n{out}")
                self._append(f"[{ok}] {out[:200]}")
            elif name == "diff":
                self.state.pending_diff = str(data.get("diff") or "")
                self.action_show_diff()
            elif name == "file_updated":
                self.action_refresh_files()

        def _agent_done(self, acc: str, err: str) -> None:
            self._paint_stream()
            if err:
                self._append(f"error: {err}")
            elif acc and acc not in "\n".join(self._log)[-8000:]:
                self._append(acc[-8000:])
            self.busy = False
            self._set_ready("listo")
            self._refresh_status()
            self.action_refresh_files()
            self.query_one("#in", Input).focus()

        @work(thread=True, exclusive=True)
        def _run_agent(self, line: str) -> None:
            from ottercode_cli.core_bridge import run_prompt
            acc: list[str] = []
            err = ""

            def on_ev(name: str, data: dict) -> None:
                if name == "token":
                    acc.append(str(data.get("token") or ""))
                try:
                    self.call_from_thread(self._on_ev, name, data)
                except Exception:
                    pass

            try:
                run_prompt(self.state, line, on_ev)
            except Exception as exc:
                err = str(exc)
            try:
                self.call_from_thread(self._agent_done, "".join(acc), err)
            except Exception:
                pass


def run_tui(state, seed: str = "") -> int:
    """Arranca Textual, o curses si no hay Textual. Nunca se queda en un print y sale."""
    if HAS_TEXTUAL:
        OtterTui(state, seed=seed).run()
        return 0
    from ottercode_cli.curses_tui import run_curses_tui
    return run_curses_tui(state, seed)
