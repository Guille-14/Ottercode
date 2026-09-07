"""OtterCode — orquestador local de agentes sobre Ollama (motor modular)."""
# Re-export del app y del namespace plano original para compatibilidad
# con dev/selftest.py (atributos leídos y mutados a nivel de módulo).
from backend.main import app
from backend.config import *  # noqa: F401,F403
from backend.agents import *  # noqa: F401,F403
from backend.prompts import *  # noqa: F401,F403
from backend.ollama import *  # noqa: F401,F403
from backend.engine import *  # noqa: F401,F403
from backend.runstate import *  # noqa: F401,F403
from backend.history import *  # noqa: F401,F403
from backend.vault import *  # noqa: F401,F403
from backend.routes import TaskRequest  # noqa
from backend.main import _auth_ok  # noqa
from backend.prompts import _prev_conversation_block  # noqa
from backend.ollama import _LlmSession, _llm_request, _ollama_ndjson_text  # noqa
from backend.engine import (  # noqa
    _strip_think, _salvage_before_finalize, _salvage_cut_json,
    _save_partial_on_abort, _should_rescue, _rescue_code_from_text,
    _plan_has_code, _write_size_guard, _WRITE_TOOLS,
    _forced_step_prompt, _invalid_json_feedback,
)
from backend.runstate import _condense_entries  # noqa
from backend.vault import _memory_recall  # noqa
__all__ = ["app"]
