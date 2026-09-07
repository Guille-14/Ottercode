# OtterCode — persistencia de sesiones (historial lateral y limpieza)
from __future__ import annotations
from backend.config import *  # noqa: F401,F403
from backend.runstate import OtterRun  # noqa: E402
from backend.config import DB_PATH, WORKSPACE_ROOT  # noqa: E402
from backend.db import *  # noqa: F401,F403

HISTORY: list[dict] = []


def save_session(run: OtterRun) -> None:
    payload = {"meta": run.meta, "transcript": run.transcript}
    path = run.workdir / "ottercode_transcript.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_history() -> None:
    """Reconstruye la lista de sesiones desde los transcripts en disco."""
    HISTORY.clear()
    for path in WORKSPACE_ROOT.glob("*/ottercode_transcript.json"):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "meta" in data:
                HISTORY.append(data["meta"])
        except (json.JSONDecodeError, OSError):
            continue
    HISTORY.sort(key=lambda m: m.get("created_at", ""), reverse=True)


def cleanup_empty_tasks() -> int:
    """v3.3 · Higiene del workspace: elimina carpetas de tareas sin ningún
    archivo (misiones vacías/abortadas al instante). Devuelve cuántas borró."""
    removed = 0
    try:
        for d in WORKSPACE_ROOT.iterdir():
            if not d.is_dir() or (d / "ottercode_transcript.json").exists():
                continue
            if any(p.is_file() for p in d.rglob("*")):
                continue
            shutil.rmtree(d, ignore_errors=True)
            removed += 1
    except OSError:
        pass
    return removed


def _append_session_event(session_id: Optional[str], event: Dict[str, Any]) -> None:
    """Añade un evento al transcript de una sesión (auditoría: creación de
    agentes dinámicos ligada a la sesión actual)."""
    if not session_id:
        return
    path = WORKSPACE_ROOT / session_id / "ottercode_transcript.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("transcript"), list):
            data["transcript"].append(event)
            path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except (json.JSONDecodeError, OSError):
        pass
    # FASE 2 · también persistir en SQLite
    try:
        kind = event.get("kind", "system")
        conn = sqlite3.connect(str(DB_PATH))
        conn.execute(
            """INSERT INTO messages
               (session_id, kind, agent, iteration, tool_name,
                tool_args_json, tool_ok, text)
               VALUES (?,?,?,?,?,?,?,?)""",
            (session_id, kind, event.get("agent"), event.get("iteration"),
             event.get("tool"),
             json.dumps(event.get("args"), ensure_ascii=False) if event.get("args") else None,
             1 if event.get("ok") else (0 if "ok" in event else None),
             event.get("text", "")),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass


# Modelos que soportan Function Calling nativo (se les envia esquema de tools).
# La comprobación en ollama._llm_request ya usa coincidencia por substring
# (`any(m in run.model for m in TOOL_CAPABLE_MODELS)`), así que basta con
# listar los prefijos/familias modernas.
TOOL_CAPABLE_MODELS = {
    "llama3.1", "llama3.2", "llama3.3",
    "qwen2.5", "qwen3", "qwen3.8",
    "gemma2", "gemma3", "gemma4",
    "mistral", "mistral-nemo", "mixtral",
    "phi4", "sqlcoder",
}

