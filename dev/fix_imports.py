"""Auto-añade imports faltantes del paquete backend/.

Cubre: (1) helpers '_' usados en un módulo pero definidos en otro (los wildcard
no los exportan) y (2) nombres públicos definidos en un módulo y usados en otro
sin llegar por ningún wildcard. Evita crear ciclos de importación. Idempotente.
"""

from __future__ import annotations

import re
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
P = ROOT / "backend"
MODS = [p.stem for p in P.glob("*.py") if p.stem != "__init__"]

DEF_RE = re.compile(r"^def ([A-Za-z_]\w*)\(", re.M)
CLASS_RE = re.compile(r"^class ([A-Za-z_]\w*):", re.M)
CONST_RE = re.compile(r"^([A-Z][A-Z0-9_]*)\s*(?::[^=]*)?=", re.M)
UNDER_CONST_RE = re.compile(r"^(_[A-Za-z_]\w*)\s*(?::[^=]*)?=", re.M)
USE_RE = re.compile(r"(?<!\.)\b([A-Za-z_]\w*)\b")
EXPLICIT_RE = re.compile(r"^from backend\.(\w+) import", re.M)
WILDCARD_RE = re.compile(r"^from backend\.(\w+) import \*", re.M)

BUILTINS = set("""
abs aiter all any anext ascii bin bool breakpoint bytearray bytes callable chr
classmethod compile complex delattr dict dir divmod enumerate eval exec filter
float format frozenset getattr globals hasattr hash help hex id input int
isinstance issubclass iter len list locals map max memoryview min next object
oct open ord pow print property range repr reversed round set setattr slice
sorted staticmethod str sum super tuple type vars zip __import__
""".split())
KEYWORDS = set("""
False None True and as assert async await break class continue def del elif else
except finally for from global if import in is lambda nonlocal not or pass raise
return try while with yield match case
""".split())
SKIP = BUILTINS | KEYWORDS | {"self", "cls", "app", "router"}

texts = {m: (P / f"{m}.py").read_text(encoding="utf-8") for m in MODS}

# nombres definidos a nivel de módulo
owned: dict[str, set[str]] = defaultdict(set)
for m, t in texts.items():
    owned[m] |= set(DEF_RE.findall(t)) | set(CLASS_RE.findall(t)) | set(CONST_RE.findall(t)) | set(UNDER_CONST_RE.findall(t))

# gráfica explícita M → importa O
imports: dict[str, set[str]] = defaultdict(set)
for m, t in texts.items():
    imports[m] |= set(EXPLICIT_RE.findall(t)) | set(WILDCARD_RE.findall(t))

# cierre transitivo (dependencias de cada módulo)
deps: dict[str, set[str]] = {}
def depends(m):
    if m in deps:
        return deps[m]
    deps[m] = set(imports[m])
    for o in set(imports[m]):
        deps[m] |= depends(o)
    return deps[m]

# sin ciclo: M puede importar de O si O no depende (directa o transitivamente) de M
def cycle_free(m, o):
    return m not in depends(o) and o != m

needed: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
for m in MODS:
    for name in USE_RE.findall(texts[m]):
        if name in SKIP or name in owned[m]:
            continue
        owners = [d for d in MODS if name in owned[d] and d != m]
        if len(owners) == 1 and cycle_free(m, owners[0]):
            needed[m][owners[0]].add(name)

for m, owners in sorted(needed.items()):
    path = P / f"{m}.py"
    lines = texts[m].splitlines(keepends=True)
    changed = False
    for owner in sorted(owners):
        names = sorted(needed[m][owner])
        already = set()
        for ln in lines:
            if ln.startswith(f"from backend.{owner} import"):
                already |= {n.strip() for n in ln.split("import", 1)[1].replace("(\\", ",").replace(")", "").split(",") if n.strip()}
        missing = sorted(n for n in names if n not in already)
        if not missing:
            continue
        imp = f"from backend.{owner} import {', '.join(missing)}  # noqa: E402\n"
        anchor = next(i for i, ln in enumerate(lines) if ln.startswith("from backend.") and "import *" in ln)
        lines.insert(anchor + 1, imp)
        changed = True
    if changed:
        path.write_text("".join(lines), encoding="utf-8")
        print(f"  {m}.py ← " + ", ".join(f"{o}({len(n)})" for o, n in owners.items()))