# OtterCode — estado de ejecución (OtterRun) y control de aborto
from __future__ import annotations
from backend.config import *  # noqa: F401,F403
from backend.config import MAX_REVIEW_ROUNDS  # noqa: E402
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


