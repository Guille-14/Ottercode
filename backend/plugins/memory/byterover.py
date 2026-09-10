from __future__ import annotations
from typing import Any, Dict, List
from backend.plugins.memory.provider import MemoryProvider
from backend.home import HOME

class Provider(MemoryProvider):
    name = "byterover"
    def store(self, key: str, value: str, meta: Dict[str, Any] | None = None) -> Dict[str, Any]:
        p = HOME / "plugins" / "memory" / "byterover.jsonl"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.open("a", encoding="utf-8").write(f"{key}\t{value}\n")
        return {"ok": True, "provider": self.name}
    def retrieve(self, key: str) -> str:
        p = HOME / "plugins" / "memory" / "byterover.jsonl"
        if not p.is_file():
            return ""
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.startswith(key + "\t"):
                return line.split("\t", 1)[1]
        return ""
    def search(self, query: str, limit: int = 8) -> List[Dict[str, Any]]:
        p = HOME / "plugins" / "memory" / "byterover.jsonl"
        hits = []
        if p.is_file():
            for line in p.read_text(encoding="utf-8").splitlines():
                if query.lower() in line.lower():
                    k, _, v = line.partition("\t")
                    hits.append({"key": k, "value": v})
                    if len(hits) >= limit:
                        break
        return hits
    def delete(self, key: str) -> bool:
        return True
