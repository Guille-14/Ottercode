# OtterCode — persistencia SQLite (chats, historial SQL, transcripciones)
from __future__ import annotations
from backend.config import *  # noqa: F401,F403  (json/os/re/time/uuid/Path + env)
from backend.runstate import OtterRun  # noqa: E402
from backend.config import DB_PATH, WORKSPACE_ROOT  # noqa: E402


def init_db() -> None:
    """Crea las tablas SQLite + FTS5 si no existen."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            task TEXT NOT NULL,
            model TEXT,
            mode TEXT DEFAULT 'chat',
            start_agent TEXT DEFAULT 'agent',
            created_at TEXT,
            approved INTEGER,
            iterations INTEGER DEFAULT 0,
            duration_s REAL DEFAULT 0,
            files_json TEXT DEFAULT '[]',
            injected_agents_json TEXT DEFAULT '[]',
            hacker INTEGER DEFAULT 0,
            goal TEXT DEFAULT '',
            plan_only INTEGER DEFAULT 0,
            ultra_review INTEGER DEFAULT 0,
            meta_json TEXT DEFAULT '{}'
        );
        CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES sessions(id),
            kind TEXT NOT NULL,
            agent TEXT,
            iteration INTEGER,
            tool_name TEXT,
            tool_args_json TEXT,
            tool_ok INTEGER,
            text TEXT NOT NULL,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id);
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            agente_id TEXT,
            contenido TEXT NOT NULL,
            timestamp TEXT NOT NULL
        );
    """)
    # FTS5 virtual table (si no existe)
    try:
        conn.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                text, agent, tool_name,
                content='messages',
                content_rowid='id'
            )
        """)
        # Triggers para mantener FTS sincronizado
        for trigger_sql in [
            """CREATE TRIGGER IF NOT EXISTS messages_fts_ai AFTER INSERT ON messages BEGIN
                INSERT INTO messages_fts(rowid, text, agent, tool_name)
                VALUES (new.id, new.text, new.agent, new.tool_name);
            END""",
            """CREATE TRIGGER IF NOT EXISTS messages_fts_ad AFTER DELETE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, text, agent, tool_name)
                VALUES ('delete', old.id, old.text, old.agent, old.tool_name);
            END""",
            """CREATE TRIGGER IF NOT EXISTS messages_fts_au AFTER UPDATE ON messages BEGIN
                INSERT INTO messages_fts(messages_fts, rowid, text, agent, tool_name)
                VALUES ('delete', old.id, old.text, old.agent, old.tool_name);
                INSERT INTO messages_fts(rowid, text, agent, tool_name)
                VALUES (new.id, new.text, new.agent, new.tool_name);
            END""",
        ]:
            conn.execute(trigger_sql)
    except sqlite3.OperationalError:
        pass  # FTS5 no disponible en esta build de SQLite
    conn.close()


def _migrate_json_to_db() -> None:
    """Migra transcripts JSON existentes a SQLite (una sola vez)."""
    conn = sqlite3.connect(str(DB_PATH))
    count = conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    if count > 0:
        conn.close()
        return  # ya migrado
    for d in WORKSPACE_ROOT.iterdir():
        if not d.is_dir():
            continue
        tf = d / "ottercode_transcript.json"
        if not tf.exists():
            continue
        try:
            data = json.loads(tf.read_text(encoding="utf-8"))
            meta = data.get("meta", {})
            transcript = data.get("transcript", [])
            session_id = meta.get("id", d.name)
            conn.execute(
                """INSERT OR IGNORE INTO sessions
                   (id, task, model, mode, start_agent, created_at, approved,
                    iterations, duration_s, files_json, injected_agents_json,
                    hacker, goal, plan_only, ultra_review, meta_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (session_id, meta.get("task", ""), meta.get("model", ""),
                 meta.get("mode", "chat"), meta.get("start_agent", "agent"),
                 meta.get("created_at", ""), meta.get("approved"),
                 meta.get("iterations", 0), meta.get("duration_s", 0),
                 json.dumps(meta.get("files", []), ensure_ascii=False),
                 json.dumps(meta.get("injected_agents", []), ensure_ascii=False),
                 1 if meta.get("hacker") else 0, meta.get("goal", ""),
                 1 if meta.get("plan_only") else 0,
                 1 if meta.get("ultra_review") else 0,
                 json.dumps(meta, ensure_ascii=False)),
            )
            for ev in transcript:
                kind = ev.get("kind", "system")
                conn.execute(
                    """INSERT INTO messages
                       (session_id, kind, agent, iteration, tool_name,
                        tool_args_json, tool_ok, text)
                       VALUES (?,?,?,?,?,?,?,?)""",
                    (session_id, kind, ev.get("agent"), ev.get("iteration"),
                     ev.get("tool"),
                     json.dumps(ev.get("args"), ensure_ascii=False) if ev.get("args") else None,
                     1 if ev.get("ok") else (0 if "ok" in ev else None),
                     ev.get("text", "")),
                )
        except Exception:
            continue
    conn.commit()
    conn.close()


