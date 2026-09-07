"""Skills markdown: backend/skills/*.md con frontmatter nombre/descripción."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Dict, List

from backend.agents import _load_skills_cfg, skill_enabled

SKILLS_DIR = Path(__file__).resolve().parent / "skills"


def _parse_md(path: Path) -> Dict[str, Any]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    name = path.stem
    desc = ""
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
    if not desc:
        for line in body.splitlines():
            if line.strip() and not line.startswith("#"):
                desc = line.strip()[:180]
                break
    return {
        "name": re.sub(r"[^\w.\-]", "_", name)[:64] or path.stem,
        "desc": desc or f"Skill {path.stem}",
        "body": body.strip(),
        "path": str(path),
        "cat": "Markdown",
        "writes_fs": False,
    }


def list_md_skills() -> List[Dict[str, Any]]:
    SKILLS_DIR.mkdir(parents=True, exist_ok=True)
    out: List[Dict[str, Any]] = []
    for p in sorted(SKILLS_DIR.glob("*.md")):
        try:
            meta = _parse_md(p)
        except OSError:
            continue
        meta["enabled"] = skill_enabled(meta["name"])
        out.append(meta)
    return out


def md_skill_names() -> set:
    return {s["name"] for s in list_md_skills()}


def active_skill_prompt() -> str:
    blocks = []
    for s in list_md_skills():
        if not s.get("enabled"):
            continue
        blocks.append(f"## {s['name']}\n{s['body'][:4000]}")
    return "\n\n".join(blocks)
