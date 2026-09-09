# OtterCode — transporte LLM: sesiones, VRAM flush, streaming, errores
from __future__ import annotations
import io
import hmac
import json
import os
import queue
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple

import httpx
import requests
import tools
from fastapi import FastAPI, HTTPException, Request, APIRouter
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from backend.secrets_filter import redact_text
from events import SseEvent, sse, Route

from backend.config import (
    OLLAMA_BASE_URL, DEFAULT_MODEL, LLM_BACKEND, _ollama_session, _ollama_httpx,
    FLUSH_WAIT_SECONDS, NUM_CTX_DEFAULT, NUM_PREDICT_DEFAULT, KEEP_ALIVE_DEFAULT,
    native_tools_enabled, GENERATE_TIMEOUT
)
import tools  # noqa: E402
from backend.runstate import OtterRun  # noqa: E402
from backend.history import TOOL_CAPABLE_MODELS  # noqa: E402
from backend.config import FLUSH_WAIT_SECONDS, GENERATE_TIMEOUT, KEEP_ALIVE_DEFAULT, LLM_BACKEND, NUM_CTX_DEFAULT, NUM_PREDICT_DEFAULT, OLLAMA_BASE_URL, _ollama_httpx, _ollama_session, native_tools_enabled  # noqa: E402
from backend.agents import _chat_base  # noqa: E402
from backend.runstate import AbortRequested  # noqa: E402  (abortos en stream_llm)
import backend.settings as _otter_settings  # noqa: E402


class _LlmSession:
    """Sesión mínima para stream_llm fuera de una misión (la Fábrica)."""

    def __init__(self, model: str):
        self.model = model
        self.aborted = False


# ---------------------------------------------------------------------------
# REGLA DE ORO — VRAM FLUSH
# ---------------------------------------------------------------------------

def flush_vram(model: str) -> Dict[str, Any]:
    """Fuerza la descarga del modelo de la VRAM (keep_alive: 0) y espera 1 s.

    Se invoca ANTES de cada turno de agente (core o dinámico) y antes de que
    la Fábrica genere perfiles: libera el KV-cache de la GPU (RTX 5060 8GB)
    para que los contextos masivos se respalden en la RAM sin crashear la GPU.

    Transporte OpenAI-compat (MLC Chat / llama.cpp en modo OpenAI): no existe
    keep_alive equivalente → el paso de testigo se registra como no-op (el
    contexto vivo del móvil se gestiona solo en la RAM del dispositivo).
    """
    if LLM_BACKEND == "openai":
        return {
            "ok": True, "ms": 0,
            "detail": "OpenAI-compat: sin descarga de VRAM (no-op; el modelo vive en la RAM del móvil)",
        }
    flush_timeout = int(os.environ.get("OTTERCODE_FLUSH_TIMEOUT", "60"))
    started = time.time()
    try:
        resp = _ollama_session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={"model": model, "keep_alive": 0, "prompt": "", "stream": False},
            timeout=flush_timeout,
        )
        detail = _safe_detail(resp)
        ok = resp.status_code == 200
    except requests.RequestException as exc:
        return {
            "ok": False,
            "ms": int((time.time() - started) * 1000),
            "error": f"Ollama inaccesible en {OLLAMA_BASE_URL}: {exc}",
        }
    if ok:
        time.sleep(FLUSH_WAIT_SECONDS)  # ← la Regla de Oro exige esta pausa
    return {
        "ok": ok,
        "ms": int((time.time() - started) * 1000),
        "detail": detail if not ok else "KV-cache descargado de VRAM",
    }


