"""Auto-creación y parcheo de SKILL.md en ~/.ottercode/skills/."""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.home import HOME, cfg_get, ensure_home

SKILLS_ROOT = HOME / "skills"
_ARCHIVE = HOME / "skills" / "_archive"


def _slug(name: str) -> str:
    s = re.sub(r"[^\w.\-]+", "_", (name or "skill").strip())[:64]
    return s or "skill"


def skill_dir(name: str) -> Path:
    return SKILLS_ROOT / _slug(name)


def list_home_skills() -> List[Dict[str, Any]]:
    ensure_home()
    out = []
    if not SKILLS_ROOT.is_dir():
        return out
    for d in sorted(SKILLS_ROOT.iterdir()):
        if not d.is_dir() or d.name.startswith("_"):
            continue
        md = d / "SKILL.md"
        if not md.is_file():
            continue
        meta = _parse(md)
        meta["enabled"] = not (d / "DISABLED").exists()
        out.append(meta)
    return out


def _parse(path: Path) -> Dict[str, Any]:
    raw = path.read_text(encoding="utf-8", errors="replace")
    meta: Dict[str, Any] = {
        "name": path.parent.name,
        "description": "",
        "triggers": [],
        "tools_used": [],
        "path": str(path),
        "body": raw,
    }
    if raw.startswith("---"):
        end = raw.find("\n---", 3)
        if end != -1:
            for line in raw[3:end].splitlines():
                if ":" not in line:
                    continue
                k, v = line.split(":", 1)
                k, v = k.strip().lower(), v.strip().strip('"').strip("'")
                if k == "name":
                    meta["name"] = v
                elif k in ("description", "desc"):
                    meta["description"] = v
                elif k == "triggers":
                    meta["triggers"] = [x.strip() for x in v.strip("[]").split(",") if x.strip()]
                elif k == "tools_used":
                    meta["tools_used"] = [x.strip() for x in v.strip("[]").split(",") if x.strip()]
            meta["body"] = raw[end + 4 :].lstrip("\n")
    return meta


def write_skill(
    name: str,
    description: str,
    body: str,
    triggers: Optional[List[str]] = None,
    tools_used: Optional[List[str]] = None,
) -> Dict[str, Any]:
    ensure_home()
    slug = _slug(name)
    d = SKILLS_ROOT / slug
    d.mkdir(parents=True, exist_ok=True)
    fm = (
        "---\n"
        f"name: {slug}\n"
        f"description: {description[:180]}\n"
        f"triggers: [{', '.join(triggers or [])}]\n"
        f"tools_used: [{', '.join(tools_used or [])}]\n"
        f"created: {datetime.now(timezone.utc).isoformat()}\n"
        "---\n\n"
    )
    (d / "SKILL.md").write_text(fm + (body or "").strip() + "\n", encoding="utf-8")
    try:
        import tools as _t
        _t.load_external_skills()
    except Exception:
        pass
    return {"ok": True, "name": slug, "path": str(d / "SKILL.md")}


def set_enabled(name: str, enabled: bool) -> Dict[str, Any]:
    d = skill_dir(name)
    if not d.is_dir():
        return {"ok": False, "error": "skill no encontrada"}
    flag = d / "DISABLED"
    if enabled:
        flag.unlink(missing_ok=True)
    else:
        flag.write_text("1", encoding="utf-8")
    return {"ok": True, "name": _slug(name), "enabled": enabled}


def delete_skill(name: str, archive: bool = True) -> Dict[str, Any]:
    d = skill_dir(name)
    if not d.is_dir():
        return {"ok": False, "error": "skill no encontrada"}
    if archive:
        _ARCHIVE.mkdir(parents=True, exist_ok=True)
        dest = _ARCHIVE / d.name
        if dest.exists():
            dest = _ARCHIVE / f"{d.name}-{int(datetime.now().timestamp())}"
        d.rename(dest)
        return {"ok": True, "archived": str(dest)}
    import shutil
    shutil.rmtree(d, ignore_errors=True)
    return {"ok": True, "deleted": name}


def restore_skill(name: str) -> Dict[str, Any]:
    src = _ARCHIVE / _slug(name)
    if not src.is_dir():
        cands = list(_ARCHIVE.glob(_slug(name) + "*")) if _ARCHIVE.is_dir() else []
        if not cands:
            return {"ok": False, "error": "archivo no encontrado"}
        src = cands[-1]
    dest = SKILLS_ROOT / _slug(name)
    if dest.exists():
        return {"ok": False, "error": "ya existe"}
    src.rename(dest)
    return {"ok": True, "name": dest.name}


def patch_skill_on_failure(name: str, note: str) -> Dict[str, Any]:
    md = skill_dir(name) / "SKILL.md"
    if not md.is_file():
        return {"ok": False, "error": "no existe"}
    extra = f"\n\n## Lecciones ({datetime.now(timezone.utc).date()})\n- {note.strip()[:400]}\n"
    md.write_text(md.read_text(encoding="utf-8", errors="replace") + extra, encoding="utf-8")
    return {"ok": True, "patched": name}


def maybe_create_from_run(run: Any) -> Optional[Dict[str, Any]]:
    """Si el turno usó ≥3 tools con éxito, extrae un skill stub (sin LLM)."""
    if cfg_get("skills", "write_approval", default=False):
        return None
    tools: List[str] = []
    ok_n = 0
    for ev in getattr(run, "transcript", []) or []:
        if ev.get("kind") == "tool" or ev.get("tool"):
            t = str(ev.get("tool") or ev.get("tool_name") or "")
            if t:
                tools.append(t)
            if ev.get("ok") is True:
                ok_n += 1
    if len(tools) < 3 or ok_n < 3:
        return None
    task = str(getattr(run, "task_text", "") or "")[:80]
    if len(task) < 12:
        return None
    slug = _slug(re.sub(r"\s+", "_", task.lower())[:40])
    if skill_dir(slug).exists():
        return None
    body = (
        f"Patrón extraído de una misión con {len(tools)} tool calls.\n\n"
        f"Tarea original: {task}\n\n"
        f"Herramientas: {', '.join(dict.fromkeys(tools))}\n"
    )
    return write_skill(slug, task, body, tools_used=list(dict.fromkeys(tools)))
