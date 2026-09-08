"""Memoria automática persistente (SQLite) — preferencias duraderas del usuario."""
from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from typing import Any, List, Optional

from backend.config import DB_PATH, LLM_BACKEND, NUM_CTX_DEFAULT, OLLAMA_BASE_URL
from backend.agents import _chat_base
from backend.ollama import _ollama_ndjson_text
import requests

_EXTRACT_SYSTEM = (
    "Extrae en una sola frase cualquier preferencia o dato duradero sobre el "
    "usuario en este intercambio, o responde exactamente NADA si no hay nada "
    "relevante. Sin comillas, sin preámbulo."
)


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute(
        """CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agente_id TEXT,
            contenido TEXT NOT NULL,
            timestamp TEXT NOT NULL
        )"""
    )
    return conn


def get_memory() -> str:
    """Texto listo para inyectar en el system prompt (memorias recientes)."""
    try:
        conn = _conn()
        rows = conn.execute(
            "SELECT contenido, timestamp FROM memories ORDER BY id DESC LIMIT 24"
        ).fetchall()
        conn.close()
    except Exception:
        return ""
    sqlite_txt = ""
    if rows:
        lines = [f"- {c} ({ts})" for c, ts in rows]
        sqlite_txt = "\n".join(reversed(lines))
    vault_txt = ""
    try:
        from backend.vault import _memory_recall
        vault_txt = (_memory_recall("preferencias usuario perfil") or "").strip()[:800]
    except Exception:
        vault_txt = ""
    parts = [p for p in (sqlite_txt, vault_txt) if p]
    return "\n\n".join(parts)


def add_memory(memory_item: str, agente_id: Optional[str] = None) -> None:
    item = (memory_item or "").strip()
    if not item or item.upper() == "NADA":
        return
    if len(item) > 400:
        item = item[:400]
    try:
        conn = _conn()
        dup = conn.execute(
            "SELECT 1 FROM memories WHERE contenido = ? LIMIT 1", (item,)
        ).fetchone()
        if dup:
            conn.close()
            return
        conn.execute(
            "INSERT INTO memories (agente_id, contenido, timestamp) VALUES (?,?,?)",
            (agente_id, item, datetime.now().strftime("%Y-%m-%d %H:%M")),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


def delete_memory_item(item_index: int) -> None:
    try:
        conn = _conn()
        rows = conn.execute("SELECT id FROM memories ORDER BY id ASC").fetchall()
        if 0 <= item_index < len(rows):
            conn.execute("DELETE FROM memories WHERE id = ?", (rows[item_index][0],))
            conn.commit()
        conn.close()
    except Exception:
        pass


def harvest_memory(run: Any, agent_id: str = "", last_text: str = "") -> None:
    """Tras una respuesta: extrae un dato duradero o no guarda nada."""
    if os.environ.get("OTTERCODE_MEMORY_LLM", "1") == "0":
        return
    user_bit = str(getattr(run, "task_text", "") or "")[:800]
    asst = (last_text or "")[:1200]
    if not user_bit.strip() and not asst.strip():
        return
    material = f"USUARIO: {user_bit}\nAGENTE: {asst}"
    num_ctx = getattr(run, "num_ctx", None) or NUM_CTX_DEFAULT
    model = getattr(run, "model", "") or ""
    try:
        if LLM_BACKEND == "openai":
            resp = requests.post(
                f"{_chat_base()}/chat/completions",
                json={
                    "model": model,
                    "stream": False,
                    "max_tokens": 80,
                    "messages": [
                        {"role": "system", "content": _EXTRACT_SYSTEM},
                        {"role": "user", "content": material},
                    ],
                },
                timeout=(5, 45),
            )
            resp.raise_for_status()
            data = resp.json()
            out = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        else:
            resp = requests.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": model,
                    "prompt": material,
                    "system": _EXTRACT_SYSTEM,
                    "stream": False,
                    "options": {"num_ctx": min(int(num_ctx), 4096), "num_predict": 80, "num_gpu": 99},
                },
                timeout=(5, 45),
            )
            resp.raise_for_status()
            out = _ollama_ndjson_text(resp.text) or ""
    except Exception:
        return
    out = (out or "").strip().splitlines()[0].strip() if out else ""
    if not out or out.upper().startswith("NADA"):
        return
    add_memory(out, agente_id=agent_id or None)
