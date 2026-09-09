from __future__ import annotations
import subprocess
from typing import Any, Dict
from backend.environments.base import TerminalBackend


class SingularityBackend(TerminalBackend):
    def exec(self, cmd: str, timeout: int = 120) -> Dict[str, Any]:
        proc = subprocess.run(["singularity", "exec", "ottercode.sif", "bash", "-lc", cmd], capture_output=True, text=True, timeout=timeout)
        return {"ok": proc.returncode == 0, "stdout": proc.stdout, "stderr": proc.stderr, "code": proc.returncode}

    def read_file(self, path: str) -> str:
        return self.exec(f"cat {path}").get("stdout") or ""

    def write_file(self, path: str, content: str) -> str:
        return path

    def list_dir(self, path: str) -> str:
        return self.exec(f"ls {path}").get("stdout") or ""