def flush_all_vram() -> Dict[str, Any]:
    """Expulsa de la VRAM CUALQUIER modelo que esté cargado en ese momento.

    Consulta /api/ps, y para cada modelo cargado fuerza keep_alive:0 (en
    /api/generate y, si no responde, /api/chat). Devuelve la lista de modelos
    expulsados. Si /api/ps no se puede consultar, cae al flush del modelo por
    defecto (comportamiento anterior) para no dejar la GPU sin limpiar.
    """
    if LLM_BACKEND == "openai":
        return {
            "ok": True, "ms": 0, "expelled": [],
            "detail": "OpenAI-compat: sin descarga de VRAM (no-op)",
        }
    flush_timeout = int(os.environ.get("OTTERCODE_FLUSH_TIMEOUT", "60"))
    started = time.time()
    expelled: List[str] = []
    try:
        ps = _ollama_httpx.get("/api/ps", timeout=flush_timeout).json()
        models = [
            str(m.get("name", "")).strip()
            for m in ps.get("models", []) if isinstance(m, dict) and m.get("name")
        ]
    except Exception:
        models = []
    if not models:
        models = [DEFAULT_MODEL]  # fallback seguro
    ok = True
    errors: List[str] = []
    for model in models:
        try:
            resp = _ollama_session.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={"model": model, "keep_alive": 0, "prompt": "", "stream": False},
                timeout=flush_timeout,
            )
            if resp.status_code == 200:
                expelled.append(model)
            else:
                # segundo intento vía /api/chat
                resp2 = _ollama_session.post(
                    f"{OLLAMA_BASE_URL}/api/chat",
                    json={"model": model, "keep_alive": 0, "messages": [], "stream": False},
                    timeout=flush_timeout,
                )
                if resp2.status_code == 200:
                    expelled.append(model)
                else:
                    errors.append(f"{model}: HTTP {resp.status_code}")
        except requests.RequestException as exc:
            ok = False
            errors.append(f"{model}: {exc}")
    if ok:
        time.sleep(FLUSH_WAIT_SECONDS)  # Regla de Oro: pausa tras descargar
    return {
        "ok": ok or bool(expelled),
        "ms": int((time.time() - started) * 1000),
        "expelled": expelled,
        "detail": f"Descargados de VRAM: {', '.join(expelled) or 'ninguno'}",
        "errors": errors,
    }


def _safe_detail(resp: requests.Response) -> str:
    try:
        payload = resp.json()
        if isinstance(payload, dict):
            return str(payload.get("error") or json.dumps(payload, ensure_ascii=False))[:300]
        return str(payload)[:300]
    except Exception:  # noqa: BLE001
        return (resp.text or f"HTTP {resp.status_code}")[:300]


# P4.2 · Caché de model list (TTL 30s)
_models_cache: Optional[Tuple[float, List[str]]] = None

def fetch_models() -> Optional[List[str]]:
    """Lista los modelos disponibles (Ollama /api/tags o OpenAI /v1/models;
    None si el servicio está caído). Con caché configurable (TTL env)."""
    global _models_cache
    models_ttl = int(os.environ.get("OTTERCODE_MODELS_TTL", "30"))
    now = time.time()
    if _models_cache and (now - _models_cache[0]) < models_ttl:
        return _models_cache[1]
    try:
        if LLM_BACKEND == "openai":
            resp = _ollama_httpx.get(f"{_chat_base()}/models")
            resp.raise_for_status()
            payload = resp.json()
            result = [m["id"] for m in payload.get("data", []) if m.get("id")]
        else:
            resp = _ollama_httpx.get("/api/tags")
            resp.raise_for_status()
            payload = resp.json()
            result = [m["name"] for m in payload.get("models", []) if m.get("name")]
        _models_cache = (now, result)
        return result
    except (httpx.HTTPError, ValueError):
        _models_cache = None  # no cachear estado caído: re-intentar en el próximo tick
        return None


def invalidate_models_cache() -> None:
    """Invalida la caché de modelos (tras pull/create/delete/copy)."""
    global _models_cache
    _models_cache = None


_VISION_MARKERS = (
    "llava", "llama3.2-vision", "llama3.2-vision", "qwen2-vl", "qwen2.5vl",
    "qwen2.5-vl", "gemma3", "minicpm-v", "minicpmv", "moondream", "granite-vision",
    "vision",
)


def model_supports_vision(model: str, show: Optional[Dict[str, Any]] = None) -> bool:
    name = (model or "").lower()
    if any(m in name for m in _VISION_MARKERS):
        return True
    if not isinstance(show, dict):
        return False
    caps = show.get("capabilities") or (show.get("details") or {}).get("capabilities") or []
    blob = " ".join(str(c).lower() for c in (caps if isinstance(caps, list) else [caps]))
    fam = str((show.get("family") or (show.get("details") or {}).get("family") or "")).lower()
    return "vision" in blob or any(m in fam for m in _VISION_MARKERS)


