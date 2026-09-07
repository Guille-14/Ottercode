# OtterCode — estado de ejecución (OtterRun) y control de aborto
from __future__ import annotations
from backend.config import *  # noqa: F401,F403
from backend.config import MAX_REVIEW_ROUNDS  # noqa: E402
import re
import json
from backend.agents import get_agent  # noqa: E402
from backend.agents import *  # noqa: F401,F403


class AbortRequested(Exception):
    """El usuario abortó la misión desde la UI."""

class OtterRun:
    def __init__(self, task_id: str, task_text: str, model: str, loop_mode: bool,
                 mode: str, start_agent: str, workdir: Path,
                 hacker: bool = False, num_ctx: Optional[int] = None,
                 goal: str = "", plan_only: bool = False,
                 ultra_review: bool = False,
                 resume_plan: Optional[Dict[str, str]] = None,
                 resume_task: str = "",
                 continue_task: str = "",
                 temperature: Optional[float] = None,
                 top_p: Optional[float] = None,
                 system_inject: Optional[str] = None,
                 yolo: bool = False,
                 max_rounds: Optional[int] = None):
        self.task_id = task_id
        self.task_text = task_text
        self.model = model
        self.loop_mode = loop_mode
        self.mode = mode                      # "chain" | "chat"
        self.start_agent = start_agent
        self.hacker = hacker                  # 🏴 modo sin censura (solo prompt)
        self.num_ctx = num_ctx                # override de contexto (v3.3)
        # FASE 3 · parámetros del perfil activo
        self.temperature = temperature if temperature is not None else 0.7
        self.top_p = top_p if top_p is not None else 0.9
        # FASE 4 · inyección temporal de sistema (/sys)
        self.system_inject = (system_inject or "").strip()
        # FASE 4 · bypass de seguridad
        self.yolo = yolo
        self.memory_block: str = ""           # 🧠 recuerdo del vault (v3.3)
        self._memory_done = False             # nota de misión ya escrita
        self._files_ever_written = False      # v4.9 · FIX B: ya se escribió algo
        # v6.0 · Fase 1: cola de contexto con roles (user/assistant/system)
        self.messages: List[Dict[str, str]] = []
        self._transport = "chat"              # chat | generate (fallback automático)
        # ── v4.1 · comandos estilo Claude Code ──
        self.goal = (goal or "").strip()      # 🎯 /goal
        self.plan_only = plan_only            # 📋 /ultraplan
        self.ultra_review = ultra_review      # 🔬 /ultrareview
        self.resume_plan = resume_plan        # ▶ ejecutar plan aprobado
        self.resume_task = (resume_task or "").strip()  # tarea original del plan
        self.continue_task = (continue_task or "").strip()  # 🧵 hilo previo
        self.max_rounds = MAX_REVIEW_ROUNDS if loop_mode else 1
        if max_rounds and max_rounds > 0:
            self.max_rounds = max_rounds
        if self.ultra_review:
            self.loop_mode = True
            self.max_rounds = min(self.max_rounds or 3, 3)
        self.workdir = workdir
        self.executor = tools.ToolExecutor(workdir)
        self.transcript: List[Dict[str, Any]] = [
            {"kind": "user", "text": task_text},
            {
                "kind": "system",
                "text": (
                    f"⚓ Misión zarpada · {get_agent(start_agent).icon} {get_agent(start_agent).nombre} "
                    f"· modo {mode} · bucle {'∞' if loop_mode else 'off'} · modelo {model} · "
                    f"workspace {workdir.name}"
                ),
            },
        ]
        if self.continue_task:
            self.transcript.append({
                "kind": "system",
                "text": (f"🧵 Continuando la conversación de {self.continue_task} "
                         "(mismo hilo y workspace)."),
            })
        self.files_report: List[Dict[str, Any]] = []
        self.approved: Optional[bool] = None
        self.iterations = 0
        self.aborted = False
        self.injected_agents: List[str] = []
        self.meta: Dict[str, Any] = {
            "id": task_id,
            "task": task_text,
            "model": model,
            "loop_mode": self.loop_mode,
            "mode": mode,
            "start_agent": start_agent,
            "hacker": hacker,
            "goal": self.goal,
            "plan_only": plan_only,
            "ultra_review": ultra_review,
            "continue_task": self.continue_task,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
            "approved": None,
            "iterations": 0,
            "files": [],
            "injected_agents": [],
            "duration_s": 0.0,
        }


# ---------------------------------------------------------------------------
# Turnos de agente (core y dinámicos comparten el mismo motor)
# ---------------------------------------------------------------------------

