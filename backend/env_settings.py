"""Validación fail-fast de entorno (Pydantic v2; pydantic-settings si existe)."""
from __future__ import annotations

import os
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator


class OtterEnv(BaseModel):
    model_config = {"extra": "ignore"}

    llm_backend: Literal["ollama", "openai"] = "ollama"
    ollama: str = "http://127.0.0.1:11434"
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    model: str = "qwen2.5-coder:7b"
    num_ctx: int = Field(default=16384, ge=1024, le=131072)
    num_predict: int = Field(default=4096, ge=64, le=131072)
    keep_alive: str = "15m"
    ask_permissions: str = "1"
    sandbox_required: str = "1"
    sandbox_net: str = "0"
    native_tools: str = "auto"
    max_tool_steps: int = Field(default=60, ge=1, le=500)
    flush_every_turn: str = "0"
    token: Optional[str] = None
    memory_llm: str = "1"

    @field_validator("llm_backend", mode="before")
    @classmethod
    def _backend(cls, v):
        s = str(v or "ollama").strip().lower()
        if s not in ("ollama", "openai"):
            raise ValueError("OTTERCODE_LLM_BACKEND debe ser ollama u openai")
        return s


def load_otter_env() -> OtterEnv:
    mapping = {
        "llm_backend": "OTTERCODE_LLM_BACKEND",
        "ollama": "OTTERCODE_OLLAMA",
        "host": "OTTERCODE_HOST",
        "port": "OTTERCODE_PORT",
        "model": "OTTERCODE_MODEL",
        "num_ctx": "OTTERCODE_NUM_CTX",
        "num_predict": "OTTERCODE_NUM_PREDICT",
        "keep_alive": "OTTERCODE_KEEP_ALIVE",
        "ask_permissions": "OTTERCODE_ASK_PERMISSIONS",
        "sandbox_required": "OTTERCODE_SANDBOX_REQUIRED",
        "sandbox_net": "OTTERCODE_SANDBOX_NET",
        "native_tools": "OTTERCODE_NATIVE_TOOLS",
        "max_tool_steps": "OTTERCODE_MAX_TOOL_STEPS",
        "flush_every_turn": "OTTERCODE_FLUSH_EVERY_TURN",
        "token": "OTTERCODE_TOKEN",
        "memory_llm": "OTTERCODE_MEMORY_LLM",
    }
    data = {}
    for field, env in mapping.items():
        if env in os.environ and os.environ[env] != "":
            data[field] = os.environ[env]
    try:
        return OtterEnv(**data)
    except ValidationError as exc:
        raise SystemExit(f"Configuración inválida:\n{exc}") from exc
