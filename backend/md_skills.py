"""Skills markdown: backend/skills/<nombre>/SKILL.md (y *.md sueltos)."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from backend.agents import skill_enabled

SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def _parse_bool(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "si", "sí", "on")


def _parse_md(path: Path, default_name: str) -> Dict[str, Any]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    name = default_name
    desc = ""
    auto_invoke = True
    body = raw
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            fm = raw[3:end]
            body = raw[end + 4 :].lstrip("\n")
            for line in fm.splitlines():
                if ":" not in line:
                    continue
                k, v = line.split(":", 1)
                k, v = k.strip().lower(), v.strip().strip('"').strip("'")
                if k in ("name", "nombre"):
                    name = v or name
                elif k in ("description", "descripcion", "desc"):
                    desc = v
                elif k == "auto_invoke":
                    auto_invoke = _parse_bool(v)
    if not desc:
        for line in body.splitlines():
            if line.strip() and not line.startswith("#"):
                desc = line.strip()[:180]
                break
    return {
        "name": re.sub(r"[^\w.\-]", "_", name)[:64] or default_name,
        "desc": desc or f"Skill {default_name}",
        "body": body.strip(),
        "path": str(path),
        "cat": "Markdown",
        "writes_fs": False,
        "auto_invoke": auto_invoke,
        "kind": "markdown",
    }


def list_md_skills() -> List[Dict[str, Any]]:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    out: List[Dict[str, Any]] = []
    seen = set()
    for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md")):
        try:
            meta = _parse_md(skill_md, skill_md.parent.name)
        except OSError:
            continue
        meta["enabled"] = skill_enabled(meta["name"])
        out.append(meta)
        seen.add(meta["name"])
    for p in sorted(SKILLS_DIR.glob("*.md")):
        try:
            meta = _parse_md(p, p.stem)
        except OSError:
            continue
        if meta["name"] in seen:
            continue
        meta["enabled"] = skill_enabled(meta["name"])
        out.append(meta)
        seen.add(meta["name"])
    return out


def md_skill_names() -> set:
    return {s["name"] for s in list_md_skills()}


def get_md_skill(name: str) -> Dict[str, Any] | None:
    key = (name or "").strip().lower()
    for s in list_md_skills():
        if s["name"].lower() == key:
            return s
    return None


def active_skill_prompt(force_name: str = "") -> str:
    blocks = []
    forced = (force_name or "").strip().lower()
    for s in list_md_skills():
        if forced and s["name"].lower() == forced:
            blocks.append(f"## {s['name']}\n{s['body'][:4000]}")
            continue
        if not s.get("enabled"):
            continue
        if s.get("auto_invoke") is False and s["name"].lower() != forced:
            continue
        blocks.append(f"## {s['name']}\n{s['body'][:4000]}")
    return "\n\n".join(blocks)
