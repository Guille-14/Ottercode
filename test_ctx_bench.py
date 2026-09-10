"""B1/B2: techo de num_ctx y presión de compactación (sin GPU)."""
from backend.ctx_bench import compact_pressure_hint, next_rung, pick_recommended


def test_pick_recommended_80_percent():
    steps = [
        {"ok": True, "num_ctx": 4096, "tps": 40.0},
        {"ok": True, "num_ctx": 8192, "tps": 36.0},  # 90%
        {"ok": True, "num_ctx": 16384, "tps": 30.0},  # 75% — corta
        {"ok": True, "num_ctx": 32768, "tps": 10.0},
    ]
    assert pick_recommended(steps, 0.8) == 8192


def test_next_rung():
    steps = [
        {"ok": True, "num_ctx": 4096, "tps": 40},
        {"ok": True, "num_ctx": 8192, "tps": 32},
    ]
    n = next_rung(4096, steps)
    assert n and n["num_ctx"] == 8192
    assert next_rung(8192, steps) is None


class _R:
    pass


def test_compact_pressure_once():
    run = _R()
    run._llm_calls = 3
    run.num_ctx = 4096
    run.model = "x"
    assert compact_pressure_hint(run) is None  # hits=1
    run._llm_calls = 4
    assert compact_pressure_hint(run) is None  # hits=2
    run._llm_calls = 5
    hint = compact_pressure_hint(run)
    assert hint and hint["compact_hits"] == 3
    assert compact_pressure_hint(run) is None  # cooldown
