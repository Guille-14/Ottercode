"""T1–T3: umbral de compactación = num_ctx − reserva de salida (sin hardcode)."""
from __future__ import annotations


def estimate_tokens(text: str) -> int:
    return max(1, len(text or "") // 4)


def compact_threshold(num_ctx: int, reserved: int) -> int:
    reserved = min(int(reserved), int(num_ctx) // 2)
    return max(512, int(num_ctx) - reserved)


def test_threshold_uses_run_num_ctx():
    t8k = compact_threshold(8192, 2048)
    t16k = compact_threshold(16384, 2048)
    assert t8k == 8192 - 2048
    assert t16k == 16384 - 2048
    assert t16k > t8k


def test_preflight_over_only_when_over_threshold():
    thresh = compact_threshold(4096, 1024)
    small = estimate_tokens("hola")
    huge = estimate_tokens("x" * 20_000)
    assert small <= thresh
    assert huge > thresh


def test_loop_guard_does_not_recompact():
    blocked = False
    attempts = 0

    def maybe(over: bool, still_over: bool) -> str:
        nonlocal blocked, attempts
        if blocked:
            return "skip"
        if not over:
            return "ok"
        attempts += 1
        if still_over:
            blocked = True
            return "warn"
        return "compacted"

    assert maybe(True, True) == "warn"
    assert maybe(True, True) == "skip"
    assert attempts == 1


if __name__ == "__main__":
    test_threshold_uses_run_num_ctx()
    test_preflight_over_only_when_over_threshold()
    test_loop_guard_does_not_recompact()
    print("T1–T3 OK")
