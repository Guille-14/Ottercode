from __future__ import annotations
from typing import Optional
from backend.home import cfg_get
from backend.plugins.memory.provider import MemoryProvider

_PROVIDERS = ("honcho", "mem0", "hindsight", "memgraph", "retaindb", "byterover", "supermemory")


def get_provider(name: str = "") -> Optional[MemoryProvider]:
    n = (name or cfg_get("memory", "provider", default=None) or "") or ""
    n = str(n).lower()
    if not n or n == "null":
        return None
    mod = __import__(f"backend.plugins.memory.{n}", fromlist=["Provider"])
    return getattr(mod, "Provider")()
