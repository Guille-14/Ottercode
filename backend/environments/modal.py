from __future__ import annotations
from typing import Any, Dict
from backend.environments.base import TerminalBackend


class ModalBackend(TerminalBackend):
    """Hiberna cuando idle; despierta on-demand (stub de API Modal)."""

    _awake = False

    def _wake(self) -> None:
        self._awake = True

    def exec(self, cmd: str, timeout: int = 120) -> Dict[str, Any]:
        self._wake()
        return {"ok": False, "stdout": "", "stderr": "modal: configura MODAL_TOKEN", "code": 1}

    def read_file(self, path: str) -> str:
        self._wake()
        return ""

    def write_file(self, path: str, content: str) -> str:
        self._wake()
        return path

    def list_dir(self, path: str) -> str:
        self._wake()
        return ""
