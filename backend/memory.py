import os
from pathlib import Path
from typing import List, Dict

MEMORY_PATH = Path("memory.md")

def get_memory() -> str:
    if not MEMORY_PATH.exists():
        return ""
    return MEMORY_PATH.read_text(encoding="utf-8")

def update_memory(new_memory: str):
    MEMORY_PATH.write_text(new_memory, encoding="utf-8")

def add_memory(memory_item: str):
    current = get_memory()
    new_content = f"{current.strip()}\n\n- {memory_item.strip()}" if current else f"- {memory_item.strip()}"
    update_memory(new_content)

def delete_memory_item(item_index: int):
    # Lógica simplificada: eliminamos línea por línea
    lines = [line for line in get_memory().split('\n') if line.strip().startswith('-')]
    if 0 <= item_index < len(lines):
        lines.pop(item_index)
        update_memory('\n'.join(lines))
