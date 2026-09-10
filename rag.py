"""RAG local: chunking por funciones/clases + búsqueda híbrida BM25 + vectores."""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import struct
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

try:
    import sqlite_vec
    HAS_SQLITE_VEC = True
except ImportError:
    HAS_SQLITE_VEC = False

_FN_RX = re.compile(
    r"(?m)^(?:(?:export\s+)?(?:async\s+)?function\s+\w+|def\s+\w+|class\s+\w+|fn\s+\w+)",
)


def semantic_chunks(text: str, chunk_size: int = 1200, overlap: int = 200) -> List[str]:
    """Parte por definiciones (AST ligero). tree-sitter si está instalado."""
    try:
        import tree_sitter_python as tspy  # type: ignore
        from tree_sitter import Language, Parser  # type: ignore
        lang = Language(tspy.language())
        parser = Parser(lang)
        tree = parser.parse(text.encode("utf-8"))
        spans = []
        def walk(n):
            if n.type in ("function_definition", "class_definition"):
                spans.append((n.start_byte, n.end_byte))
            for c in n.children:
                walk(c)
        walk(tree.root_node)
        if spans:
            out = []
            for a, b in spans:
                out.append(text.encode("utf-8")[a:b].decode("utf-8", "replace")[:chunk_size * 2])
            return out or [text[:chunk_size]]
    except Exception:
        pass
    parts: List[str] = []
    idxs = [m.start() for m in _FN_RX.finditer(text)]
    if idxs:
        idxs.append(len(text))
        for i, start in enumerate(idxs[:-1]):
            block = text[start:idxs[i + 1]].strip()
            if block:
                if len(block) > chunk_size * 2:
                    step = max(1, chunk_size - overlap)
                    for s in range(0, len(block), step):
                        parts.append(block[s:s + chunk_size])
                else:
                    parts.append(block)
        if parts:
            return parts
    chunks: List[str] = []
    step = max(1, chunk_size - overlap)
    start = 0
    while start < len(text):
        chunks.append(text[start:start + chunk_size])
        start += step
    return chunks or [""]


def _tokenize(s: str) -> List[str]:
    return re.findall(r"[A-Za-zÁÉÍÓÚÑáéíóúñ_][A-Za-z0-9_]{1,40}", (s or "").lower())


def _bm25(query: str, docs: List[str], k1: float = 1.5, b: float = 0.75) -> List[float]:
    q = _tokenize(query)
    if not q or not docs:
        return [0.0] * len(docs)
    N = len(docs)
    toks = [_tokenize(d) for d in docs]
    avgdl = sum(len(t) for t in toks) / max(N, 1)
    df: Counter[str] = Counter()
    for t in toks:
        df.update(set(t))
    scores = []
    for t in toks:
        tf = Counter(t)
        dl = len(t) or 1
        s = 0.0
        for term in q:
            n = df.get(term, 0)
            if n == 0:
                continue
            idf = math.log(1 + (N - n + 0.5) / (n + 0.5))
            f = tf.get(term, 0)
            s += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / avgdl))
        scores.append(s)
    return scores


class VectorStore:
    def __init__(self, db_path: str = "rag_memory.db", ollama_url: str = "http://localhost:11434"):
        self.db_path = db_path
        self.ollama_url = ollama_url.rstrip("/")
        self._init_db()

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        if HAS_SQLITE_VEC:
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
        return conn

    def _init_db(self):
        conn = self._conn()
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
                json={"model": os.environ.get("OTTERCODE_EMBED_MODEL", "nomic-embed-text"), "prompt": text},
                timeout=10,
            )
            if resp.status_code == 200:
                emb = resp.json().get("embedding")
                if emb:
                    return emb
        except Exception:
            pass
        return [0.0] * 768

    def add_chunk(self, source: str, content: str, metadata: Dict[str, Any] = None):
        emb = self.get_embedding(content)
        conn = self._conn()
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO document_chunks (source, content, metadata) VALUES (?, ?, ?)",
            (source, content, json.dumps(metadata or {})),
        )
        chunk_id = cur.lastrowid
        if HAS_SQLITE_VEC and emb and len(emb) == 768:
            emb_blob = struct.pack(f"{len(emb)}f", *emb)
            cur.execute("INSERT INTO vec_chunks (chunk_id, embedding) VALUES (?, ?)", (chunk_id, emb_blob))
        conn.commit()
        conn.close()

    def _vec_blob(self, emb: List[float]) -> bytes:
        return struct.pack("<I", len(emb)) + struct.pack(f"{len(emb)}f", *emb)

    def _chunk_dict(self, row, distance: float = 1.0, score: float = 1.0) -> Dict[str, Any]:
        meta = json.loads(row[3] or "{}") if len(row) > 3 else {}
        return {
            "id": row[0],
            "source": row[1],
            "content": row[2],
            "metadata": meta,
            "filepath": meta.get("filepath", row[1]),
            "distance": distance,
            "score": score,
        }

    def index_file(self, filepath: str, content: str, source: str = "workspace",
                   chunk_size: int = 1200, overlap: int = 200) -> int:
        chunks = semantic_chunks(str(content or ""), chunk_size, overlap)
        for c in chunks:
            self.add_chunk(str(filepath), c, {"source": source, "filepath": str(filepath)})
        return len(chunks)

    def search(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        conn = self._conn()
        cur = conn.cursor()
        rows = cur.execute("SELECT id, source, content, metadata FROM document_chunks").fetchall()
        lexical = []
        if rows:
            scores = _bm25(query, [r[2] or "" for r in rows])
            ranked = sorted(zip(scores, rows), key=lambda x: -x[0])[: max(limit * 3, limit)]
            lexical = [(r, s) for s, r in ranked if s > 0]

        vec_hits: Dict[int, float] = {}
        query_emb = self.get_embedding(query)
        try:
            if HAS_SQLITE_VEC and query_emb and len(query_emb) == 768:
                q_blob = self._vec_blob(query_emb)
                vrows = cur.execute(
                    """SELECT chunk_id, distance FROM vec_chunks
                       WHERE embedding MATCH ? AND k = ?""",
                    (q_blob, limit),
                ).fetchall()
                vec_hits = {int(r[0]): float(r[1]) for r in vrows}
        except Exception:
            vec_hits = {}

        merged: Dict[int, Dict[str, Any]] = {}
        by_id = {r[0]: r for r in rows}
        max_bm = max((s for _row, s in lexical), default=1.0) or 1.0
        for row, s in lexical:
            merged[row[0]] = self._chunk_dict(row, distance=1.0, score=0.45 * (s / max_bm if max_bm else 0))
        for cid, dist in vec_hits.items():
            r = by_id.get(cid)
            if not r:
                continue
            vec_score = 1.0 / (1.0 + dist)
            if cid in merged:
                merged[cid]["score"] = merged[cid]["score"] + 0.55 * vec_score
                merged[cid]["distance"] = dist
            else:
                merged[cid] = self._chunk_dict(r, distance=dist, score=0.55 * vec_score)
        conn.close()
        out = sorted(merged.values(), key=lambda d: -float(d.get("score") or 0))[:limit]
        if out:
            return out
        # LIKE fallback
        conn = self._conn()
        rows = conn.execute(
            "SELECT id, source, content, metadata FROM document_chunks WHERE content LIKE ? LIMIT ?",
            (f"%{query}%", limit),
        ).fetchall()
        conn.close()
        return [self._chunk_dict(r) for r in rows]
