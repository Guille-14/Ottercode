from __future__ import annotations

import json
from enum import Enum, auto
from typing import Any, Dict


# ---------------------------------------------------------------------------
# Rutas API centralizadas (evita strings mágicos dispersos)
# ---------------------------------------------------------------------------

class Route:
    INDEX = "/"
    FAVICON = "/favicon.ico"
    MOBILE = "/m"
    MOBILE_MANIFEST = "/m/manifest.webmanifest"
    MOBILE_SW = "/m/sw.js"
    MOBILE_ICON_192 = "/m/icon-192.png"
    MOBILE_ICON_512 = "/m/icon-512.png"
    HEALTHZ = "/api/healthz"
    STATUS = "/api/status"
    MODELS = "/api/models"
    PS = "/api/ps"
    FLUSH = "/api/flush"
    VERSION = "/api/version"
    MODELS_PULL = "/api/models/pull"
    MODELS_CREATE = "/api/models/create"
    MODELS_DELETE = "/api/models/delete"
    MODELS_COPY = "/api/models/copy"
    MODEL_SHOW = "/api/model/show"
    SKILLS = "/api/skills"
    SKILLS_CONFIG = "/api/skills/config"
    AGENTS = "/api/agents"
    AGENTS_CREATE = "/api/agents/create"
    TASK = "/api/task"
    TASK_ABORT = "/api/task/{task_id}/abort"
    SKILL = "/api/skill"
    TREE = "/api/tree"
    FILE = "/api/file"
    WORKSPACE = "/api/workspace"
    TASK_ZIP = "/api/task/{task_id}/zip"
    HISTORY = "/api/history"
    HISTORY_DETAIL = "/api/history/{task_id}"
    HISTORY_DELETE = "/api/history/{task_id}"
    HISTORY_RENAME = "/api/history/{task_id}/rename"
    ACTIVITY = "/api/activity"
    PULSE = "/api/pulse"
    SYSTEM = "/api/system"
    SETTINGS = "/api/settings"
    SETTINGS_RESET = "/api/settings/reset"
    SETTINGS_APPLY_PROFILE = "/api/settings/apply-profile"
    VAULT_STATUS = "/api/vault/status"
    VAULT_CONFIG = "/api/vault/config"
    VAULT_GRAPH = "/api/vault/graph"
    VAULT_NOTE = "/api/vault/note"
    IDENTITY_SOUL = "/api/identity/soul"
    IDENTITY_USER = "/api/identity/user"
    # FASE 3 · Perfiles
    PROFILES = "/api/profiles"
    PROFILES_ACTIVE = "/api/profiles/active"
    PROFILES_DETAIL = "/api/profiles/{name}"
    PROFILES_SAVE = "/api/profiles/save"
    # FASE 6 & 9 · Skills y Pruning
    HISTORY_PRUNE = "/api/history/{task_id}/prune"
    SKILLS_ENABLE = "/api/skills/enable"
    AUTH_TOKEN = "/api/auth/token"


class SseEvent(Enum):
    """Centralized SSE event names for OtterCode."""

    # Task events
    task_start = auto()
    task_done = auto()
    task_error = auto()
    task_aborted = auto()

    # Agent turn events
    agent_start = auto()
    agent_end = auto()
    vram_flush = auto()
    token = auto()
    system = auto()
    tool_call = auto()
    tool_result = auto()
    delegate = auto()
    agent_injected = auto()
    loop_iter = auto()
    loop_exhausted = auto()

    # Factory events
    agent_create_start = auto()
    agent_create_error = auto()
    agent_created = auto()

    # Model manager events (pull / create desde la UI)
    pull_progress = auto()
    pull_done = auto()
    create_progress = auto()
    create_done = auto()

    # Chat specific
    session_id = auto()

def sse(event: SseEvent, data: Dict[str, Any]) -> str:
    """Formatea un evento Server-Sent Events usando el enum centralizado."""
    return f"event: {event.name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"