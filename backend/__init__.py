"""OtterCode — orquestador local de agentes sobre Ollama (motor modular)."""
from backend.main import app, _auth_ok
import requests
import backend.config as _cfg
import backend.agents as _agents
import backend.prompts as _prompts
import backend.ollama as _ollama
import backend.engine as _engine
import backend.runstate as _runstate
import backend.history as _history
import backend.vault as _vault
import backend.routes as _routes
import backend.db as _db
import backend.runtime as _runtime

def _reexport(*mods):
    g = globals()
    for m in mods:
        for k, v in m.__dict__.items():
            if k.startswith("__"):
                continue
            g[k] = v

_reexport(_cfg, _agents, _prompts, _ollama, _engine, _runstate, _history, _vault, _routes, _db, _runtime)

from backend.routes import TaskRequest  # noqa: F401
from backend.main import _auth_ok  # noqa: F401

__all__ = ["app"]
