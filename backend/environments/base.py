from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict


class TerminalBackend(ABC):
    @abstractmethod
    def exec(self, cmd: str, timeout: int = 120) -> Dict[str, Any]: ...
    @abstractmethod
    def read_file(self, path: str) -> str: ...
    @abstractmethod
    def write_file(self, path: str, content: str) -> str: ...
    @abstractmethod
    def list_dir(self, path: str) -> str: ...
