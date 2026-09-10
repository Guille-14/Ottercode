"""Puente al core existente: OtterRun, tools, sandbox, RAG, MCP. Sin FastAPI."""
from __future__ import annotations

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

import tools
from backend.config import DEFAULT_MODEL, NUM_CTX_DEFAULT, WORKSPACE_ROOT
from backend.prompts import build_chat_prompt
from backend.runstate import OtterRun
from backend.turn import run_agent_turn
from ottercode_cli.sessions import compact_history, new_session_id, save_session


EventCb = Callable[[str, Dict[str, Any]], None]


LEARN_INJECT = (
    "MODO TUTOR (OTTERCODE_LEARNING_MODE): explica paso a paso, propone un ejercicio "
    "corto, corrige el código del usuario, ejecuta en sandbox si hay tests, "
    "señala errores comunes y el siguiente paso. No reescribas proyectos enteros."
)


@dataclass
class CliState:
    workdir: Path
    model: str
    session_id: str
    task_id: str
    pending_diff: str = ""
    pending_path: str = ""
    last_error: str = ""
    last_cmd_out: str = ""
    yolo: bool = False
    learn: bool = False
    learn_topic: str = ""
    messages: List[Dict[str, str]] = field(default_factory=list)
    ctx_used: int = 0
    current_run: Any = None
    busy: bool = False
    session_allow: bool = False
    awaiting_plan: bool = False
    plan_approved: bool = False
    last_role: str = "especialista"
    last_perm: Dict[str, Any] = field(default_factory=dict)
    last_compact: str = ""


def default_workdir() -> Path:
    env = os.environ.get("OTTERCODE_WORKSPACE", "").strip()
    if env:
        p = Path(env)
        p.mkdir(parents=True, exist_ok=True)
        return p
    cwd = Path.cwd()
    return cwd


def make_run(state: CliState, task: str, system_inject: str = "") -> OtterRun:
    inject = system_inject
    if state.learn:
        inject = (inject + "\n" + LEARN_INJECT + f"\nTema: {state.learn_topic or 'programación'}").strip()
    wd = state.workdir
    wd.mkdir(parents=True, exist_ok=True)
    run = OtterRun(
        task_id=state.task_id,
        task_text=task,
        model=state.model,
        loop_mode=False,
        mode="chat",
        start_agent="agent",
        workdir=wd,
        yolo=state.yolo or os.environ.get("OTTERCODE_ASK_PERMISSIONS", "1") in ("0", "false"),
        system_inject=inject,
        num_ctx=NUM_CTX_DEFAULT,
        continue_task=state.task_id if state.messages else "",
    )
    run._session_allow = bool(state.session_allow)
    run.plan_gate = not state.plan_approved
    run.plan_approved = bool(state.plan_approved)
    if state.messages:
        run.messages = compact_history(list(state.messages))
    return run


def iter_sse(gen: Iterator[str]) -> Iterator[tuple[str, Dict[str, Any]]]:
    buf = ""
    for chunk in gen:
        if chunk is None:
            continue
        if isinstance(chunk, bytes):
            chunk = chunk.decode("utf-8", "replace")
        buf += str(chunk)
        while "\n\n" in buf:
            frame, buf = buf.split("\n\n", 1)
            name, data = "", {}
            for line in frame.split("\n"):
                if line.startswith("event:"):
                    name = line[6:].strip()
                elif line.startswith("data:"):
                    raw = line[5:].strip()
                    try:
                        data = json.loads(raw)
                    except json.JSONDecodeError:
                        data = {"text": raw}
            if name:
                yield name, data


def abort_run(state: CliState) -> bool:
    run = state.current_run
    if run is None:
        return False
    run.aborted = True
    resp = getattr(run, "_active_resp", None)
    try:
        if resp is not None:
            resp.close()
    except Exception:
        pass
    return True


