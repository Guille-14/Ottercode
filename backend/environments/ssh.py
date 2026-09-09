from __future__ import annotations
import subprocess
from typing import Any, Dict
from backend.environments.base import TerminalBackend
from backend.home import cfg_get


class SshBackend(TerminalBackend):
    def _base(self) -> list[str]:
        host = str(cfg_get("terminal", "ssh", "host", default="") or "")
        user = str(cfg_get("terminal", "ssh", "user", default="") or "")
        key = str(cfg_get("terminal", "ssh", "key", default="") or "")
        tgt = f"{user}@{host}" if user else host
        cmd = ["ssh", "-o", "BatchMode=yes"]
        if key:
            cmd += ["-i", key]
        cmd.append(tgt)
        return cmd

    def exec(self, cmd: str, timeout: int = 120) -> Dict[str, Any]:
        proc = subprocess.run(self._base() + [cmd], capture_output=True, text=True, timeout=timeout)
        return {"ok": proc.returncode == 0, "stdout": proc.stdout, "stderr": proc.stderr, "code": proc.returncode}

    def read_file(self, path: str) -> str:
        return self.exec(f"cat {path}").get("stdout") or ""

    def write_file(self, path: str, content: str) -> str:
        self.exec(f"mkdir -p $(dirname {path})")
        return path

    def list_dir(self, path: str) -> str:
        return self.exec(f"ls -la {path}").get("stdout") or ""
