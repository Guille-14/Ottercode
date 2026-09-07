"""Modelo router pequeño: clasificación rápida directo | agente | skill."""
from __future__ import annotations

import json
import os
import re
import threading
from typing import Any, Dict, List

import requests

from backend.config import LLM_BACKEND, OLLAMA_BASE_URL
from backend.md_skills import list_md_skills
from backend.ollama import _ollama_ndjson_text

ROUTER_MODEL = os.environ.get("OTTERCODE_ROUTER_MODEL", "qwen2.5:1.5b")
_GREET = re.compile(
    r"^(hola|hey|hi|hello|buenas|qué tal|que tal|buenos días|buenas tardes)"
    r"[\s!?.¡¿]*$",
    re.IGNORECASE,
)
_CODEY = re.compile(
    r"\b(código|codigo|implementa|crea|escribe|refactor|html|python|bug|"
    r"archivo|función|funcion|app|web|fix|debug)\b",
    re.IGNORECASE,
)

_CLASSIFY = (
    "Clasifica el mensaje del usuario. Responde SOLO un JSON: "
    '{"tipo":"directo"|"agente"|"skill","destino":"<id>"}. '
    "directo = saludo o pregunta trivial. "
    "skill = encaja con una skill listada. "
    "agente = código, misión o trabajo. destino=otter|developer|architect."
)


def _heuristic(mensaje: str) -> Dict[str, str]:
    t = (mensaje or "").strip()
    if not t:
        return {"tipo": "directo", "destino": "router"}
    if t.startswith("/"):
        name = t[1:].split()[0].strip().lower()
        for s in list_md_skills():
            if s["name"].lower() == name:
                return {"tipo": "skill", "destino": s["name"]}
        return {"tipo": "agente", "destino": "otter"}
    if len(t) < 80 and _GREET.search(t):
        return {"tipo": "directo", "destino": "router"}
    low = t.lower()
    best = ""
    for s in list_md_skills():
        if s["name"].lower() in low or (s.get("desc") or "")[:40].lower() in low:
            best = s["name"]
            break
    if best and not _CODEY.search(t):
        return {"tipo": "skill", "destino": best}
    if _CODEY.search(t) or len(t) > 120:
        return {"tipo": "agente", "destino": "developer"}
    return {"tipo": "agente", "destino": "otter"}


def route(mensaje: str) -> Dict[str, str]:
    """Decide directo | agente | skill. Llamada corta; fallback heurístico."""
    base = _heuristic(mensaje)
    if os.environ.get("OTTERCODE_ROUTER", "1") == "0":
        return base
    if os.environ.get("OTTERCODE_ROUTER_LLM", "1") == "0":
        return base
    if LLM_BACKEND != "ollama":
        return base
    skills = ", ".join(s["name"] for s in list_md_skills()[:24]) or "(ninguna)"
    prompt = f"Skills: {skills}\nMensaje: {(mensaje or '')[:400]}"
    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": ROUTER_MODEL,
                "prompt": prompt,
                "system": _CLASSIFY,
                "stream": False,
                "keep_alive": -1,
                "options": {"num_ctx": 512, "num_predict": 64, "temperature": 0},
            },
            timeout=(3, 20),
        )
        resp.raise_for_status()
        raw = _ollama_ndjson_text(resp.text) or ""
        m = re.search(r"\{[^{}]+\}", raw)
        if not m:
            return base
        obj = json.loads(m.group(0))
        tipo = str(obj.get("tipo") or "").lower()
        dest = str(obj.get("destino") or "").strip()
        if tipo not in ("directo", "agente", "skill"):
            return base
        return {"tipo": tipo, "destino": dest or base["destino"]}
    except Exception:
        return base


def preload_router() -> None:
    """Mantiene el modelo router residente (keep_alive -1). No bloquea arranque."""
    if os.environ.get("OTTERCODE_ROUTER", "1") == "0":
        return
    if LLM_BACKEND != "ollama":
        return

    def _go() -> None:
        try:
            requests.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={
                    "model": ROUTER_MODEL,
                    "prompt": "ok",
                    "stream": False,
                    "keep_alive": -1,
                    "options": {"num_ctx": 256, "num_predict": 1},
                },
                timeout=(5, 60),
            )
            print(f"[ottercode] router {ROUTER_MODEL} keep_alive=-1", flush=True)
        except Exception as exc:
            print(f"[ottercode] router no precargado: {exc}", flush=True)

    threading.Thread(target=_go, daemon=True).start()


def direct_reply(mensaje: str) -> str:
    """Respuesta corta del router para saludos (sin agente grande)."""
    if os.environ.get("OTTERCODE_ROUTER_LLM", "1") == "0" or LLM_BACKEND != "ollama":
        return "Hola. Dime la tarea y la balsa se pone en marcha."
    try:
        resp = requests.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": ROUTER_MODEL,
                "prompt": (mensaje or "")[:400],
                "system": "Eres OtterCode. Responde en español, 1-3 frases, sin código.",
                "stream": False,
                "keep_alive": -1,
                "options": {"num_ctx": 512, "num_predict": 120, "temperature": 0.4},
            },
            timeout=(5, 30),
        )
        resp.raise_for_status()
        return (_ollama_ndjson_text(resp.text) or "").strip() or (
            "Hola. ¿En qué te ayudo?"
        )
    except Exception:
        return "Hola. El router está listo; lanza una misión cuando quieras."


def is_router_model(name: str) -> bool:
    n = (name or "").strip()
    return bool(n) and (n == ROUTER_MODEL or n.startswith(ROUTER_MODEL.split(":")[0]))