def resolve_permission(state: CliState, decision: str, perm_id: str = "") -> bool:
    """a=aprobar, d=denegar, s=siempre esta sesión. Despierta el wait del motor."""
    pid = perm_id or str((state.last_perm or {}).get("id") or "")
    raw = (decision or "").strip().lower()
    allow = raw in ("a", "s", "y", "yes", "si", "sí", "always", "aprobar", "1")
    always = raw in ("s", "always", "siempre")
    if always:
        state.session_allow = True
        if state.current_run is not None:
            try:
                state.current_run._session_allow = True
            except Exception:
                pass
    try:
        from backend.engine import PENDING_PERMISSIONS, PERMISSION_RESPONSES
        if pid:
            PERMISSION_RESPONSES[pid] = allow
            ev = PENDING_PERMISSIONS.get(pid)
            if ev is not None:
                ev.set()
    except Exception:
        pass
    return allow


def _looks_like_mission(text: str) -> bool:
    t = (text or "").strip().lower()
    if len(t) > 80:
        return True
    keys = (
        "implementa", "crea", "escribe", "refactor", "html", "css", "app",
        "web", "archivo", "proyecto", "página", "pagina", "fix", "bug",
    )
    return any(k in t for k in keys)


_PLAN_TOOLS = {
    "todo_write", "todo_read", "tree", "read_file", "list_dir",
    "grep_search", "glob_files", "finalizar",
}


def todo_snapshot(state: CliState) -> Dict[str, Any]:
    p = state.workdir / ".otter_todo.json"
    items: List[Dict[str, Any]] = []
    if p.is_file():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, list):
                items = [x for x in data if isinstance(x, dict)]
        except (json.JSONDecodeError, OSError):
            items = []
    done = sum(1 for t in items if str(t.get("status") or "").lower() in ("done", "completed", "completado"))
    return {"items": items, "done": done, "total": len(items)}


def mcp_panel() -> str:
    try:
        import mcp_client
        mgr = mcp_client.get_mcp_manager()
        ready = False
        try:
            ready = bool(mcp_client.mcp_loop.is_ready())
        except Exception:
            ready = bool(mgr.is_available)
        lines = [f"mcp_ready={ready}"]
        sessions = getattr(mgr, "sessions", {}) or {}
        if not sessions:
            lines.append("(sin servidores en mcp_servers.json)")
        for name, sess in sessions.items():
            conn = (getattr(mgr, "_connections", {}) or {}).get(name)
            tools_n = list(conn.tools) if conn else [t.get("name") for t in (getattr(sess, "tools", None) or [])]
            ok = bool(conn and conn.connected) if conn else bool(getattr(sess, "_connected", False))
            lines.append(f"- {name}: {'conectado' if ok else 'off'}")
            for t in tools_n[:40]:
                lines.append(f"    · {t}")
        catalog = list((getattr(mgr, "tools_catalog", {}) or {}).keys())
        if catalog and not any("·" in ln for ln in lines):
            for t in catalog[:40]:
                lines.append(f"    · {t}")
        return "\n".join(lines)
    except Exception as exc:
        return f"mcp error: {exc}"


def discard_pending_plan(state: CliState) -> None:
    state.awaiting_plan = False
    state.plan_approved = False
    p = state.workdir / ".otter_todo.json"
    try:
        if p.is_file():
            p.unlink()
    except OSError:
        pass


def friendly_error(exc: BaseException) -> str:
    from backend.runstate import AbortRequested
    if isinstance(exc, AbortRequested):
        return "(abortado)"
    msg = str(exc) or type(exc).__name__
    low = msg.lower()
    if "connection" in low or "refused" in low or "inaccesible" in low:
        return f"Ollama no responde. Arranca: ollama serve  ({msg[:180]})"
    if "not found" in low:
        return msg[:400]
    return msg[:600]


