"""Parser de comandos slash de la TUI (sin I/O)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


SLASH_HELP = """/help            esta ayuda
/status          modelo, ctx, sandbox, MCP, memoria
/model [nombre]  ver o cambiar modelo
/context         tokens / num_ctx
/compact         compactar contexto
/memory          recuerdos
/profile         perfil de usuario
/files           árbol del workspace
/open <archivo>  leer archivo
/edit <archivo>  enfocar edición
/diff            cambios pendientes
/apply           aplicar último parche (confirmación)
/reject          descartar parche
/run <cmd>       comando en sandbox
/test            pytest/npm test
/explain         explicar último error
/learn [tema]    modo tutor
/quiz            pregunta de práctica
/clear           limpiar chat
/history         sesiones
/resume [id]     reanudar sesión
/yolo            permisos off
/permissions     ASK_PERMISSIONS
/mcp             estado MCP
/tools           skills
/rag <query>     búsqueda semántica
/config          variables OTTERCODE_*
"""


@dataclass
class SlashCmd:
    name: str
    arg: str = ""


def parse_slash(line: str) -> Optional[SlashCmd]:
    t = (line or "").strip()
    if not t.startswith("/"):
        return None
    parts = t[1:].split(None, 1)
    if not parts:
        return None
    return SlashCmd(name=parts[0].lower(), arg=(parts[1] if len(parts) > 1 else "").strip())
