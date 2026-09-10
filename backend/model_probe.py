"""Sonda nativa POST /api/show: contexto y visión reales del modelo."""
from __future__ import annotations

import re
import threading
import time
from typing import Any, Dict, Optional, Tuple

from backend.config import OLLAMA_BASE_URL, _ollama_session

_TTL = 300.0
_LOCK = threading.Lock()
_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}


def invalidate_probe(model: str = "") -> None:
    with _LOCK:
        if model:
            _CACHE.pop(model, None)
        else:
            _CACHE.clear()


def show_model(model_name: str, *, force: bool = False) -> Dict[str, Any]:
    name = (model_name or "").strip()
    if not name:
        return {}
    now = time.time()
    with _LOCK:
        hit = _CACHE.get(name)
        if not force and hit and (now - hit[0]) < _TTL:
            return hit[1]
    try:
        resp = _ollama_session.post(
            f"{OLLAMA_BASE_URL}/api/show",
            json={"model": name},
            timeout=(5, 60),
        )
        resp.raise_for_status()
        data = resp.json() if resp.content else {}
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}
    with _LOCK:
        _CACHE[name] = (time.time(), data)
    return data


def get_model_context(model_name: str, show: Optional[Dict[str, Any]] = None) -> Optional[int]:
    """Prioridad: parameters `num_ctx N` (Modelfile) → model_info *.context_length."""
    data = show if isinstance(show, dict) else show_model(model_name)
    params = str(data.get("parameters") or "")
    m = re.search(r"(?m)^\s*num_ctx\s+(\d+)\s*$", params, re.I)
    if not m:
        m = re.search(r"\bnum_ctx\s+(\d+)\b", params, re.I)
    if m:
        n = int(m.group(1))
        return n if n >= 512 else None
    mf = str(data.get("modelfile") or "")
    m2 = re.search(r"(?i)PARAMETER\s+num_ctx\s+(\d+)", mf)
    if m2:
        n = int(m2.group(1))
        return n if n >= 512 else None
    info = data.get("model_info") or data.get("modelinfo") or {}
    if isinstance(info, dict):
        for k, v in info.items():
            if str(k).endswith(".context_length") or str(k).lower().endswith("context_length"):
                try:
                    n = int(v)
                except (TypeError, ValueError):
                    continue
                if n >= 512:
                    return n
    return None


def get_model_vision(model_name: str, show: Optional[Dict[str, Any]] = None) -> bool:
    """Prioridad: capabilities contiene vision → model_info *vision.block_count*."""
    data = show if isinstance(show, dict) else show_model(model_name)
    caps = data.get("capabilities")
    if isinstance(caps, list) and any(str(c).lower() == "vision" for c in caps):
        return True
    if isinstance(caps, str) and "vision" in caps.lower():
        return True
    info = data.get("model_info") or data.get("modelinfo") or {}
    if isinstance(info, dict):
        for k in info:
            if "vision.block_count" in str(k).lower():
                try:
                    return int(info[k] or 0) > 0
                except (TypeError, ValueError):
                    return True
    return False


def probe(model_name: str) -> Dict[str, Any]:
    data = show_model(model_name)
    ctx = get_model_context(model_name, data)
    vis = get_model_vision(model_name, data)
    warn = ""
    suggest = None
    try:
        import backend.settings as _s
        from backend.config import VRAM_TOTAL_BYTES, _ollama_httpx
        used = 0
        try:
            ps = _ollama_httpx.get("/api/ps").json()
            used = sum(int(m.get("size_vram") or 0) for m in (ps.get("models") or []) if isinstance(m, dict))
        except Exception:
            used = 0
        free = max(0, int(VRAM_TOTAL_BYTES) - used)
        size_b = int(data.get("size") or 0)
        fit = _s.suggest_num_ctx(model_name, context_max=int(ctx or 0), size_bytes=size_b, vram_free=free)
        suggest = fit.get("num_ctx")
        warn = str(fit.get("warn") or "")
    except Exception:
        pass
    return {
        "ok": bool(data),
        "model": model_name,
        "context_length": ctx,
        "vision": vis,
        "family": (data.get("details") or {}).get("family"),
        "parameter_size": (data.get("details") or {}).get("parameter_size"),
        "quantization_level": (data.get("details") or {}).get("quantization_level"),
        "suggested_num_ctx": suggest,
        "vram_warn": warn,
    }