def _condense_entries(entries: List[Tuple[str, str]]) -> str:
    """Texto condensado de los pasos acumulados (entrada del resumidor).

    Presupuesto POR PASO en función del número de pasos: nunca se pierde un
    paso entero ni se desboda el prompt del resumidor."""
    n = max(1, len(entries))
    total_budget = 16_000
    per_step = max(280, min(2200, total_budget // (n * 2)))
    head_chars = per_step
    res_chars = max(140, per_step // 2)
    parts: List[str] = []
    for i, (model_output, tool_result) in enumerate(entries, start=1):
        mo = model_output[:head_chars]
        tr = tool_result[-res_chars:] if tool_result else ""
        parts.append(f"## PASO {i}\nSALIDA DEL AGENTE:\n{mo}\nRESULTADO DE LA SKILL:\n{tr}")
    return "\n\n".join(parts)[:total_budget]


CHECKPOINT_DIR = Path(__file__).resolve().parent / "data" / "checkpoints"


def _ck_path(mission_id: str) -> Path:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.\-]+", "_", mission_id or "unknown")[:80]
    return CHECKPOINT_DIR / f"{safe}.jsonl"


def append_checkpoint(
    run: Any,
    *,
    done: str,
    decisions: str = "",
    pending: str = "",
    next_action: str = "",
    kind: str = "step",
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """JSONL append-only: una línea por unidad de trabajo."""
    rec = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "task_id": getattr(run, "task_id", ""),
        "kind": kind,
        "done": done,
        "decisions": decisions,
        "pending": pending,
        "next_action": next_action,
        "agent": getattr(run, "start_agent", ""),
        "files": [f.get("path") if isinstance(f, dict) else str(f)
                  for f in (getattr(run, "files_report", None) or [])][:40],
        "open": True,
        **(extra or {}),
    }
    try:
        path = _ck_path(str(getattr(run, "task_id", "") or "unknown"))
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def load_checkpoints(mission_id: str) -> List[Dict[str, Any]]:
    path = _ck_path(mission_id)
    if not path.is_file():
        return []
    out: List[Dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return out


def last_checkpoint(mission_id: str) -> Optional[Dict[str, Any]]:
    rows = load_checkpoints(mission_id)
    return rows[-1] if rows else None


def mark_checkpoint_closed(mission_id: str) -> None:
    rec = last_checkpoint(mission_id) or {}
    dummy = type("R", (), {"task_id": mission_id, "start_agent": "", "files_report": []})()
    append_checkpoint(dummy, done="misión cerrada", kind="closed",
                      pending="", next_action="", extra={"open": False})


def list_unfinished_checkpoints(limit: int = 12) -> List[Dict[str, Any]]:
    CHECKPOINT_DIR.mkdir(parents=True, exist_ok=True)
    found: List[Dict[str, Any]] = []
    for p in sorted(CHECKPOINT_DIR.glob("*.jsonl"), key=lambda x: x.stat().st_mtime, reverse=True):
        rows = load_checkpoints(p.stem)
        if not rows:
            continue
        last = rows[-1]
        if last.get("kind") == "closed" or last.get("open") is False:
            continue
        found.append({
            "task_id": p.stem,
            "last": last,
            "steps": len(rows),
            "path": str(p),
        })
        if len(found) >= limit:
            break
    return found


def resume_summary(mission_id: str) -> str:
    last = last_checkpoint(mission_id)
    if not last:
        return ""
    return (
        f"# CHECKPOINT PREVIO (reanudar, no repetir lo hecho)\n"
        f"Hecho: {last.get('done','')}\n"
        f"Decisiones: {last.get('decisions','')}\n"
        f"Pendiente: {last.get('pending','')}\n"
        f"Siguiente acción: {last.get('next_action','')}\n"
        f"No reescribas archivos ya listados: {last.get('files') or []}"
    )


def note_stall(run: Any, key: str, error: str, limit: int = 3) -> bool:
    """True si la subtarea queda bloqueada (≥limit errores iguales)."""
    stalls = getattr(run, "_stalls", None)
    if not isinstance(stalls, dict):
        stalls = {}
        run._stalls = stalls
    sig = f"{key}|{(error or '')[:180]}"
    n = int(stalls.get(sig, 0)) + 1
    stalls[sig] = n
    if n < limit:
        return False
    blocked = getattr(run, "_blocked", None)
    if not isinstance(blocked, list):
        blocked = []
        run._blocked = blocked
    blocked.append({"key": key, "error": error[:300], "n": n})
    append_checkpoint(
        run, kind="blocked", done=f"subtarea bloqueada: {key}",
        decisions=f"{n} errores iguales", pending=error[:200],
        next_action="intervención humana o saltar subtarea",
        extra={"blocked": True},
    )
    return True


