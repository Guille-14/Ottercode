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
    "/apply", "/reject", "/todo", "/run ", "/test", "/explain", "/learn ", "/yolo",
    "/rag ", "/clear", "/history", "/resume ", "/permissions", "/mcp", "/tools",
]

_SKIP = {
    ".git", "node_modules", ".venv", "venv", "__pycache__", ".mypy_cache",
    ".pytest_cache", "dist", "build", ".next", ".turbo", "coverage",
}


def _esc(s: object) -> str:
    return str(s).replace("\\", "\\\\").replace("[", "\\[")


def _safe_txt(s: object) -> str:
    t = str(s or "").encode("utf-8", "replace").decode("utf-8")
    return t.replace("\x00", "")[:8000]


def _bar(frac: float, width: int = 18) -> str:
    frac = max(0.0, min(1.0, frac))
    n = int(round(frac * width))
    return "#" * n + "-" * (width - n)


def _status_markup(c: dict) -> str:
    ctx_f = (c["ctx_used"] / c["ctx_tot"]) if c["ctx_tot"] else 0
    vram_f = (c["vram_used"] / c["vram_tot"]) if c["vram_tot"] else 0
    mcp = "ok" if c["mcp"] else "off"
    role = c.get("role") or "especialista"
    td, tt = int(c.get("todo_done") or 0), int(c.get("todo_total") or 0)
    return (
        f"Modelo   [{role}] {_esc(c['model'])}\n"
        f"Ctx      {c['ctx_used']} / {c['ctx_tot']}\n"
        f"{_bar(ctx_f)}\n"
        f"VRAM     {c['vram_used']:.1f} / {c['vram_tot']:.1f} GB\n"
        f"{_bar(vram_f)}\n"
        f"Todo     {td}/{tt} completados\n"
        f"Sandbox  {_esc(c['sandbox'])}\n"
        f"MCP      {mcp}\n"
        f"Permisos {_esc(c['perms'])}\n"
        f"Dir      {_esc(c['workdir'])}\n"
        f"Sesión   {_esc(c['session'])}"
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

    class PermModal(ModalScreen[str]):
        BINDINGS = [Binding("escape", "deny", "Denegar"), Binding("a", "ok", "Aprobar"),
                    Binding("d", "deny", "Denegar"), Binding("s", "always", "Siempre")]

        def __init__(self, title: str) -> None:
            super().__init__()
            self._title = title or "Permiso"

        def compose(self) -> ComposeResult:
            yield Label(self._title[:240])
            yield Label("Aprobar (a) / Denegar (d) / Siempre esta sesión (s)")
            yield ListView(
                ListItem(Label("Aprobar")),
                ListItem(Label("Denegar")),
                ListItem(Label("Siempre esta sesión")),
                id="perm-opts",
            )

        def on_list_view_selected(self, event: ListView.Selected) -> None:
            idx = event.list_view.index or 0
            self.dismiss(["a", "d", "s"][min(idx, 2)])

        def action_ok(self) -> None:
            self.dismiss("a")

        def action_deny(self) -> None:
            self.dismiss("d")

        def action_always(self) -> None:
            self.dismiss("s")

    class OtterTui(App):
        TITLE = "OtterCode Neo TUI"
        AUTO_FOCUS = "#in"
        ENABLE_COMMAND_PALETTE = False
        CSS = """
        Screen { background: #09090B; color: #FAFAFA; }
        Header { background: #18181B; color: #FAFAFA; }
        Footer { background: #09090B; color: #A1A1AA; }
        #col-files { width: 26; }
        #col-agent { width: 1fr; }
        #col-side { width: 36; }
        .panel-title { color: #A1A1AA; text-style: bold; padding: 0 1; height: 1; }
        DirectoryTree { height: 1fr; border: solid #27272A; background: #18181B; color: #A1A1AA; }
        #chat { height: 1fr; border: solid #27272A; background: #09090B; color: #FAFAFA; }
        #composer { height: auto; }
        Input { background: #18181B; border: solid #27272A; color: #FAFAFA; }
        Input:focus { border: solid #A1A1AA; }
        #hint { color: #A1A1AA; height: 1; padding: 0 1; }
        #status { height: auto; max-height: 18; border: solid #27272A; padding: 1 1; }
        #diff { height: 1fr; border: solid #27272A; padding: 1 1; color: #FAFAFA; }
        ListView { background: #18181B; border: solid #27272A; }
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
            try:
                st = _status_markup(cockpit(self.state))
            except Exception as exc:
                st = f"estado: {exc}"
            wd = str(self.state.workdir)
            try:
                Path(wd).mkdir(parents=True, exist_ok=True)
            except Exception:
                wd = "."
            yield Header(show_clock=True)
            with Horizontal():
                with Vertical(id="col-files"):
                    yield Static("ARCHIVOS", classes="panel-title")
                    yield WorkspaceTree(wd, id="tree")
                with Vertical(id="col-agent"):
                    yield Static("AGENTE", classes="panel-title")
                    yield Log(id="chat", highlight=False, max_lines=400)
                    with Vertical(id="composer"):
                        yield Static("F1 help  F2 modelo  Tab /cmd  Esc aborta  Ctrl+Q sale", id="hint")
                        yield Input(placeholder="Escribe una instrucción o /help", id="in")
                with Vertical(id="col-side"):
                    yield Static("ESTADO", classes="panel-title")
                    yield Static(st, id="status", markup=True)
                    yield Static("TOOLS / SALIDA / DIFF", classes="panel-title")
                    yield VerticalScroll(Static("Listo.", id="diff", markup=False))
            yield Footer()

        def _handle_exception(self, error: Exception) -> None:
            self.busy = False
            try:
                self._chat().write_line(_safe_txt(f"(capturado) {error}"))
                self.query_one("#in", Input).focus()
            except Exception:
                pass

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

        def on_directory_tree_file_selected(self, event) -> None:
            try:
                raw = getattr(event, "path", None)
                p = Path(str(raw))
            except Exception:
                return
            p = Path(str(p))
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
            for line in SLASH_HELP.splitlines():
                self._chat().write_line(line)
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

        def _line(self, text: str) -> None:
            try:
                self._chat().write_line(_safe_txt(text)[:2000])
            except Exception:
                pass

        def _push_text(self, chunk: str) -> None:
            if not chunk:
                return
            try:
                self._chat().write(_safe_txt(chunk))
            except Exception:
                self._line(chunk)

        def _dispatch(self, line: str) -> None:
            from ottercode_cli.commands import handle_slash
            sl = parse_slash(line)
            if sl and sl.name in ("explain", "quiz"):
                sl = None
            if sl:
                if sl.name == "model" and not sl.arg:
                    self.action_pick_model()
                    return
                if sl.name == "apply":
                    self.state.plan_approved = True
                    self.state.awaiting_plan = False
                    self._chat().write_line("Plan aprobado. Ejecutando…")
                    self.busy = True
                    self._run_agent(
                        "El usuario APROBÓ el plan (todo_read). Ejecuta los pasos ahora. No regeneres el plan."
                    )
                    return
                if sl.name == "clear":
                    self._chat().clear()
                    self._chat().write_line("OtterCode Neo TUI")
                    return
                buf: list[str] = []
                try:
                    handle_slash(self.state, sl, buf.append)
                except Exception as exc:
                    buf.append(str(exc))
                text = "\n".join(buf) or f"/{sl.name}"
                for line in text.splitlines() or [text]:
                    self._chat().write_line(line[:2000])
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
            try:
                if name == "tool_call":
                    self.query_one("#diff", Static).update(f"tool {data.get('tool')}")
                elif name == "tool_result":
                    ok = "ok" if data.get("ok") else "err"
                    out = str(data.get("output") or "")[:800]
                    self.query_one("#diff", Static).update(f"[{ok}]\n{out}")
                elif name == "diff":
                    self.state.pending_diff = str(data.get("diff") or "")
                    self.query_one("#diff", Static).update(self.state.pending_diff[:8000] or "(sin diff)")
                elif name in ("permission_requested", "perm_request"):
                    title = str(data.get("title") or data.get("tool") or "Permiso")
                    pid = str(data.get("id") or "")
                    try:
                        self.query_one("#in", Input).disabled = True
                    except Exception:
                        pass
                    self._chat().write_line(f"Permiso: {title[:200]}")

                    def done(ans: str | None) -> None:
                        from ottercode_cli.core_bridge import resolve_permission
                        resolve_permission(self.state, ans or "d", pid)
                        try:
                            self.query_one("#in", Input).disabled = False
                            self.action_focus_prompt()
                        except Exception:
                            pass

                    self.push_screen(PermModal(title), done)
                elif name == "system":
                    t = str(data.get("text") or "")
                    if t:
                        self._chat().write_line(t[:500])
            except Exception:
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
