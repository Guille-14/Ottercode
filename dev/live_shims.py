"""Accesos 'live' a globals re-asignables compartidos entre módulos.

`_ACTIVE_PROFILE`, `_SOUL_CONTENT` y `_USER_CONTENT` se re-asignan en su módulo
dueño (config/profiles) en tiempo de ejecución (bootstrap, POST save, switch de
perfil). Los `from ... import NOMBRE` de los consumidores dejan una copia
congelada. Este script las convierte en lecturas calificadas por módulo:

    from backend.config  import _SOUL_CONTENT   ->  _otter_cfg._SOUL_CONTENT
    from backend.profiles import _ACTIVE_PROFILE ->  _otter_profiles._ACTIVE_PROFILE

así el valor se lee VIVO en cada acceso sin tocar la semántica del cuerpo.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
P = ROOT / "backend"

LIVE = [
    ("_SOUL_CONTENT", "config"),
    ("_USER_CONTENT", "config"),
    ("_ACTIVE_PROFILE", "profiles"),
]

MODULE_ALIAS = {  # módulo dueño -> alias usado en lecturas
    "config": "_otter_cfg",
    "profiles": "_otter_profiles",
}


def _strip_live_from_import_lines(text: str) -> str:
    names = [n for n, _ in LIVE]
    out: list[str] = []
    for line in text.splitlines(keepends=True):
        if line.startswith("from backend.") and "import *" not in line and any(n in line for n in names):
            body = re.sub(r"\s*#.*$", "", line).strip()
            mod, _, imp = body.partition(" import ")
            if mod.startswith("from "):
                mod = mod[5:]
            kept = [n.strip().rstrip(",") for n in imp.split(",") if n.strip() and n.strip() not in names]
            if kept:
                out.append(f"from {mod} import {', '.join(kept)}  # noqa: E402\n")
            continue
        out.append(line)
    return "".join(out)


def _add_module_imports(text: str) -> str:
    aliases = {o for _, o in LIVE}
    missing = [
        a
        for a in aliases
        if f"import backend.{a} as {MODULE_ALIAS[a]}" not in text
    ]
    if not missing:
        return text
    lines = text.splitlines(keepends=True)
    insert_at = 0
    for i, line in enumerate(lines[:80]):
        stripped = line.lstrip()
        if stripped.startswith("def ") or stripped.startswith("@"):
            insert_at = i
            break
    else:
        insert_at = len(lines)
    for a in missing:
        lines.insert(insert_at, f"import backend.{a} as {MODULE_ALIAS[a]}  # noqa: E402\n")
        insert_at += 1
    return "".join(lines)


def main() -> None:
    for path in sorted(P.glob("*.py")):
        if path.name in ("__init__.py",):
            continue
        if path.stem in {o for _, o in LIVE}:
            continue  # los dueños mantienen sus globals reales
        text = path.read_text(encoding="utf-8")
        if not any(re.search(rf"(?<!\.)\b{n}\b", text) for n, _ in LIVE):
            continue
        changed = _strip_live_from_import_lines(text)
        for n, o in LIVE:
            changed = re.sub(rf"(?<!\.)\b{n}\b", f"{MODULE_ALIAS[o]}.{n}", changed)
        changed = _add_module_imports(changed)
        if changed != text:
            path.write_text(changed, encoding="utf-8")
            print(f"  {path.name}: lecturas live por módulo")


if __name__ == "__main__":
    main()