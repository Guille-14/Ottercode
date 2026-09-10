"""Fase 1: sonda /api/show — visión y contexto con payloads sintéticos."""
from backend.model_probe import get_model_context, get_model_vision


def test_context_prefers_parameters_num_ctx():
    show = {
        "parameters": "num_ctx 8192\nstop <|end|>\n",
        "model_info": {"qwen2.context_length": 32768},
    }
    assert get_model_context("x", show) == 8192


def test_context_from_any_family_key():
    show = {"model_info": {"gemma3.context_length": 131072}}
    assert get_model_context("gemma", show) == 131072
    show2 = {"model_info": {"llama.context_length": 4096}}
    assert get_model_context("llama", show2) == 4096


def test_vision_capabilities_and_block_count():
    vis = {"capabilities": ["completion", "vision"]}
    txt = {"capabilities": ["completion"], "model_info": {"llama.context_length": 8192}}
    assert get_model_vision("llava", vis) is True
    assert get_model_vision("qwen2.5-coder:7b", txt) is False
    blk = {"model_info": {"qwen2vl.vision.block_count": 32}}
    assert get_model_vision("qwen2.5vl", blk) is True


def test_timeout_local_readable():
    from backend.ollama import LAST_GENERATE_TIMEOUT, generate_timeout_for
    to = generate_timeout_for("http://127.0.0.1:11434/api/chat")
    assert to[1] >= 900
    assert LAST_GENERATE_TIMEOUT == to
    remote = generate_timeout_for("https://example.com/api/chat")
    assert remote[1] < to[1] or remote == to  # GENERATE_TIMEOUT 420 default
