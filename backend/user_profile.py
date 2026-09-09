"""Perfil de usuario como schema JSON compacto inyectado en el system prompt."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from pydantic import BaseModel, Field


class UserProfileSchema(BaseModel):
    stack: List[str] = Field(default_factory=list)
    estilo: str = ""
    idioma: str = "es"
    nivel: str = ""
    vetos: List[str] = Field(default_factory=list)
    notas: List[str] = Field(default_factory=list)


_BULLET = re.compile(r"^[\-\*]\s+(.+)$", re.M)


def parse_perfil_md(text: str) -> UserProfileSchema:
    bullets = [m.group(1).strip()[:200] for m in _BULLET.finditer(text or "")]
    stack: List[str] = []
    for b in bullets:
        low = b.lower()
        for kw in ("python", "fastapi", "typescript", "react", "rust", "go", "sql"):
            if kw in low and kw not in stack:
                stack.append(kw)
    estilo = next((b for b in bullets if "ui" in b.lower() or "estilo" in b.lower()), "")
    return UserProfileSchema(stack=stack[:8], estilo=estilo[:160], notas=bullets[:12])


def inject_profile_block(vault_root: Path | None) -> str:
    if vault_root is None:
        return ""
    p = vault_root / "OtterCode" / "Perfil_Usuario.md"
    if not p.is_file():
        return ""
    try:
        raw = p.read_text(encoding="utf-8", errors="replace")[:8000]
    except OSError:
        return ""
    schema = parse_perfil_md(raw)
    blob = schema.model_dump_json(exclude_defaults=True)
    if blob == "{}":
        return ""
    return (
        "\n\n# PERFIL USUARIO (JSON compacto; no copies este bloque al chat)\n"
        f"{blob}\n"
    )
