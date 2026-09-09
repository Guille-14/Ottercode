from __future__ import annotations
from typing import Any, Dict
from backend.environments.base import TerminalBackend


class VercelBackend(TerminalBackend):
    def exec(self, cmd: str, timeout: int = 120) -> Dict[str, Any]:
        return {"ok": False, "stdout": "", "stderr": "vercel sandbox: configura VERCEL_TOKEN", "code": 1}

    def read_file(self, path: str) -> str:
        return ""

    def write_file(self, path: str, content: str) -> str:
        return path

    def list_dir(self, path: str) -> str:
        return ""
