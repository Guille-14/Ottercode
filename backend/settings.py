# OtterCode — parámetros de runtime de Ollama (nueva pestaña Ajustes).
# Persisten en workspace/ollama_runtime.json (hot-reload sin reiniciar) y
# alimentan build_options() que fusiona, por precedencia:
#   run/perfil (temperature/top_p/num_ctx)  >  ajustes globales  >  env config
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from backend.config import NUM_CTX_DEFAULT, NUM_PREDICT_DEFAULT, WORKSPACE_ROOT

SETTINGS_PATH = WORKSPACE_ROOT / "ollama_runtime.json"

# Valores por defecto (iguales a los que el engine usa hoy).
SETTINGS_DEFAULTS: Dict[str, Any] = {
    # Generación
    "temperature": 0.7,
    "top_p": 0.9,
    "top_k": 40,
    "min_p": 0.0,
    "tfs_z": 1.0,
    "typical_p": 1.0,
    "seed": -1,                       # -1 = aleatorio
    "num_predict": NUM_PREDICT_DEFAULT,
    "num_keep": 0,
    "stop": [],
    # Contexto
    "num_ctx": NUM_CTX_DEFAULT,
    # Penalización
    "repeat_penalty": 1.1,
    "repeat_last_n": 64,
    "presence_penalty": 0.0,
    "frequency_penalty": 0.0,
    "penalize_newline": True,
    # Mirostat
    "mirostat": 0,                    # 0=off 1= 2=
    "mirostat_eta": 0.1,
    "mirostat_tau": 5.0,
}

_SETTINGS: Dict[str, Any] = {}


def _load_from_env() -> Dict[str, Any]:
    """Aplica los env de config como base inferior (num_ctx/num_predict)."""
    env: Dict[str, Any] = {}
    if os.environ.get("OTTERCODE_NUM_CTX"):
        env["num_ctx"] = int(os.environ["OTTERCODE_NUM_CTX"])
    if os.environ.get("OTTERCODE_NUM_PREDICT"):
        env["num_predict"] = int(os.environ["OTTERCODE_NUM_PREDICT"])
    return env


def load_runtime_settings() -> Dict[str, Any]:
    """Carga los ajustes persistidos (o defaults). Fuerza el archivo si existe."""
    global _SETTINGS
    if _SETTINGS:
        return _SETTINGS
    merged: Dict[str, Any] = dict(SETTINGS_DEFAULTS)
    merged.update(_load_from_env())
    try:
        if SETTINGS_PATH.exists():
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                for k, v in data.items():
                    if k in merged or k in ("ctx_speed_floor",):
                        merged[k] = v
    except (json.JSONDecodeError, OSError):
        pass
    _SETTINGS = merged
    return _SETTINGS


def save_runtime_settings(changes: Dict[str, Any]) -> Dict[str, Any]:
    """Persiste un merge de ajustes validado y recarga en memoria. Hot-reload."""
    current = load_runtime_settings()
    for k, v in changes.items():
        if k in current:
            current[k] = v
    try:
        SETTINGS_PATH.write_text(
            json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except OSError:
        raise
    _SETTINGS = current
    return current


def reset_runtime_settings() -> Dict[str, Any]:
    """Restablece a defaults y borra el archivo si existe."""
    global _SETTINGS
    _SETTINGS = dict(SETTINGS_DEFAULTS)
    _SETTINGS.update(_load_from_env())
    try:
        if SETTINGS_PATH.exists():
            SETTINGS_PATH.unlink()
    except OSError:
        pass
    return _SETTINGS


def _solo_range(v: Any, lo: float, hi: float, dft: float) -> Any:
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return dft
    return max(lo, min(hi, fv))


def build_options(run: Any) -> Dict[str, Any]:
    """Dict `options` de Ollama a partir de los ajustes globales, con override
    de run/perfil para temperature/top_p/num_ctx (precedencia mayor)."""
    s = load_runtime_settings()
    num_ctx = getattr(run, "num_ctx", None) or s.get("num_ctx") or NUM_CTX_DEFAULT
    # SIEMPRE GPU: 99 capas = todas las que tenga el modelo. 0 = CPU y
    # no se permite (es lo que tiraba tok/s a ~10 con offload a RAM).
    opts: Dict[str, Any] = {
        "num_gpu": 99,
        "main_gpu": 0,
        "num_ctx": int(num_ctx),
        "num_predict": int(s.get("num_predict", NUM_PREDICT_DEFAULT)),
        "temperature": _solo_range(
            getattr(run, "temperature", None) if getattr(run, "temperature", None) is not None else s.get("temperature"), 0.0, 2.0, 0.2),
        "top_p": _solo_range(
            getattr(run, "top_p", None) if getattr(run, "top_p", None) is not None else s.get("top_p"), 0.0, 1.0, 0.9),
        "top_k": int(s.get("top_k", 40)),
        "min_p": _solo_range(s.get("min_p"), 0.0, 1.0, 0.0),
        "tfs_z": _solo_range(s.get("tfs_z"), 0.0, 2.0, 1.0),
        "typical_p": _solo_range(s.get("typical_p"), 0.0, 1.0, 1.0),
        "repeat_penalty": _solo_range(s.get("repeat_penalty"), 0.5, 2.0, 1.1),
        "repeat_last_n": int(s.get("repeat_last_n", 64)),
        "presence_penalty": _solo_range(s.get("presence_penalty"), -2.0, 2.0, 0.0),
        "frequency_penalty": _solo_range(s.get("frequency_penalty"), -2.0, 2.0, 0.0),
        "penalize_newline": bool(s.get("penalize_newline", True)),
        "mirostat": int(s.get("mirostat", 0)),
        "mirostat_eta": _solo_range(s.get("mirostat_eta"), 0.0, 1.0, 0.1),
        "mirostat_tau": _solo_range(s.get("mirostat_tau"), 0.0, 10.0, 5.0),
    }
    seed = s.get("seed", -1)
    if int(seed) >= 0:
        opts["seed"] = int(seed)
    keep = s.get("num_keep", 0)
    if int(keep) > 0:
        opts["num_keep"] = int(keep)
    return opts