def save_session_to_db(run: "OtterRun") -> None:
    """Persiste la sesión completada en SQLite."""
    conn = sqlite3.connect(str(DB_PATH))
    meta = run.meta
    conn.execute(
        """INSERT OR REPLACE INTO sessions
           (id, task, model, mode, start_agent, created_at, approved,
            iterations, duration_s, files_json, injected_agents_json,
            hacker, goal, plan_only, ultra_review, meta_json)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (meta.get("id", ""), meta.get("task", ""), meta.get("model", ""),
         meta.get("mode", "chat"), meta.get("start_agent", "agent"),
         meta.get("created_at", ""), meta.get("approved"),
         meta.get("iterations", 0), meta.get("duration_s", 0),
         json.dumps(meta.get("files", []), ensure_ascii=False),
         json.dumps(meta.get("injected_agents", []), ensure_ascii=False),
         1 if meta.get("hacker") else 0, meta.get("goal", ""),
         1 if meta.get("plan_only") else 0,
         1 if meta.get("ultra_review") else 0,
         json.dumps(meta, ensure_ascii=False)),
    )
    # Insertar mensajes del transcript
    conn.execute("DELETE FROM messages WHERE session_id = ?", (meta.get("id", ""),))
    for ev in run.transcript:
        kind = ev.get("kind", "system")
        conn.execute(
            """INSERT INTO messages
               (session_id, kind, agent, iteration, tool_name,
                tool_args_json, tool_ok, text)
               VALUES (?,?,?,?,?,?,?,?)""",
            (meta.get("id", ""), kind, ev.get("agent"), ev.get("iteration"),
             ev.get("tool"),
             json.dumps(ev.get("args"), ensure_ascii=False) if ev.get("args") else None,
             1 if ev.get("ok") else (0 if "ok" in ev else None),
             ev.get("text", "")),
        )
    conn.commit()
    conn.close()


def search_history(query: str = "", limit: int = 50, offset: int = 0) -> list:
    """Busca sesiones por FTS5 o devuelve las más recientes."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    if query.strip():
        try:
            rows = conn.execute(
                """SELECT DISTINCT s.* FROM sessions s
                   JOIN messages_fts f ON f.rowid IN (
                       SELECT id FROM messages WHERE session_id = s.id
                   )
                   WHERE messages_fts MATCH ?
                   ORDER BY s.created_at DESC LIMIT ? OFFSET ?""",
                (query, limit, offset),
            ).fetchall()
        except sqlite3.OperationalError:
            rows = conn.execute(
                """SELECT * FROM sessions WHERE task LIKE ?
                   ORDER BY created_at DESC LIMIT ? OFFSET ?""",
                (f"%{query}%", limit, offset),
            ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM sessions ORDER BY created_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    result = []
    for r in rows:
        d = dict(r)
        d["files"] = json.loads(d.pop("files_json", "[]"))
        d["injected_agents"] = json.loads(d.pop("injected_agents_json", "[]"))
        d["hacker"] = bool(d.pop("hacker", 0))
        d["plan_only"] = bool(d.pop("plan_only", 0))
        d["ultra_review"] = bool(d.pop("ultra_review", 0))
        d.pop("meta_json", None)
        result.append(d)
    conn.close()
    return result


def get_session_detail(session_id: str) -> dict:
    """Recupera sesión + mensajes completos (para /api/history/{id})."""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    r = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
    if not r:
        conn.close()
        return {}
    d = dict(r)
    files = json.loads(d.pop("files_json", "[]"))
    injected = json.loads(d.pop("injected_agents_json", "[]"))
    hacker = bool(d.pop("hacker", 0))
    plan_only = bool(d.pop("plan_only", 0))
    ultra_review = bool(d.pop("ultra_review", 0))
    meta = json.loads(d.pop("meta_json", "{}"))
    msgs = conn.execute(
        "SELECT kind, agent, iteration, tool_name, tool_args_json, tool_ok, text "
        "FROM messages WHERE session_id = ? ORDER BY id", (session_id,)
    ).fetchall()
    conn.close()
    transcript = []
    for m in msgs:
        md = dict(m)
        if md.get("tool_args_json"):
            md["args"] = json.loads(md.pop("tool_args_json"))
        else:
            md.pop("tool_args_json", None)
        if md.get("tool_ok") is not None:
            md["ok"] = bool(md.pop("tool_ok"))
        else:
            md.pop("tool_ok", None)
        if not md.get("tool_name"):
            md.pop("tool_name", None)
        transcript.append(md)
    d["meta"] = meta
    d["meta"]["files"] = files
    d["meta"]["injected_agents"] = injected
    d["meta"]["hacker"] = hacker
    d["meta"]["plan_only"] = plan_only
    d["meta"]["ultra_review"] = ultra_review
    d["transcript"] = transcript
    return d


