"""Memoria formato Hermes: MEMORY.md + USER.md en ~/.ottercode/memories/."""
from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.home import HOME, cfg_get, ensure_home

MEM_DIR = HOME / "memories"
MEMORY_PATH = MEM_DIR / "MEMORY.md"
USER_PATH = MEM_DIR / "USER.md"

_INJECT = re.compile(
    r"(ignore (all )?previous|you are now|exfiltrat|api[_-]?key|sk-[a-z0-9]{10}|password\s*=)",
    re.I,
)
_INVIS = re.compile(r"[\u200b-\u200f\u202a-\u202e\ufeff]")


def _limits() -> tuple[int, int]:
    return (
        int(cfg_get("memory", "memory_char_limit", default=2200) or 2200),
        int(cfg_get("memory", "user_char_limit", default=1375) or 1375),
    )


def _path(target: str) -> Path:
    ensure_home()
    MEM_DIR.mkdir(parents=True, exist_ok=True)
    return USER_PATH if target == "user" else MEMORY_PATH


def _read(target: str) -> str:
    p = _path(target)
    if not p.exists():
        p.write_text("", encoding="utf-8")
        return ""
    return p.read_text(encoding="utf-8", errors="replace")


def frozen_snapshot() -> str:
    mem_on = bool(cfg_get("memory", "memory_enabled", default=True))
    user_on = bool(cfg_get("memory", "user_profile_enabled", default=True))
    parts = []
    if mem_on:
        t = _read("memory").strip()
        if t:
            parts.append("# MEMORY.md\n" + t)
    if user_on:
        t = _read("user").strip()
        if t:
            parts.append("# USER.md\n" + t)
    return "\n\n".join(parts)


def _scan(text: str) -> Optional[str]:
    if _INVIS.search(text):
        return "unicode invisible rechazado"
    if _INJECT.search(text):
        return "posible inyección o credencial rechazada"
    if any(unicodedata.category(ch) == "Cf" and ch not in "\t\n\r" for ch in text):
        return "carácter de formato rechazado"
    return None


def _entries(text: str) -> List[str]:
    parts = [p.strip() for p in re.split(r"\n(?=- |\n)", text) if p.strip()]
    if not parts and text.strip():
        parts = [text.strip()]
    return parts


def memory_tool(
    action: str,
    target: str = "memory",
    text: str = "",
    old_text: str = "",
) -> Dict[str, Any]:
    tgt = "user" if str(target).lower() in ("user", "user.md", "profile") else "memory"
    if tgt == "memory" and not cfg_get("memory", "memory_enabled", default=True):
        return {"ok": False, "error": "memory_enabled=false"}
    if tgt == "user" and not cfg_get("memory", "user_profile_enabled", default=True):
        return {"ok": False, "error": "user_profile_enabled=false"}
    cap = _limits()[1] if tgt == "user" else _limits()[0]
    cur = _read(tgt)
    act = (action or "").lower().strip()
    if act == "add":
        err = _scan(text)
        if err:
            return {"ok": False, "error": err}
        blob = (text or "").strip()
        if not blob:
            return {"ok": False, "error": "texto vacío"}
        if blob.lower() in cur.lower():
            return {"ok": True, "skipped": "duplicate"}
        nxt = (cur.rstrip() + ("\n" if cur.strip() else "") + "- " + blob).strip() + "\n"
        if len(nxt) > cap:
            return {
                "ok": False,
                "error": "limit_exceeded",
                "limit": cap,
                "current_entries": _entries(cur),
            }
        if cfg_get("memory", "write_approval", default=False):
            return _stage("add", tgt, nxt)
        _path(tgt).write_text(nxt, encoding="utf-8")
        return {"ok": True, "target": tgt, "chars": len(nxt)}
    if act == "replace":
        err = _scan(text)
        if err:
            return {"ok": False, "error": err}
        needle = old_text or ""
        if not needle or needle not in cur:
            return {"ok": False, "error": "old_text no encontrado (substring)"}
        nxt = cur.replace(needle, text, 1)
        if len(nxt) > cap:
            return {"ok": False, "error": "limit_exceeded", "limit": cap, "current_entries": _entries(cur)}
        if cfg_get("memory", "write_approval", default=False):
            return _stage("replace", tgt, nxt)
        _path(tgt).write_text(nxt, encoding="utf-8")
        return {"ok": True, "target": tgt}
    if act == "remove":
        needle = old_text or text
        if not needle or needle not in cur:
            return {"ok": False, "error": "texto no encontrado"}
        nxt = cur.replace(needle, "", 1)
        nxt = re.sub(r"\n{3,}", "\n\n", nxt)
        if cfg_get("memory", "write_approval", default=False):
            return _stage("remove", tgt, nxt)
        _path(tgt).write_text(nxt, encoding="utf-8")
        return {"ok": True, "target": tgt}
    return {"ok": False, "error": f"acción desconocida: {action}"}


_PENDING: Dict[str, Dict[str, Any]] = {}


def _stage(action: str, target: str, content: str) -> Dict[str, Any]:
    pid = f"mem-{len(_PENDING)+1}"
    _PENDING[pid] = {"action": action, "target": target, "content": content}
    return {"ok": True, "pending": pid, "hint": f"/memory approve {pid}"}


def approve_write(pid: str) -> Dict[str, Any]:
    item = _PENDING.pop(pid, None)
    if not item:
        return {"ok": False, "error": "id desconocido"}
    _path(item["target"]).write_text(item["content"], encoding="utf-8")
    return {"ok": True, "applied": pid}


def pending_ids() -> List[str]:
    return list(_PENDING)
