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
from ottercode_cli.sessions import new_session_id, save_session


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
    return run


def iter_sse(gen: Iterator[str]) -> Iterator[tuple[str, Dict[str, Any]]]:
    buf = ""
    for chunk in gen:
        buf += chunk
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


def run_prompt(state: CliState, text: str, on_event: Optional[EventCb] = None) -> str:
    """Un turno de chat usando el mismo motor que la web."""
    run = make_run(state, text)
    prompt = build_chat_prompt(run, task_text=text)
    collected: List[str] = []
    for name, data in iter_sse(run_agent_turn(run, "agent", 1, prompt)):
        if on_event:
            on_event(name, data)
        if name == "token":
            tok = str(data.get("token") or "")
            collected.append(tok)
        if name == "diff":
            state.pending_diff = str(data.get("diff") or "")
            state.pending_path = str(data.get("path") or "")
        if name == "file_updated":
            state.pending_path = str(data.get("path") or state.pending_path)
        if name == "tool_result" and not data.get("ok"):
            state.last_error = str(data.get("output") or "")[:2000]
        if name == "perm_request":
            if on_event:
                on_event("permission_requested", data)
    text_out = "".join(collected)
    state.messages.append({"role": "user", "content": text})
    state.messages.append({"role": "assistant", "content": text_out[:8000]})
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
    return text_out


def sandbox_run(state: CliState, cmd: str) -> str:
    ex = tools.ToolExecutor(state.workdir)
    res = ex.dispatch("execute_bash", {"cmd": cmd})
    out = str(res.get("output") or "")
    state.last_cmd_out = out
    if not res.get("ok"):
        state.last_error = out
    return out


def read_file(state: CliState, path: str) -> str:
    return str(tools.ToolExecutor(state.workdir).dispatch("read_file", {"filepath": path}).get("output") or "")


def tree(state: CliState) -> str:
    return str(tools.ToolExecutor(state.workdir).dispatch("tree", {"path": "."}).get("output") or "")


def rag_query(state: CliState, q: str) -> str:
    return str(tools.ToolExecutor(state.workdir).dispatch("semantic_search", {"query": q}).get("output") or "")


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
