from __future__ import annotations
import subprocess
from pathlib import Path
from typing import Any, Dict
from backend.environments.base import TerminalBackend
from backend.config import WORKSPACE_ROOT


class LocalBackend(TerminalBackend):
    def __init__(self) -> None:
        self.root = WORKSPACE_ROOT

    def exec(self, cmd: str, timeout: int = 120) -> Dict[str, Any]:
        proc = subprocess.run(cmd, shell=True, cwd=str(self.root), capture_output=True, text=True, timeout=timeout)
        return {"ok": proc.returncode == 0, "stdout": proc.stdout, "stderr": proc.stderr, "code": proc.returncode}

    def read_file(self, path: str) -> str:
        return (self.root / path).read_text(encoding="utf-8", errors="replace")

    def write_file(self, path: str, content: str) -> str:
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        return str(p)

    def list_dir(self, path: str) -> str:
        p = self.root / path
        return "\n".join(x.name for x in sorted(p.iterdir()))
