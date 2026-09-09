from __future__ import annotations
import subprocess
from typing import Any, Dict
from backend.environments.base import TerminalBackend
from backend.home import cfg_get


class DockerBackend(TerminalBackend):
    def _c(self) -> str:
        return str(cfg_get("terminal", "docker", "container", default="ottercode-workspace") or "ottercode-workspace")

    def exec(self, cmd: str, timeout: int = 120) -> Dict[str, Any]:
        proc = subprocess.run(["docker", "exec", self._c(), "bash", "-lc", cmd], capture_output=True, text=True, timeout=timeout)
        return {"ok": proc.returncode == 0, "stdout": proc.stdout, "stderr": proc.stderr, "code": proc.returncode}

    def read_file(self, path: str) -> str:
        return self.exec(f"cat {path}").get("stdout") or ""

    def write_file(self, path: str, content: str) -> str:
        self.exec(f"mkdir -p $(dirname {path})")
        proc = subprocess.run(["docker", "exec", "-i", self._c(), "tee", path], input=content, text=True, capture_output=True, timeout=60)
        return path

    def list_dir(self, path: str) -> str:
        return self.exec(f"ls -la {path}").get("stdout") or ""
