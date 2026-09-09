"""Rutas y config unificada en ~/.ottercode/ (paridad Hermes)."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any, Dict

HOME = Path(os.environ.get("OTTERCODE_HOME", str(Path.home() / ".ottercode"))).expanduser()

DEFAULT_CONFIG: Dict[str, Any] = {
    "model": {
        "default": os.environ.get("OTTERCODE_MODEL", "qwen2.5-coder:7b"),
        "provider": "ollama",
        "base_url": os.environ.get("OTTERCODE_OLLAMA", "http://127.0.0.1:11434"),
        "context_length": 32768,
        "num_predict": 4096,
        "keep_alive": "15m",
    },
    "memory": {
        "memory_enabled": True,
        "user_profile_enabled": True,
        "memory_char_limit": 2200,
        "user_char_limit": 1375,
        "write_approval": False,
        "provider": None,
    },
    "skills": {"write_approval": False, "creation_nudge_interval": "medium"},
    "auxiliary": {
        "background_review": {
            "enabled": True,
            "defer": "auto",
            "defer_max_age_s": 1800,
            "provider": "ollama",
            "model": "auto",
        }
    },
    "cron": {
        "enabled": True,
        "preflight": True,
        "model_drift_guard": True,
        "allow_agent_scheduling": False,
        "max_parallel_jobs": 4,
        "misfire_grace_minutes": 10,
    },
    "terminal": {"backend": "local", "sandbox_required": True},
    "voice": {
        "stt_backend": "whisper_local",
        "tts_backend": "edge_tts",
        "tts_voice": "es-ES-ElviraNeural",
    },
    "display": {"memory_notifications": "on"},
    "agent": {"reasoning_effort": "medium", "disabled_toolsets": []},
    "providers": {
        "ollama": {"base_url": os.environ.get("OTTERCODE_OLLAMA", "http://127.0.0.1:11434"), "credentials": []},
        "fallback_providers": [],
    },
}

_lock = threading.Lock()
_cfg: Dict[str, Any] | None = None


def ensure_home() -> Path:
    for sub in (
        "skills", "memories", "cron", "cron/output", "scripts", "bots",
        "plugins/memory", "artifacts", "pending",
    ):
        (HOME / sub).mkdir(parents=True, exist_ok=True)
    cfg = HOME / "config.yaml"
    if not cfg.exists():
        cfg.write_text(_dump_yaml(DEFAULT_CONFIG), encoding="utf-8")
    return HOME


def _dump_yaml(obj: Any, indent: int = 0) -> str:
    sp = "  " * indent
    if isinstance(obj, dict):
        lines = []
        for k, v in obj.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{sp}{k}:")
                lines.append(_dump_yaml(v, indent + 1).rstrip())
            elif v is None:
                lines.append(f"{sp}{k}: null")
            elif isinstance(v, bool):
                lines.append(f"{sp}{k}: {'true' if v else 'false'}")
            elif isinstance(v, (int, float)):
                lines.append(f"{sp}{k}: {v}")
            else:
                s = str(v).replace("\\", "\\\\").replace('"', '\\"')
                lines.append(f'{sp}{k}: "{s}"')
        return "\n".join(lines) + "\n"
    if isinstance(obj, list):
        if not obj:
            return f"{sp}[]\n"
        lines = []
        for it in obj:
            if isinstance(it, dict):
                lines.append(f"{sp}-")
                inner = _dump_yaml(it, indent + 1)
                lines.append(inner.rstrip())
            else:
                lines.append(f"{sp}- {json.dumps(it, ensure_ascii=False)}")
        return "\n".join(lines) + "\n"
    return f"{sp}{obj}\n"


def _parse_scalar(s: str) -> Any:
    t = s.strip()
    if t in ("null", "~", ""):
        return None
    if t in ("true", "True"):
        return True
    if t in ("false", "False"):
        return False
    if t.startswith('"') and t.endswith('"'):
        return t[1:-1].replace('\\"', '"')
    if t.startswith("'") and t.endswith("'"):
        return t[1:-1]
    try:
        if "." in t:
            return float(t)
        return int(t)
    except ValueError:
        return t


def _load_simple_yaml(text: str) -> Dict[str, Any]:
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(text) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        pass
    root: Dict[str, Any] = {}
    stack: list[tuple[int, Any]] = [(-1, root)]
    pending_list_key: str | None = None
    for raw in text.splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        line = raw.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if line.startswith("- "):
            if isinstance(parent, list):
                parent.append(_parse_scalar(line[2:]))
            continue
        if ":" not in line:
            continue
        k, rest = line.split(":", 1)
        k = k.strip()
        rest = rest.strip()
        if rest == "":
            child: Any = {}
            if isinstance(parent, dict):
                parent[k] = child
            stack.append((indent, child))
        else:
            if isinstance(parent, dict):
                parent[k] = _parse_scalar(rest)
    return root


def load_config(force: bool = False) -> Dict[str, Any]:
    global _cfg
    with _lock:
        if _cfg is not None and not force:
            return _cfg
        ensure_home()
        path = HOME / "config.yaml"
        try:
            data = _load_simple_yaml(path.read_text(encoding="utf-8"))
        except OSError:
            data = {}
        merged = json.loads(json.dumps(DEFAULT_CONFIG))
        _deep_merge(merged, data if isinstance(data, dict) else {})
        _cfg = merged
        return merged


def _deep_merge(base: Dict[str, Any], over: Dict[str, Any]) -> None:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _deep_merge(base[k], v)
        else:
            base[k] = v


def cfg_get(*keys: str, default: Any = None) -> Any:
    cur: Any = load_config()
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def save_config(changes: Dict[str, Any]) -> Dict[str, Any]:
    cur = load_config(force=True)
    _deep_merge(cur, changes)
    (HOME / "config.yaml").write_text(_dump_yaml(cur), encoding="utf-8")
    return load_config(force=True)
