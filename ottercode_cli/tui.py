"""TUI Textual: chat, archivos, diffs, estado. Consume el core, no lo duplica."""
from __future__ import annotations

from typing import Optional

from ottercode_cli.commands import handle_slash
from ottercode_cli.core_bridge import CliState, run_prompt, tree
from ottercode_cli.slash import parse_slash

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical
    from textual.widgets import Footer, Header, Input, Static
    HAS_TEXTUAL = True
except ImportError:
    HAS_TEXTUAL = False
    App = object  # type: ignore[misc,assignment]


if HAS_TEXTUAL:

    class OtterTui(App):
        CSS = """
        Screen { background: #0b1220; color: #e8eefc; }
        #chat { height: 1fr; border: solid #334155; padding: 1; }
        #side { width: 36; border: solid #334155; }
        #diff { height: 10; border: solid #334155; }
        #status { height: 5; border: solid #1e3a5f; color: #93c5fd; }
        Input { dock: bottom; }
        """
        BINDINGS = [
            Binding("ctrl+c", "quit", "Salir"),
            Binding("f1", "help", "Ayuda"),
        ]

        def __init__(self, state: CliState, seed: str = "") -> None:
            super().__init__()
            self.state = state
            self.seed = seed

        def compose(self) -> ComposeResult:
            yield Header()
            with Horizontal():
                with Vertical():
                    yield Static("OtterCode · chat\n", id="chat")
                    yield Static("(diff)", id="diff")
                    yield Input(placeholder="mensaje o /help")
                with Vertical(id="side"):
                    yield Static("estado", id="status")
                    yield Static("archivos", id="files")
            yield Footer()

        def on_mount(self) -> None:
            self.query_one("#status", Static).update(f"{self.state.model}\n{self.state.workdir}")
            try:
                self.query_one("#files", Static).update(tree(self.state)[:2000])
            except Exception:
                pass
            if self.seed:
                self._send(self.seed)

        def action_help(self) -> None:
            from ottercode_cli.slash import SLASH_HELP
            self.query_one("#chat", Static).update(SLASH_HELP)

        def on_input_submitted(self, event: Input.Submitted) -> None:
            line = event.value.strip()
            event.input.value = ""
            if not line:
                return
            self._send(line)

        def _send(self, line: str) -> None:
            chat = self.query_one("#chat", Static)
            sl = parse_slash(line)
            if sl:
                buf: list[str] = []
                handle_slash(self.state, sl, buf.append)
                chat.update("\n".join(buf) or chat.renderable)  # type: ignore[arg-type]
                if sl.name == "diff":
                    self.query_one("#diff", Static).update(self.state.pending_diff[:4000] or "(sin diff)")
                return
            acc: list[str] = []

            def on_ev(name: str, data: dict) -> None:
                if name == "token":
                    acc.append(str(data.get("token") or ""))
                    chat.update("".join(acc)[-4000:])
                if name == "diff":
                    self.query_one("#diff", Static).update(str(data.get("diff") or "")[:4000])

            run_prompt(self.state, line, on_ev)
            if acc:
                chat.update("".join(acc)[-8000:])


def run_tui(state: CliState, seed: str = "") -> int:
    if not HAS_TEXTUAL:
        print("Instala textual: pip install textual rich")
        return 2
    OtterTui(state, seed=seed).run()
    return 0
