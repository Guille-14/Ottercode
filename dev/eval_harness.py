#!/usr/bin/env python3
"""Harness de evaluación (20 tareas) sin GPU: valida el corpus, no llama al LLM."""
from __future__ import annotations
import json
from pathlib import Path

TASKS = [
    {"id": "e01", "prompt": "landing HTML de una nutria", "expect": ["index.html"]},
    {"id": "e02", "prompt": "API FastAPI hello world", "expect": ["main.py"]},
    {"id": "e03", "prompt": "README del proyecto", "expect": [".md"]},
    {"id": "e04", "prompt": "script Python que sume CSV", "expect": [".py"]},
    {"id": "e05", "prompt": "página CSS grid de recetas", "expect": [".html", ".css"]},
    {"id": "e06", "prompt": "tests pytest de una función suma", "expect": ["test_"]},
    {"id": "e07", "prompt": "cli argparse echo", "expect": [".py"]},
    {"id": "e08", "prompt": "todo list vanilla JS", "expect": [".html", ".js"]},
    {"id": "e09", "prompt": "Dockerfile python slim", "expect": ["Dockerfile"]},
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


def main() -> int:
    out = Path(__file__).with_name("eval_tasks.json")
    out.write_text(json.dumps(TASKS, ensure_ascii=False, indent=2), encoding="utf-8")
    assert len(TASKS) == 20
    print(f"20 tareas de eval en {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
