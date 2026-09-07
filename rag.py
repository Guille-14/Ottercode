"""
rag.py · Pipeline RAG Local & Memoria Semántica con Ollama y sqlite-vec
"""

import os
import json
import sqlite3
import requests
from pathlib import Path
from typing import List, Dict, Any, Optional

try:
    import sqlite_vec
    HAS_SQLITE_VEC = True
except ImportError:
    HAS_SQLITE_VEC = False


class VectorStore:
    def __init__(self, db_path: str = "rag_memory.db", ollama_url: str = "http://localhost:11434"):
        self.db_path = db_path
        self.ollama_url = ollama_url.rstrip("/")
        self._init_db()

    def _init_db(self):
        conn = sqlite3.connect(self.db_path)
        if HAS_SQLITE_VEC:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
        
        conn.execute("""
            CREATE TABLE IF NOT EXISTS document_chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source TEXT,
                content TEXT,
                metadata TEXT
            )
        """)
        if HAS_SQLITE_VEC:
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS vec_chunks USING vec0(
                    chunk_id INTEGER PRIMARY KEY,
                    embedding float[768]
                )
            """)
        conn.commit()
        conn.close()

    def get_embedding(self, text: str) -> List[float]:
        try:
            resp = requests.post(
                f"{self.ollama_url}/api/embeddings",
                json={"model": os.environ.get("OTTERCODE_MODEL", "qwen3.8-distill-64k"), "prompt": text},
                timeout=10,
            )
            if resp.status_code == 200:
                data = resp.json()
                emb = data.get("embedding")
                if emb:
                    return emb
        except Exception:
            pass
        # Fallback a vector cero de 768 dims si falla
        return [0.0] * 768

    def add_chunk(self, source: str, content: str, metadata: Dict[str, Any] = None):
        emb = self.get_embedding(content)
        conn = sqlite3.connect(self.db_path)
        if HAS_SQLITE_VEC:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)

        cur = conn.cursor()
        cur.execute(
            "INSERT INTO document_chunks (source, content, metadata) VALUES (?, ?, ?)",
            (source, content, json.dumps(metadata or {}))
        )
        chunk_id = cur.lastrowid
        if HAS_SQLITE_VEC and emb and len(emb) == 768:
            import struct
            emb_blob = struct.pack(f"{len(emb)}f", *emb)
            cur.execute("INSERT INTO vec_chunks (chunk_id, embedding) VALUES (?, ?)", (chunk_id, emb_blob))
        conn.commit()
        conn.close()

    def _vec_blob(self, emb: List[float]) -> bytes:
        """Convierte un vector a blob con prefijo de dimensión que espera sqlite-vec."""
        import struct
        return struct.pack("<I", len(emb)) + struct.pack(f"{len(emb)}f", *emb)

    def _chunk_dict(self, row, distance: float = 1.0) -> Dict[str, Any]:
        meta = json.loads(row[3] or "{}") if len(row) > 3 else {}
        return {
            "id": row[0],
            "source": row[1],
            "content": row[2],
            "metadata": meta,
            "filepath": meta.get("filepath", row[1]),
            "distance": distance,
            "score": 1.0,
        }

    def index_file(self, filepath: str, content: str, source: str = "workspace",
                   chunk_size: int = 1200, overlap: int = 200) -> int:
        """Indexa un archivo en fragmentos solapados. Devuelve el nº de fragmentos creados."""
        text = str(content or "")
        chunks: List[str] = []
        step = max(1, chunk_size - overlap)
        start = 0
        while start < len(text):
            chunks.append(text[start: start + chunk_size])
            start += step
        if not chunks:
            chunks = [""]
        for c in chunks:
            self.add_chunk(str(filepath), c, {"source": source, "filepath": str(filepath)})
        return len(chunks)

    def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        query_emb = self.get_embedding(query)
        conn = sqlite3.connect(self.db_path)
        if HAS_SQLITE_VEC:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)

        results = []
        try:
            if HAS_SQLITE_VEC and query_emb and len(query_emb) == 768:
                q_blob = self._vec_blob(query_emb)
                cur = conn.cursor()
                rows = cur.execute(
                    """
                    SELECT chunk_id, distance
                    FROM vec_chunks
                    WHERE embedding MATCH ? AND k = ?
                    """,
                    (q_blob, limit),
                ).fetchall()
                if rows:
                    ids = [r[0] for r in rows]
                    dist = {r[0]: r[1] for r in rows}
                    placeholders = ",".join("?" * len(ids))
                    cur = conn.cursor()
                    rows2 = cur.execute(
                        f"SELECT id, source, content, metadata FROM document_chunks WHERE id IN ({placeholders})",
                        ids,
                    ).fetchall()
                    by_id = {r[0]: r for r in rows2}
                    for cid in ids:
                        r = by_id.get(cid)
                        if r:
                            results.append(self._chunk_dict(r, float(dist.get(cid, 1.0))))
        except Exception:
            results = []

        # Fallback de búsqueda por LIKE si vec_chunks no está disponible o falla
        if not results:
            cur = conn.cursor()
            rows = cur.execute(
                "SELECT id, source, content, metadata FROM document_chunks WHERE content LIKE ? LIMIT ?",
                (f"%{query}%", limit)
            ).fetchall()
            for r in rows:
                results.append(self._chunk_dict(r))

        conn.close()
        return results
