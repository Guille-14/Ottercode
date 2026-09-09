"""TUI estilo OpenCode: chat streaming, modelo, diffs, archivos, slash."""
from __future__ import annotations

from typing import List

from ottercode_cli.commands import handle_slash
from ottercode_cli.core_bridge import CliState, list_models, run_prompt, status_blob, tree
from ottercode_cli.slash import SLASH_HELP, parse_slash

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.screen import ModalScreen
    from textual.widgets import Button, Footer, Header, Input, Label, ListItem, ListView, Static
    HAS_TEXTUAL = True
except ImportError:
    HAS_TEXTUAL = False
    App = object  # type: ignore[misc,assignment]


SLASH_HINTS = [
    "/help", "/status", "/model", "/files", "/open ", "/edit ", "/diff",
    "/apply", "/reject", "/run ", "/test", "/explain", "/learn ", "/yolo",
    "/rag ", "/clear", "/history", "/resume ", "/permissions", "/mcp", "/tools",
]


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
        TITLE = "OtterCode"
        CSS = """
        Screen { background: #0c0f14; color: #e8edf5; }
        Header { background: #111827; color: #22d3ee; }
        Footer { background: #111827; }
        #chat {
            height: 1fr;
            border: tall #1f3a4d;
            padding: 1 1;
            background: #0b1220;
        }
        #diff {
            height: 8;
            border: tall #3f2d1a;
            color: #86efac;
            background: #0b1220;
        }
        #side { width: 42; }
        #status {
            height: 9;
            border: tall #1e3a5f;
            color: #93c5fd;
            background: #0b1220;
        }
        #files {
            height: 1fr;
            border: tall #1f2937;
            color: #cbd5e1;
        }
        #composer { dock: bottom; height: auto; }
        Input {
            background: #111827;
            border: tall #22d3ee;
            color: #e8edf5;
        }
        #hint { color: #64748b; height: 1; }
        """
        BINDINGS = [
            Binding("ctrl+c", "quit", "Salir"),
            Binding("ctrl+q", "quit", "Salir", show=False),
            Binding("f1", "help", "Ayuda"),
            Binding("f2", "pick_model", "Modelo"),
            Binding("f3", "refresh_files", "Archivos"),
            Binding("f4", "show_diff", "Diff"),
            Binding("ctrl+y", "yolo", "YOLO"),
        ]

        def __init__(self, state: CliState, seed: str = "") -> None:
            super().__init__()
            self.state = state
            self.seed = seed
            self._log: List[str] = ["OtterCode · F2 modelo · F1 ayuda · / para slash"]
            self.busy = False

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Horizontal():
                with Vertical():
                    yield VerticalScroll(Static("\n".join(self._log), id="chat"))
                    yield Static("(sin diff)", id="diff")
                    with Vertical(id="composer"):
                        yield Static("/help  /model  /run  /diff  /yolo", id="hint")
                        yield Input(placeholder="Pregunta, /comando o Tab para slash…", id="in")
                with Vertical(id="side"):
                    yield Static("estado", id="status")
                    yield Static("archivos", id="files")
            yield Footer()

        def on_mount(self) -> None:
            self._refresh_status()
            self.action_refresh_files()
            self.sub_title = self.state.model
            if self.seed:
                self.call_after_refresh(lambda: self._send(self.seed))

        def _refresh_status(self) -> None:
            blob = status_blob(self.state)
            extra = f"yolo={self.state.yolo}  learn={self.state.learn}"
            self.query_one("#status", Static).update(blob + extra)
            self.sub_title = self.state.model

        def action_refresh_files(self) -> None:
            try:
                self.query_one("#files", Static).update(tree(self.state)[:3500])
            except Exception as exc:
                self.query_one("#files", Static).update(str(exc))

        def action_help(self) -> None:
            self._append("\n" + SLASH_HELP)

        def action_show_diff(self) -> None:
            self.query_one("#diff", Static).update(self.state.pending_diff[:6000] or "(sin diff)")

        def action_yolo(self) -> None:
            handle_slash(self.state, parse_slash("/yolo") or parse_slash("/help"), lambda s: None)  # type: ignore[arg-type]
            self._append("YOLO on")
            self._refresh_status()

        def action_pick_model(self) -> None:
            models = list_models() or [self.state.model]

            def done(name: str | None) -> None:
                if name:
                    self.state.model = name
                    self._append(f"Modelo → {name}")
                    self._refresh_status()

            self.push_screen(ModelPicker(models, self.state.model), done)

        def on_input_changed(self, event: Input.Changed) -> None:
            v = event.value
            if v.startswith("/") and len(v) < 24:
                hits = [h for h in SLASH_HINTS if h.startswith(v)][:6]
                self.query_one("#hint", Static).update("  ".join(hits) or "/help")
            else:
                self.query_one("#hint", Static).update("/help  F2 modelo  F4 diff")

        def on_input_submitted(self, event: Input.Submitted) -> None:
            line = event.value.strip()
            event.input.value = ""
            if not line or self.busy:
                return
            self._send(line)

        def _append(self, text: str) -> None:
            self._log.append(text)
            self._log = self._log[-80:]
            self.query_one("#chat", Static).update("\n".join(self._log)[-12000:])

        def _send(self, line: str) -> None:
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
                self._refresh_status()
                return
            self.busy = True
            self._append(f"\n▸ {line}\n")
            acc: list[str] = []

            def on_ev(name: str, data: dict) -> None:
                if name == "token":
                    acc.append(str(data.get("token") or ""))
                    self.query_one("#chat", Static).update(
                        "\n".join(self._log) + "\n" + "".join(acc)[-6000:]
                    )
                elif name == "tool_call":
                    self._append(f"⚙ {data.get('tool')}")
                elif name == "tool_result":
                    ok = "ok" if data.get("ok") else "err"
                    self._append(f"[{ok}] {str(data.get('output') or '')[:300]}")
                elif name == "diff":
                    self.state.pending_diff = str(data.get("diff") or "")
                    self.action_show_diff()
                elif name == "file_updated":
                    self.action_refresh_files()

            try:
                run_prompt(self.state, line, on_ev)
            except Exception as exc:
                self._append(f"error: {exc}")
            if acc:
                self._append("".join(acc)[-8000:])
            self.busy = False
            self._refresh_status()
            self.action_refresh_files()


def run_tui(state: CliState, seed: str = "") -> int:
    if not HAS_TEXTUAL:
        print("TUI: pip install textual   (o ottercode chat --plain)")
        return 2
    OtterTui(state, seed=seed).run()
    return 0
