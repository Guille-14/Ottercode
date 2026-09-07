"""
orchestrator.py · Ejecución Concurrente / Paralela para OtterCode v3.0
Soporte para ejecución paralela con APIs remotas y secuencial con Ollama local.
"""

import os
import asyncio
import logging
from typing import List, Dict, Any, Callable, Awaitable, Optional
from concurrent.futures import ThreadPoolExecutor

logger = logging.getLogger("ottercode.orchestrator")

# Si PARALLEL_EXECUTION=1 o el backend es openai/vllm/groq, se permite paralelismo
PARALLEL_EXECUTION = os.environ.get("PARALLEL_EXECUTION", "0") == "1"
REMOTE_BACKENDS = {"openai", "vllm", "groq", "together", "groqcloud"}


def is_parallel_allowed(backend: str = "ollama") -> bool:
    """Determina si se puede ejecutar en paralelo."""
    if PARALLEL_EXECUTION:
        return True
    return backend.lower() in REMOTE_BACKENDS


class AgentTask:
    """Representa una tarea de agente ejecutable."""
    def __init__(self, agent_id: str, func: Callable[..., Awaitable[Any]], **kwargs):
        self.agent_id = agent_id
        self.func = func
        self.kwargs = kwargs
        self.result: Any = None
        self.error: Optional[Exception] = None


class Orchestrator:
    """Orquestador de ejecución de agentes con soporte paralelo/secuencial."""
    def __init__(self, backend: str = "ollama", max_workers: int = 4):
        self.backend = backend
        self.parallel = is_parallel_allowed(backend)
        self.max_workers = max_workers
        self._executor = ThreadPoolExecutor(max_workers=max_workers) if self.parallel else None

    async def run_agents(self, tasks: List[AgentTask]) -> List[AgentTask]:
        """Ejecuta una lista de tareas de agentes (paralelo o secuencial)."""
        if self.parallel and len(tasks) > 1:
            return await self._run_parallel(tasks)
        return await self._run_sequential(tasks)

    async def _run_parallel(self, tasks: List[AgentTask]) -> List[AgentTask]:
        """Ejecución paralela con asyncio.gather."""
        async def _exec(task: AgentTask):
            try:
                task.result = await task.func(**task.kwargs)
            except Exception as exc:
                task.error = exc
                logger.error(f"Agent {task.agent_id} failed: {exc}")
            return task

        results = await asyncio.gather(*[_exec(t) for t in tasks])
        return list(results)

    async def _run_sequential(self, tasks: List[AgentTask]) -> List[AgentTask]:
        """Ejecución secuencial (GPU lock, Ollama local)."""
        for task in tasks:
            try:
                task.result = await task.func(**task.kwargs)
            except Exception as exc:
                task.error = exc
                logger.error(f"Agent {task.agent_id} failed: {exc}")
        return tasks

    def shutdown(self):
        if self._executor:
            self._executor.shutdown(wait=False)
