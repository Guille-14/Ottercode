"""Bots multi-agente en ~/.ottercode/bots/<name>/."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any, Dict, List

from backend.home import HOME, ensure_home

ROOT = HOME / "bots"


def list_bots() -> List[Dict[str, Any]]:
    ensure_home()
    ROOT.mkdir(parents=True, exist_ok=True)
    out = []
    for d in sorted(ROOT.iterdir()):
        if d.is_dir() and (d / "bot.json").is_file():
            try:
                meta = json.loads((d / "bot.json").read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                meta = {"name": d.name}
            meta["path"] = str(d)
            out.append(meta)
    return out


def save_bot(fields: Dict[str, Any]) -> Dict[str, Any]:
    name = "".join(ch for ch in str(fields.get("name") or "bot") if ch.isalnum() or ch in "-_")[:40]
    if not name:
        return {"ok": False, "error": "name inválido"}
    d = ROOT / name
    d.mkdir(parents=True, exist_ok=True)
    meta = {
        "name": name,
        "title": fields.get("title") or name,
        "description": fields.get("description") or "",
        "avatar": fields.get("avatar") or "",
        "model": fields.get("model") or "",
        "skills": fields.get("skills") or [],
        "toolsets": fields.get("toolsets") or [],
        "mcp": fields.get("mcp") or [],
    }
    (d / "bot.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    soul = fields.get("soul") or fields.get("SOUL") or ""
    if soul:
        (d / "SOUL.md").write_text(str(soul), encoding="utf-8")
    return {"ok": True, "bot": meta}


def delete_bot(name: str) -> Dict[str, Any]:
    d = ROOT / name
    if not d.is_dir():
        return {"ok": False, "error": "no existe"}
    shutil.rmtree(d, ignore_errors=True)
    return {"ok": True}


def mention_handoff(text: str) -> Dict[str, Any]:
    """@name al inicio → handoff."""
    t = (text or "").lstrip()
    if not t.startswith("@"):
        return {"handoff": None, "text": text}
    token = t[1:].split()[0]
    rest = t[1 + len(token):].lstrip()
    bots = {b["name"]: b for b in list_bots()}
    if token in bots:
        return {"handoff": bots[token], "text": rest}
    return {"handoff": None, "text": text}
