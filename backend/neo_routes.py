"""Rutas Neo: journey, cron, subagentes, voz, bots, artefactos, config, memory."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

router = APIRouter(tags=["neo"])


class MemoryToolReq(BaseModel):
    action: str
    target: str = "memory"
    text: str = ""
    old_text: str = ""


class CronReq(BaseModel):
    action: str = "list"
    id: str = ""
    schedule: str = ""
    prompt: str = ""
    name: str = ""
    skills: List[str] = Field(default_factory=list)
    script: str = ""
    no_agent: bool = False
    deliver: str = "local"
    workdir: str = ""
    context_from: List[str] = Field(default_factory=list)
    continuity: bool = False
    model: str = ""
    paused: bool = False
    repeat: Optional[Any] = None


class DelegateReq(BaseModel):
    task: str
    context: str = ""
    model: str = ""
    skills: List[str] = Field(default_factory=list)
    tools: List[str] = Field(default_factory=list)
    timeout_seconds: int = 300
    parent_id: str = ""


class SteerReq(BaseModel):
    id: str
    guidance: str = ""


class SynthReq(BaseModel):
    text: str


class BotReq(BaseModel):
    name: str
    title: str = ""
    description: str = ""
    model: str = ""
    soul: str = ""
    skills: List[str] = Field(default_factory=list)
    toolsets: List[str] = Field(default_factory=list)


@router.get("/api/journey")
def api_journey(filter: str = "all") -> Dict[str, Any]:
    from backend.journey import journey_graph
    return journey_graph(filter)


@router.post("/api/memory/tool")
def api_memory_tool(req: MemoryToolReq) -> Dict[str, Any]:
    from backend.memory_md import memory_tool
    return memory_tool(req.action, req.target, req.text, req.old_text)


@router.post("/api/memory/approve/{pid}")
def api_memory_approve(pid: str) -> Dict[str, Any]:
    from backend.memory_md import approve_write
    return approve_write(pid)


@router.get("/api/memory/status")
def api_memory_status() -> Dict[str, Any]:
    from backend.home import cfg_get
    from backend.plugins.memory import get_provider
    p = get_provider()
    return {
        "ok": True,
        "builtin": True,
        "provider": getattr(p, "name", None),
        "write_approval": bool(cfg_get("memory", "write_approval", default=False)),
    }


@router.post("/api/cron")
def api_cron(req: CronReq) -> Dict[str, Any]:
    from backend.cron import jobs as J
    from backend.cron.scheduler import run_now
    act = req.action.lower()
    if act == "list":
        return {"ok": True, "jobs": J.list_jobs()}
    if act == "create":
        return J.create_job(req.model_dump(), from_agent=False)
    if act == "update":
        return J.update_job(req.id or req.name, req.model_dump())
    if act == "pause":
        return J.pause_job(req.id or req.name)
    if act == "resume":
        return J.resume_job(req.id or req.name)
    if act == "remove":
        return J.remove_job(req.id or req.name)
    if act == "run":
        return run_now(req.id or req.name)
    raise HTTPException(400, "acción cron desconocida")


@router.post("/api/delegate")
def api_delegate(req: DelegateReq) -> Dict[str, Any]:
    from backend.subagents import delegate_task
    return delegate_task(
        req.task, req.context, req.model, req.skills, req.tools, req.timeout_seconds, req.parent_id,
    )


@router.get("/api/subagents")
def api_subagents() -> Dict[str, Any]:
    from backend.subagents import list_workers
    return {"ok": True, "workers": list_workers()}


@router.post("/api/subagents/steer")
def api_steer(req: SteerReq) -> Dict[str, Any]:
    from backend.subagents import steer
    return steer(req.id, req.guidance)


@router.post("/api/subagents/stop")
def api_stop(req: SteerReq) -> Dict[str, Any]:
    from backend.subagents import stop
    return stop(req.id)


@router.get("/api/session-search")
def api_session_search(q: str = "", limit: int = 20, session_id: str = "") -> Dict[str, Any]:
    from backend.db import session_search
    return {"ok": True, "hits": session_search(q, limit, session_id or None)}


@router.get("/api/artifacts")
def api_artifacts(session_id: str = "", limit: int = 50, q: str = "") -> Dict[str, Any]:
    from backend.artifacts import list_artifacts
    return list_artifacts(session_id, limit, q)


@router.post("/api/voice/transcribe")
async def api_stt(request: Request) -> Dict[str, Any]:
    from backend.voice import transcribe
    data = await request.body()
    return transcribe(data, "audio.bin")


@router.post("/api/voice/synthesize")
def api_tts(req: SynthReq):
    from backend.voice import synthesize
    r = synthesize(req.text)
    if not r.get("ok"):
        raise HTTPException(501, r.get("error") or "tts")
    return FileResponse(r["path"], media_type=r.get("mime") or "audio/mpeg")


@router.get("/api/bots")
def api_bots() -> Dict[str, Any]:
    from backend.bots import list_bots
    return {"ok": True, "bots": list_bots()}


@router.post("/api/bots")
def api_bots_save(req: BotReq) -> Dict[str, Any]:
    from backend.bots import save_bot
    return save_bot(req.model_dump())


@router.get("/api/neo/config")
def api_neo_config() -> Dict[str, Any]:
    from backend.home import load_config
    return {"ok": True, "config": load_config()}