def parse_context_length(show: Dict[str, Any]) -> int:
    """num_ctx máximo declarado por /api/show (model_info / parameters)."""
    info = show.get("model_info") or show.get("modelinfo") or {}
    if isinstance(info, dict):
        for k, v in info.items():
            if "context_length" in str(k).lower() or str(k).endswith(".context_length"):
                try:
                    n = int(v)
                    if n >= 512:
                        return n
                except (TypeError, ValueError):
                    pass
    params = str(show.get("parameters") or "")
    m = re.search(r"num_ctx\s+(\d+)", params, re.I)
    if m:
        return max(512, int(m.group(1)))
    mf = str(show.get("modelfile") or "")
    m2 = re.search(r"PARAMETER\s+num_ctx\s+(\d+)", mf, re.I)
    if m2:
        return max(512, int(m2.group(1)))
    return 0


def strip_image_b64(raw: str) -> str:
    s = (raw or "").strip()
    if "," in s and s.lower().startswith("data:"):
        s = s.split(",", 1)[1]
    return re.sub(r"\\s+", "", s)


def _with_images(messages: List[Dict[str, Any]], images: List[str]) -> List[Dict[str, Any]]:
    if not images:
        return messages
    out = [dict(m) for m in messages]
    for m in reversed(out):
        if (m.get("role") or "") == "user":
            m["images"] = images
            break
    else:
        out.append({"role": "user", "content": "(imagen)", "images": images})
    return out


def resolve_coder_model(requested: str = "") -> str:
    """Si el modelo pedido no está en /api/tags, usa DEFAULT_MODEL o el primero."""
    want = (requested or DEFAULT_MODEL or "").strip()
    tags = fetch_models() or []
    if want and want in tags:
        return want
    if want:
        base = want.split(":")[0]
        for t in tags:
            if t == want or t.startswith(base):
                return t
    if DEFAULT_MODEL in tags:
        return DEFAULT_MODEL
    return tags[0] if tags else want or DEFAULT_MODEL


# ---------------------------------------------------------------------------
# Errores amigables + stream de Ollama
# ---------------------------------------------------------------------------

class TruncatedToolCall(RuntimeError):
    """llama-server cortó el JSON de arguments de un tool_call nativo."""

    def __init__(self, tool: str, detail: str = ""):
        self.tool = tool or "tool"
        super().__init__(detail or f"JSON truncado en {self.tool}")


def _truncated_tool_name(err: str) -> Optional[str]:
    e = err or ""
    m = re.search(r'invalid tool call arguments for "([^"]+)"', e, re.I)
    if m:
        return m.group(1)
    low = e.lower()
    if "unexpected end of json" in low and "tool call" in low:
        return "write_file"
    return None


class ContextOverflow(RuntimeError):
    """Ollama rechazó la llamada por ventana de contexto llena."""


def _is_context_overflow(text: str) -> bool:
    t = (text or "").lower()
    return any(k in t for k in (
        "context length", "context window", "too many tokens",
        "n_keep", "exceeds context", "maximum context",
        "prompt is too long", "num_ctx",
        "exceed_context", "available context", "n_prompt_tokens",
        "n_ctx", "context size",
    ))


def _friendly_ollama_error(exc: Exception) -> str:
    msg = str(exc)
    name = type(exc).__name__
    if "Connection" in name or "Connection refused" in msg:
        return (
            f"Ollama no responde en {OLLAMA_BASE_URL}. "
            f"¿Está corriendo? (arranca con: ollama serve)"
        )
    if "Timeout" in name:
        return (
            "Ollama tardó demasiado en responder. El modelo puede estar cargando a "
            "VRAM por primera vez; reintenta la misión."
        )
    low = msg.lower()
    if "out of memory" in low or ("cuda" in low and "memory" in low) or "vram" in low:
        return (
            "La GPU se ha quedado sin VRAM. Prueba un modelo más pequeño o "
            "reduce num_ctx (Ajustes)."
        )
    return msg


