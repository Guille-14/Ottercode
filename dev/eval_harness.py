#!/usr/bin/env python3
"""Eval: 5 tests automatizados sin GPU + 15 definiciones para GPU real."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

TASKS = [
    {"id": "e01", "prompt": "landing HTML de una nutria", "expect": ["index.html"], "auto": True},
    {"id": "e02", "prompt": "API FastAPI hello world", "expect": ["main.py"], "auto": True},
    {"id": "e03", "prompt": "README del proyecto", "expect": [".md"], "auto": True},
    {"id": "e04", "prompt": "script Python que sume CSV", "expect": [".py"], "auto": True},
    {"id": "e09", "prompt": "Dockerfile python slim", "expect": ["Dockerfile"], "auto": True},
    {"id": "e05", "prompt": "página CSS grid de recetas", "expect": [".html", ".css"]},
    {"id": "e06", "prompt": "tests pytest de una función suma", "expect": ["test_"]},
    {"id": "e07", "prompt": "cli argparse echo", "expect": [".py"]},
    {"id": "e08", "prompt": "todo list vanilla JS", "expect": [".html", ".js"]},
    {"id": "e10", "prompt": "gitignore python+node", "expect": [".gitignore"]},
    {"id": "e11", "prompt": "función TypeScript debounce", "expect": [".ts"]},
    {"id": "e12", "prompt": "tabla HTML de precios", "expect": [".html"]},
    {"id": "e13", "prompt": "regex email en python", "expect": [".py"]},
    {"id": "e14", "prompt": "svg logo nutria", "expect": [".svg"]},
    {"id": "e15", "prompt": "sql schema users", "expect": [".sql", ".py"]},
    {"id": "e16", "prompt": "markdown changelog", "expect": [".md"]},
    {"id": "e17", "prompt": "json schema producto", "expect": [".json"]},
    {"id": "e18", "prompt": "bash script backup", "expect": [".sh"]},
    {"id": "e19", "prompt": "componente React contador", "expect": [".tsx", ".jsx"]},
    {"id": "e20", "prompt": "plan ultraplan sin archivos", "expect": ["plan_ready"]},
]

# Respuestas predefinidas del "modelo" (sin Ollama).
MOCK_FILES = {
    "e01": {"index.html": "<!DOCTYPE html><html><body>nutria</body></html>\n"},
    "e02": {"main.py": "from fastapi import FastAPI\napp = FastAPI()\n@app.get('/')\ndef hi():\n    return {'ok': True}\n"},
    "e03": {"README.md": "# Proyecto\nEval auto.\n"},
    "e04": {"sum_csv.py": "import csv,sys\nprint(sum(float(r[0]) for r in csv.reader(sys.stdin)))\n"},
    "e09": {"Dockerfile": "FROM python:3.12-slim\nCMD [\"python\",\"-c\",\"print(1)\"]\n"},
}


def _match(workdir: Path, expect: list[str]) -> bool:
    names = [p.name for p in workdir.rglob("*") if p.is_file()]
    blob = " ".join(names)
    for token in expect:
        if token.startswith(".") and token != ".gitignore":
            if not any(n.endswith(token) or n == token.lstrip(".") for n in names) and token not in blob:
                if not any(n.endswith(token) for n in names):
                    return False
        elif token == "plan_ready":
            continue
        else:
            if not any(token in n for n in names):
                return False
    nonempty = False
    for p in workdir.rglob("*"):
        if p.is_file() and p.stat().st_size > 0:
            nonempty = True
            break
    return nonempty


def run_auto(task: dict) -> bool:
    import tools
    files = MOCK_FILES[task["id"]]
    with tempfile.TemporaryDirectory(prefix="otter-eval-") as td:
        ex = tools.ToolExecutor(td)
        for path, content in files.items():
            r = ex.dispatch("write_file", {"filepath": path, "content": content})
            if not r.get("ok"):
                return False
        wd = Path(td)
        if not _match(wd, task["expect"]):
            return False
        for path in files:
            p = wd / path
            if not p.is_file() or p.stat().st_size == 0:
                return False
        return True


def main() -> int:
    out = Path(__file__).with_name("eval_tasks.json")
    dump = [{k: v for k, v in t.items() if k != "auto"} for t in TASKS]
    out.write_text(json.dumps(dump, ensure_ascii=False, indent=2), encoding="utf-8")
    autos = [t for t in TASKS if t.get("auto")]
    ok_n = 0
    for t in autos:
        ok = run_auto(t)
        print(f"  [{'PASS' if ok else 'FAIL'}] {t['id']} {t['prompt']}")
        ok_n += int(ok)
    print(f"{ok_n}/{len(autos)} tareas OK")
    return 0 if ok_n == len(autos) else 1


if __name__ == "__main__":
    raise SystemExit(main())
