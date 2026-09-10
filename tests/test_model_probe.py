"""Fase 1: sonda /api/show — visión, contexto y tools nativos."""
from backend.model_probe import get_model_context, get_model_tools_capable, get_model_vision


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


def test_tools_capabilities_true_false_none():
    assert get_model_tools_capable("command-r", {"capabilities": ["completion", "tools"]}) is True
    assert get_model_tools_capable("tiny", {"capabilities": ["completion"]}) is False
    assert get_model_tools_capable("old", {"modelfile": "FROM x"}) is None
    assert get_model_tools_capable("empty", {}) is None


def test_native_auto_uses_capabilities_then_markers():
    import os
    from backend import config as cfg
    import backend.model_probe as mp
    prev = os.environ.get("OTTERCODE_NATIVE_TOOLS")
    os.environ["OTTERCODE_NATIVE_TOOLS"] = "auto"
    orig = mp.show_model

    def fake_show(name, *, force=False):
        if "command-r" in name:
            return {"capabilities": ["completion", "tools"]}
        if "plain" in name:
            return {"modelfile": "FROM x"}
        return {"capabilities": ["completion"]}

    mp.show_model = fake_show  # type: ignore[assignment]
    try:
        assert cfg.native_tools_enabled("command-r:35b") is True
        assert cfg.native_tools_enabled("unknown-plain:7b") is False
        assert cfg.native_tools_enabled("qwen2.5-coder:7b-plain") is True
        assert cfg.native_tools_enabled("mistral-nemo:12b") is False
    finally:
        mp.show_model = orig
        if prev is None:
            os.environ.pop("OTTERCODE_NATIVE_TOOLS", None)
        else:
            os.environ["OTTERCODE_NATIVE_TOOLS"] = prev


def test_native_on_off_ignore_capabilities():
    import os
    from backend import config as cfg
    prev = os.environ.get("OTTERCODE_NATIVE_TOOLS")
    try:
        os.environ["OTTERCODE_NATIVE_TOOLS"] = "on"
        assert cfg.native_tools_enabled("anything-without-tools") is True
        os.environ["OTTERCODE_NATIVE_TOOLS"] = "off"
        assert cfg.native_tools_enabled("qwen2.5-coder:7b") is False
    finally:
        if prev is None:
            os.environ.pop("OTTERCODE_NATIVE_TOOLS", None)
        else:
            os.environ["OTTERCODE_NATIVE_TOOLS"] = prev


def test_timeout_local_readable():
    from backend import ollama as ol
    to = ol.generate_timeout_for("http://127.0.0.1:11434/api/chat")
    assert to[1] >= 900
    assert ol.LAST_GENERATE_TIMEOUT[1] >= 900