def _with_model_hint(detail: str, model: str) -> str:
    if "not found" in detail.lower():
        return f"{detail} → solución: `ollama pull {model}`"
    return detail


def ensure_gpu_exclusive(keep_model: str) -> None:
    """Deja SOLO `keep_model` en VRAM. El resto (router, residuos) a keep_alive 0.

    Dos modelos a la vez en 8 GB empujan capas a CPU/RAM. El usuario pide GPU
    siempre: un ocupante, todas las capas en GPU (num_gpu=99 en options).
    """
    if LLM_BACKEND != "ollama" or not keep_model:
        return
    try:
        ps = _ollama_httpx.get("/api/ps", timeout=5).json()
        loaded = [
            str(m.get("name", "")).strip()
            for m in (ps.get("models") or [])
            if isinstance(m, dict) and m.get("name")
        ]
    except Exception:
        return
    for name in loaded:
        if not name or name == keep_model:
            continue
        try:
            _ollama_session.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={"model": name, "keep_alive": 0, "prompt": "", "stream": False},
                timeout=30,
            )
        except requests.RequestException:
            pass


def stream_llm(
    run: Any, agent_id: str, system_prompt: str, prompt: str
) -> Iterator[str]:
    """Stream del modelo. Yields eventos SSE 'token'; devuelve (texto, stats).

    Soporta dos transportes:
      - "ollama"  : POST /api/generate (stream NDJSON) — Ollama, o llama.cpp
                    (llama-server) en su modo API-Ollama sobre el móvil.
      - "openai"  : POST /v1/chat/completions (stream SSE) — MLC Chat,
                    llama.cpp en modo OpenAI, LM Studio…

    Robustez: si la conexión cae ANTES del primer token, reintenta UNA vez
    (nunca a mitad de stream, para no duplicar tokens en la UI).
    `run` solo aporta .model y .aborted (OtterRun o _LlmSession).
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            ensure_gpu_exclusive(getattr(run, "model", "") or "")
        except Exception:
            pass
        # v6.0 · el transporte puede conmutarse a mitad de misión (fallback)
        url, payload = _llm_request(run, system_prompt, prompt, agent_id=agent_id)
        _is_chat = "/api/chat" in url
        collected: List[str] = []
        stats: Dict[str, Any] = {}
        emitted = False
        _chat_tool_calls: List[Any] = []
        try:
            with _ollama_session.post(
                url, json=payload, stream=True, timeout=GENERATE_TIMEOUT,
            ) as resp:
                # Abort agresivo: api_abort cierra este socket y el stream muere
                # al instante (antes el abort esperaba al timeout de 300 s).
                try:
                    run._active_resp = resp
                except AttributeError:
                    pass
                if resp.status_code != 200:
                    _detail = _safe_detail(resp)
                    # v6.0 · Fase 1: servidor sin /api/chat → fallback generate
                    if (resp.status_code == 404
                            and _is_chat and LLM_BACKEND == "ollama"
                            and getattr(run, "_transport", "chat") == "chat"):
                        run._transport = "generate"
                        yield sse(SseEvent.system, {
                            "text": "ℹ️ Este servidor no soporta /api/chat: "
                                    "cambiando a /api/generate (compatibilidad)."
                        })
                        continue
                    if _is_context_overflow(_detail):
                        raise ContextOverflow(_detail)
                    trunc = _truncated_tool_name(_detail)
                    if trunc:
                        stats["_truncated_tool"] = trunc
                        stats["_truncated_err"] = _detail[:400]
                        return "".join(collected), stats
                    raise RuntimeError(
                        _with_model_hint(
                            f"El modelo devolvió HTTP {resp.status_code}: {_detail}",
                            run.model,
                        )
                    )
                for raw in resp.iter_lines():
                    if run.aborted:
                        # v4.4 · conserva lo generado antes del aborto
                        try:
                            run._partial_text = "".join(collected)
                        except AttributeError:
                            pass
                        raise AbortRequested()
                    if not raw:
                        continue
                    text_line = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else raw
                    if LLM_BACKEND == "openai":
                        line = text_line.strip()
                        if not line.startswith("data:"):
                            continue
                        data = line[5:].strip()
                        if data == "[DONE]":
                            stats = {"tokens": None, "seconds": 0.0}
                            break
                        try:
                            chunk = json.loads(data)
                        except json.JSONDecodeError:
                            continue
                        if chunk.get("error"):
                            raise RuntimeError(
                                _with_model_hint(f"Modelo: {chunk['error']}", run.model))
                        choices = chunk.get("choices") or [{}]
                        token = (choices[0].get("delta") or {}).get("content") or ""
                        if token:
                            collected.append(token)
                            emitted = True
                            yield sse(SseEvent.token, {"agent": agent_id, "token": redact_text(token)})
                        continue
                    try:
                        data = json.loads(text_line)
                    except json.JSONDecodeError:
                        continue
                    if data.get("error"):
                        err = str(data["error"])
                        if _is_context_overflow(err):
                            raise ContextOverflow(err)
                        trunc = _truncated_tool_name(err)
                        if trunc:
                            stats["_truncated_tool"] = trunc
                            stats["_truncated_err"] = err[:400]
                            break
                        raise RuntimeError(_with_model_hint(f"Ollama: {err}", run.model))
                    if data.get("done"):
                        stats = {
                            "tokens": data.get("eval_count"),
                            "seconds": round((data.get("eval_duration") or 0) / 1e9, 2),
                        }
                        # v6.0 · los tool_calls nativos llegan en frames INTERMEDIOS
                        # (Gemma/otros), NO en el frame final "done" → se acumulan.
                        msg = data.get("message") or {}
                        tc = msg.get("tool_calls") or []
                        if tc:
                            _chat_tool_calls.extend(tc)
                        if _chat_tool_calls:
                            stats["message"] = {"role": "assistant", "content": "",
                                                "tool_calls": _chat_tool_calls}
                        elif msg:
                            stats["message"] = msg
                        break
                    msg = data.get("message") or {}
                    if _is_chat and msg.get("tool_calls"):
                        _chat_tool_calls.extend(msg["tool_calls"])
                        continue
                    token = msg.get("content", "") if _is_chat else data.get("response", "")
                    if token:
                        collected.append(token)
                        emitted = True
                        yield sse(SseEvent.token, {"agent": agent_id, "token": redact_text(token)})
            if getattr(run, "_active_resp", None) is resp:
                run._active_resp = None
            return "".join(collected), stats
        except (AttributeError, requests.exceptions.ChunkedEncodingError) as exc:
            # v3.4 · carrera del ABORT AGRESIVO: api_abort cerró el socket
            # mientras el worker leía ('NoneType' has no 'read', chunk roto…).
            # Con abort solicitado es un final LIMPIO, nunca un task_error.
            if run.aborted:
                # v4.4 · conserva lo generado antes del aborto
                try:
                    run._partial_text = "".join(collected)
                except AttributeError:
                    pass
                raise AbortRequested()
            raise RuntimeError(_friendly_ollama_error(exc))
        except (requests.ConnectionError, requests.ConnectTimeout,
                requests.ReadTimeout) as exc:
            if run.aborted:
                # v4.4 · conserva lo generado antes del aborto
                try:
                    run._partial_text = "".join(collected)
                except AttributeError:
                    pass
                raise AbortRequested()
            max_tries = int(os.environ.get("OTTERCODE_OLLAMA_RETRIES", "3"))
            if emitted or attempt >= max_tries:
                raise RuntimeError(_friendly_ollama_error(exc))
            wait = min(32, 2 ** attempt)
            print(f"[ottercode] Ollama retry {attempt}/{max_tries} wait={wait}s: {exc}",
                  flush=True)
            yield sse(SseEvent.system, {
                "text": f"⏳ Ollama caída/timeout (intento {attempt}/{max_tries}); "
                        f"reintento en {wait}s…"
            })
            time.sleep(wait)


def _llm_request(run: Any, system_prompt: str, prompt: str, agent_id: str = "") -> Tuple[str, Dict[str, Any]]:
    """(url, payload) según el transporte configurado.

    v6.0 · Fase 1: transporte primario /api/chat con messages[] por roles
    (memoria conversacional nativa); fallback /api/generate para servidores
    que no lo soporten (run._transport = "generate" lo conmuta stream_llm).
    Envía SIEMPRE options (num_ctx/num_predict) y keep_alive.
    """
    num_ctx = getattr(run, "num_ctx", None) or NUM_CTX_DEFAULT
    keep = os.environ.get("OTTERCODE_KEEP_ALIVE", KEEP_ALIVE_DEFAULT)
    transport = getattr(run, "_transport", "chat")
    # FASE 3 · resolver temperature/top_p del run (viene del perfil)
    _temp = getattr(run, "temperature", None)
    if _temp is None:
        _temp = 0.2
    _top = getattr(run, "top_p", None) or 0.9
    _want_tools = native_tools_enabled(getattr(run, "model", "") or "") and agent_id != "reviewer"
    _allowed = getattr(run, "_native_allowed", None)
    _tools = tools.get_ollama_tools(_allowed) if _want_tools else None
    if LLM_BACKEND == "openai" or transport == "openai":
        _oa_msgs: List[Dict[str, str]] = []
        if system_prompt:
            _oa_msgs.append({"role": "system", "content": system_prompt})
        _hist = getattr(run, "messages", None)
        if _hist:
            _oa_msgs.extend(list(_hist))
        else:
            _oa_msgs.append({"role": "user", "content": prompt})
        oa = {
            "model": run.model,
            "stream": True,
            "max_tokens": NUM_PREDICT_DEFAULT,
            "temperature": _temp,
            "top_p": _top,
            "messages": _oa_msgs,
        }
        if _tools:
            oa["tools"] = _tools
        return (f"{_chat_base()}/chat/completions", oa)
    if transport == "generate":
        _prompt = _flatten_messages(getattr(run, "messages", None)) or prompt
        return (
            f"{OLLAMA_BASE_URL}/api/generate",
            {
                "model": run.model, "prompt": _prompt, "system": system_prompt,
                "stream": True,
                "keep_alive": keep,
                "options": _otter_settings.build_options(run),
            },
        )
    _messages: List[Dict[str, Any]] = []
    if system_prompt:
        _messages.append({"role": "system", "content": system_prompt})
    _hist = getattr(run, "messages", None)
    if _hist:
        _messages.extend(list(_hist))
    else:
        _messages.append({"role": "user", "content": prompt})
    if imgs:
        _messages = _with_images(_messages, imgs)
    payload = {
        "model": run.model, "messages": _messages, "stream": True,
        "keep_alive": keep,
        "options": _otter_settings.build_options(run),
    }
    if _tools:
        payload["tools"] = _tools
    return (f"{OLLAMA_BASE_URL}/api/chat", payload)


def _estimate_tokens(text: str) -> int:
    """Estimación ligera de tokens (~4 chars/token, cero dependencias)."""
    return max(1, len(text or "") // 4)


def _flatten_messages(messages: Optional[List[Dict[str, str]]]) -> str:
    """Aplana la cola de mensajes a un prompt plano (fallback /api/generate)."""
    if not messages:
        return ""
    return "\n\n".join(
        f"[{(m.get('role') or 'user').upper()}]\n{m.get('content', '')}"
        for m in messages)


# ---------------------------------------------------------------------------
def _ollama_ndjson_text(raw: str) -> Optional[str]:
    """Extrae el texto completo de una respuesta Ollama.

    Acepta JSON puro (stream:false real) y NDJSON por trozos
    (mock, llama.cpp modo Ollama): acumula TODOS los fragmentos
    'response' en orden. Devuelve None si no hay nada parseable."""
    raw = (raw or "").strip()
    if not raw:
        return None
    try:                                    # JSON único completo
        data = json.loads(raw)
        if isinstance(data, dict) and "response" in data:
            return str(data.get("response") or "") or None
    except json.JSONDecodeError:
        pass
    pieces: List[str] = []
    for line in raw.splitlines():           # NDJSON: acumula fragmentos
        line = line.strip()
        if not line:
            continue
        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            piece = data.get("response")
            if piece:
                pieces.append(str(piece))
    return "".join(pieces) or None
