"""Grafo skills + memorias para Learning Journey."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from backend.home import HOME
from backend.memory_md import MEMORY_PATH, USER_PATH
from backend.skill_creator import list_home_skills


def journey_graph(filt: str = "all") -> Dict[str, Any]:
    nodes: List[Dict[str, Any]] = []
    for s in list_home_skills():
        st = Path(s["path"]).stat() if Path(s["path"]).exists() else None
        node = {
            "id": "skill:" + s["name"],
            "tipo": "skill",
            "timestamp": st.st_mtime if st else 0,
            "contenido": (s.get("description") or "")[:400],
            "metadata": {"enabled": s.get("enabled"), "path": s.get("path")},
        }
        if filt.lower() == "used" and not s.get("enabled"):
            continue
        if filt.lower() == "learned":
            pass
        nodes.append(node)
    for label, p in (("memory", MEMORY_PATH), ("user", USER_PATH)):
        if p.is_file():
            txt = p.read_text(encoding="utf-8", errors="replace")
            if filt.lower() == "used" and not txt.strip():
                continue
            nodes.append({
                "id": "mem:" + label,
                "tipo": "memory",
                "timestamp": p.stat().st_mtime,
                "contenido": txt[:800],
                "metadata": {"path": str(p)},
            })
    nodes.sort(key=lambda n: n.get("timestamp") or 0)
    edges = []
    skills = [n["id"] for n in nodes if n["tipo"] == "skill"]
    mems = [n["id"] for n in nodes if n["tipo"] == "memory"]
    if skills and mems:
        edges.append({"from": mems[0], "to": skills[0]})
    return {"ok": True, "nodes": nodes, "edges": edges, "filter": filt}