def run_prompt(state: CliState, text: str, on_event: Optional[EventCb] = None) -> str:
    """Un turno de chat usando el mismo motor que la web."""
    from backend.runstate import AbortRequested
    try:
        from backend.ollama import resolve_coder_model
        state.model = resolve_coder_model(state.model) or state.model
    except Exception:
        pass
    try:
        from backend.router import route, ROUTER_MODEL, is_router_model
        dec = route(text)
        if dec.get("tipo") == "directo":
            state.last_role = "router"
            if is_router_model(state.model) or True:
                pass
        else:
            state.last_role = "especialista"
        _ = ROUTER_MODEL
    except Exception:
        state.last_role = "especialista"
    run = make_run(state, text)
    state.current_run = run
    state.busy = True
    collected: List[str] = []
    prompt = ""
    only = None
    extra = ""
    if not state.plan_approved and _looks_like_mission(text) and not state.yolo:
        only = set(_PLAN_TOOLS)
        extra = (
            "\n\nPLAN OBLIGATORIO (todo_write, mismo sistema que la web): "
            "ANTES de cualquier write_file/edit_file, guarda un plan de ≥3 pasos "
            "con todo_write y finaliza. El usuario usará /apply, /reject o texto "
            "libre para editar el plan. PROHIBIDO escribir archivos de código ahora."
        )
    try:
        prompt = build_chat_prompt(run, task_text=text) + extra
        gen = run_agent_turn(run, "agent", 1, prompt, only_tools=only)
        for name, data in iter_sse(gen):
            if name == "perm_request":
                state.last_perm = dict(data or {})
                if state.yolo or state.session_allow:
                    resolve_permission(state, "s", str(data.get("id") or ""))
                if on_event:
                    try:
                        on_event("permission_requested", data)
                        on_event("perm_request", data)
                    except Exception:
                        pass
                continue
            if on_event:
                try:
                    on_event(name, data)
                except Exception:
                    pass
            if name == "token":
                tok = str(data.get("token") or "")
                collected.append(tok)
            if name == "diff":
                state.pending_diff = str(data.get("diff") or "")
                state.pending_path = str(data.get("path") or "")
            if name == "file_updated":
                state.pending_path = str(data.get("path") or state.pending_path)
            if name == "tool_result":
                if not data.get("ok"):
                    state.last_error = str(data.get("output") or "")[:2000]
                if str(data.get("tool") or "") == "todo_write" and data.get("ok"):
                    snap = todo_snapshot(state)
                    if snap["total"] >= 2 and not state.plan_approved:
                        state.awaiting_plan = True
            if name == "system":
                t = str(data.get("text") or "")
                if "compact" in t.lower() or "compactación" in t.lower() or "🗜️" in t:
                    state.last_compact = t
                    if on_event:
                        try:
                            on_event("system", {"text": "[contexto compactado] " + t[:240]})
                        except Exception:
                            pass
    except AbortRequested:
        if on_event:
            try:
                on_event("system", {"text": "(abortado)"})
            except Exception:
                pass
    except Exception as exc:
        msg = friendly_error(exc)
        state.last_error = msg
        if on_event:
            try:
                on_event("system", {"text": msg})
            except Exception:
                pass
        collected.append("\n" + msg)
    finally:
        state.current_run = None
        state.busy = False
    text_out = "".join(collected)
    state.ctx_used = min(NUM_CTX_DEFAULT, max(state.ctx_used, (len(prompt) + len(text_out)) // 4))
    state.messages.append({"role": "user", "content": text})
    state.messages.append({"role": "assistant", "content": text_out[:8000]})
    try:
        save_session({
            "id": state.session_id,
            "task_id": state.task_id,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "model": state.model,
            "task": text[:200],
            "workdir": str(state.workdir),
            "messages": state.messages[-40:],
            "learn": state.learn,
            "learn_topic": state.learn_topic,
        })
    except Exception:
        pass
    return text_out


def sandbox_run(state: CliState, cmd: str) -> str:
    try:
        ex = tools.ToolExecutor(state.workdir)
        res = ex.dispatch("execute_bash", {"cmd": cmd})
        out = str(res.get("output") or "")
        state.last_cmd_out = out
        if not res.get("ok"):
            state.last_error = out
        return out
    except Exception as exc:
        msg = friendly_error(exc)
        state.last_error = msg
        return msg


def read_file(state: CliState, path: str) -> str:
    try:
        return str(tools.ToolExecutor(state.workdir).dispatch("read_file", {"filepath": path}).get("output") or "")
    except Exception as exc:
        return friendly_error(exc)


def tree(state: CliState) -> str:
    try:
        return str(tools.ToolExecutor(state.workdir).dispatch("tree", {"path": "."}).get("output") or "")
    except Exception as exc:
        return friendly_error(exc)


def rag_query(state: CliState, q: str) -> str:
    return str(tools.ToolExecutor(state.workdir).dispatch("semantic_search", {"query": q}).get("output") or "")


_VRAM_CACHE: tuple[float, float, float] = (0.0, 8.0, 0.0)


def vram_usage() -> tuple[float, float]:
    """(used_gb, total_gb). nvidia-smi cacheado 4s para no congelar la TUI."""
    global _VRAM_CACHE
    now = time.monotonic()
    used0, tot0, ts = _VRAM_CACHE
    if now - ts < 4.0 and ts > 0:
        return used0, tot0
    import subprocess
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
            timeout=1,
            text=True,
        ).strip().splitlines()[0]
        used, total = [float(x.strip()) for x in out.split(",")[:2]]
        _VRAM_CACHE = (used / 1024.0, total / 1024.0, now)
        return _VRAM_CACHE[0], _VRAM_CACHE[1]
    except Exception:
        try:
            from backend.config import VRAM_TOTAL_BYTES
            tot = max(VRAM_TOTAL_BYTES / (1024 ** 3), 1.0)
        except Exception:
            tot = 8.0
        _VRAM_CACHE = (used0, tot, now)
        return used0, tot


def cockpit(state: CliState) -> Dict[str, Any]:
    import mcp_client
    ask = os.environ.get("OTTERCODE_ASK_PERMISSIONS", "1")
    sand = os.environ.get("OTTERCODE_SANDBOX_REQUIRED", "1")
    mcp_ok = False
    try:
        mcp_ok = bool(mcp_client.mcp_loop.is_ready())
    except Exception:
        mcp_ok = False
    mem = ""
    used_gb, tot_gb = vram_usage()
    ctx_tot = int(NUM_CTX_DEFAULT)
    ctx_used = int(state.ctx_used or 0)
    yolo = state.yolo or ask in ("0", "false")
    snap = todo_snapshot(state)
    role = state.last_role if state.last_role in ("router", "especialista") else "especialista"
    return {
        "model": state.model,
        "role": role,
        "ctx_used": ctx_used,
        "ctx_tot": ctx_tot,
        "vram_used": used_gb,
        "vram_tot": tot_gb,
        "sandbox": "bubblewrap" if sand not in ("0", "false") else "off",
        "mcp": mcp_ok,
        "memory": mem or "Perfil usuario",
        "workdir": str(state.workdir),
        "perms": "YOLO" if yolo else "ASK",
        "session": state.session_id,
        "ollama": os.environ.get("OTTERCODE_OLLAMA", "http://127.0.0.1:11434"),
        "todo_done": snap["done"],
        "todo_total": snap["total"],
    }


def status_blob(state: CliState) -> str:
    import mcp_client
    ask = os.environ.get("OTTERCODE_ASK_PERMISSIONS", "1")
    sand = os.environ.get("OTTERCODE_SANDBOX_REQUIRED", "1")
    mcp_ok = False
    try:
        mcp_ok = bool(mcp_client.mcp_loop.is_ready())
    except Exception:
        mcp_ok = False
    mem = ""
    try:
        from backend.memory import get_memory
        mem = (get_memory() or "")[:400]
    except Exception:
        mem = ""
    return (
        f"modelo={state.model}\n"
        f"num_ctx={NUM_CTX_DEFAULT}\n"
        f"sandbox_required={sand}\n"
        f"ask_permissions={ask} yolo={state.yolo}\n"
        f"mcp={mcp_ok}\n"
        f"workdir={state.workdir}\n"
        f"session={state.session_id}\n"
        f"memoria={mem or '(vacía)'}\n"
    )


def list_models() -> List[str]:
    try:
        from backend.ollama import fetch_models
        return list(fetch_models() or [])
    except Exception:
        return []


def new_state(workdir: Optional[Path] = None, model: Optional[str] = None) -> CliState:
    sid = new_session_id()
    wd = workdir or default_workdir()
    task_dir = WORKSPACE_ROOT / sid
    task_dir.mkdir(parents=True, exist_ok=True)
    # CLI trabaja sobre el cwd del usuario; el executor del run usa workdir de misión.
    # Para edit/learn sobre el proyecto actual, usamos cwd.
    use = wd
    return CliState(
        workdir=use,
        model=model or os.environ.get("OTTERCODE_CLI_MODEL") or os.environ.get("OTTERCODE_MODEL") or DEFAULT_MODEL,
        session_id=sid,
        task_id=sid,
    )
