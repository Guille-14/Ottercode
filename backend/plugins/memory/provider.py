from __future__ import annotations
from abc import ABC, abstractmethod
from typing import Any, Dict, List


class MemoryProvider(ABC):
    name = "base"

    @abstractmethod
    def store(self, key: str, value: str, meta: Dict[str, Any] | None = None) -> Dict[str, Any]: ...
    @abstractmethod
    def retrieve(self, key: str) -> str: ...
    @abstractmethod
    def search(self, query: str, limit: int = 8) -> List[Dict[str, Any]]: ...
    @abstractmethod
    def delete(self, key: str) -> bool: ...
