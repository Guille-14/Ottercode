# B1 — benchmark de num_ctx por modelo/máquina (medición real, no README).
from __future__ import annotations

import json
import os
import time
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional

import requests

from backend.config import DEFAULT_MODEL, OLLAMA_BASE_URL, WORKSPACE_ROOT, _ollama_session
from backend.ollama import ensure_gpu_exclusive
from events import SseEvent, sse

BENCH_PATH = WORKSPACE_ROOT / "ctx_bench.json"
CTX_RUNGS = (4096, 8192, 16384, 32768)
BENCH_PREDICT = 100
SPEED_FLOOR = float(os.environ.get("OTTERCODE_CTX_SPEED_FLOOR", "0.8") or "0.8")
_BENCH_PROMPT_UNIT = (
    "OtterCode calibra el contexto de esta GPU. "
    "Responde solo con la palabra OK al final. "
)


def load_ctx_bench() -> Dict[str, Any]:
    try:
        data = json.loads(BENCH_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_ctx_bench(data: Dict[str, Any]) -> None:
    BENCH_PATH.parent.mkdir(parents=True, exist_ok=True)
    BENCH_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def bench_for_model(model: str) -> Optional[Dict[str, Any]]:
    rec = load_ctx_bench().get("models") or {}
    hit = rec.get(model)
    return hit if isinstance(hit, dict) else None


def pick_recommended(steps: List[Dict[str, Any]], floor: float = SPEED_FLOOR) -> Optional[int]:
    """Mayor num_ctx cuya velocidad ≥ floor * velocidad del ctx más pequeño que funcionó."""
    ok = [s for s in steps if s.get("ok") and float(s.get("tps") or 0) > 0]
    if not ok:
        return None
    baseline = float(ok[0]["tps"])
    rec = int(ok[0]["num_ctx"])
    for s in ok:
        tps = float(s["tps"])
        if tps >= baseline * floor:
            rec = int(s["num_ctx"])
        else:
            break
    return rec


def next_rung(current: int, steps: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    cur = int(current or 0)
    higher = [s for s in steps if s.get("ok") and int(s.get("num_ctx") or 0) > cur]
    return higher[0] if higher else None


def _pad_prompt(num_ctx: int) -> str:
    # Deja ~300 tokens para la generación; ~4 chars/token.
    target_chars = max(800, int((num_ctx - 300) * 3.2))
    buf = [_BENCH_PROMPT_UNIT]
    n = 0
    while n < target_chars:
        buf.append(_BENCH_PROMPT_UNIT)
        n += len(_BENCH_PROMPT_UNIT)
    buf.append("\nDi solo: OK\n")
    return "".join(buf)


def _tps(eval_count: Any, eval_duration_ns: Any) -> float:
    try:
        n = float(eval_count or 0)
        d = float(eval_duration_ns or 0) / 1e9
        if d <= 0 or n <= 0:
            return 0.0
        return round(n / d, 2)
    except (TypeError, ValueError):
        return 0.0


def run_ctx_rung(model: str, num_ctx: int, predict: int = BENCH_PREDICT) -> Dict[str, Any]:
    ensure_gpu_exclusive(model)
    started = time.time()
    try:
        resp = _ollama_session.post(
            f"{OLLAMA_BASE_URL}/api/generate",
            json={
                "model": model,
                "prompt": _pad_prompt(num_ctx),
                "stream": False,
                "keep_alive": "5m",
                "options": {
                    "num_gpu": 99,
                    "main_gpu": 0,
                    "num_ctx": int(num_ctx),
                    "num_predict": int(predict),
                    "temperature": 0,
                },
            },
            timeout=(15, 420),
        )
    except requests.RequestException as exc:
        return {
            "ok": False, "num_ctx": num_ctx, "tps": 0.0,
            "error": str(exc)[:240], "ms": int((time.time() - started) * 1000),
        }
    ms = int((time.time() - started) * 1000)
    if resp.status_code != 200:
        detail = (resp.text or f"HTTP {resp.status_code}")[:240]
        return {"ok": False, "num_ctx": num_ctx, "tps": 0.0, "error": detail, "ms": ms}
    try:
        data = resp.json()
    except ValueError:
        return {"ok": False, "num_ctx": num_ctx, "tps": 0.0, "error": "JSON inválido", "ms": ms}
    err = data.get("error")
    if err:
        return {"ok": False, "num_ctx": num_ctx, "tps": 0.0, "error": str(err)[:240], "ms": ms}
    tps = _tps(data.get("eval_count"), data.get("eval_duration"))
    return {
        "ok": tps > 0,
        "num_ctx": num_ctx,
        "tps": tps,
        "eval_count": data.get("eval_count"),
        "eval_duration_ns": data.get("eval_duration"),
        "ms": ms,
    }


def persist_result(model: str, steps: List[Dict[str, Any]], recommended: int, floor: float) -> Dict[str, Any]:
    rec = {
        "model": model,
        "recommended": recommended,
        "floor": floor,
        "steps": steps,
        "ts": datetime.now().isoformat(timespec="seconds"),
        "baseline_tps": next((s.get("tps") for s in steps if s.get("ok")), None),
    }
    data = load_ctx_bench()
    models = data.get("models") if isinstance(data.get("models"), dict) else {}
    models[model] = rec
    data["models"] = models
    save_ctx_bench(data)
    return rec


def stream_benchmark(model: str) -> Iterator[str]:
    """SSE: un peldaño cada vez; para si baja del 80% del más pequeño o falla."""
    model = (model or DEFAULT_MODEL).strip() or DEFAULT_MODEL
    floor = SPEED_FLOOR
    yield sse(SseEvent.system, {
        "text": f"Calibrando num_ctx de {model} (peldaños {', '.join(str(x) for x in CTX_RUNGS)}). "
                f"Umbral: {int(floor * 100)}% de la velocidad en el ctx más pequeño. GPU exclusiva.",
    })
    steps: List[Dict[str, Any]] = []
    baseline: Optional[float] = None
    for ctx in CTX_RUNGS:
        yield sse(SseEvent.system, {"text": f"Midiendo {ctx} tokens de contexto…"})
        step = run_ctx_rung(model, ctx)
        steps.append(step)
        yield sse(SseEvent.system, {
            "text": (
                f"{'OK' if step.get('ok') else 'FALLO'} ctx={ctx}: "
                f"{step.get('tps') or 0} tok/s"
                + (f" — {step.get('error')}" if step.get('error') else "")
            ),
            "ctx_step": step,
        })
        if not step.get("ok"):
            break
        tps = float(step["tps"])
        if baseline is None:
            baseline = tps
        elif tps < baseline * floor:
            break
    recommended = pick_recommended(steps, floor) or (steps[0]["num_ctx"] if steps else 4096)
    rec = persist_result(model, steps, int(recommended), floor)
    # Aplica el techo a los ajustes persistidos (hot-reload).
    try:
        import backend.settings as _s
        _s.save_runtime_settings({"num_ctx": int(recommended)})
    except Exception:
        pass
    yield sse(SseEvent.system, {
        "text": f"Techo recomendado: {recommended} (modelo {model}). Guardado en Ajustes.",
        "ctx_bench": rec,
    })
    yield sse(SseEvent.create_done, {"ok": True, "kind": "ctx_bench", **rec})


def compact_pressure_hint(run: Any) -> Optional[Dict[str, Any]]:
    """B2: si compacta demasiado a menudo, ofrece subir ctx con números reales."""
    hits = int(getattr(run, "_compact_hits", 0) or 0) + 1
    run._compact_hits = hits
    if getattr(run, "_ctx_hint_sent", False):
        return None
    calls = int(getattr(run, "_llm_calls", 0) or 1)
    # 3 compactaciones y al menos compactar ~cada 2 llamadas.
    if hits < 3 or hits * 2 < calls:
        return None
    model = getattr(run, "model", "") or DEFAULT_MODEL
    bench = bench_for_model(model)
    current = int(getattr(run, "num_ctx", 0) or 0)
    nxt = None
    if bench:
        nxt = next_rung(current or int(bench.get("recommended") or 0), list(bench.get("steps") or []))
    run._ctx_hint_sent = True
    payload: Dict[str, Any] = {
        "compact_hits": hits,
        "llm_calls": calls,
        "current_ctx": current,
        "model": model,
        "has_bench": bool(bench),
        "recommended": (bench or {}).get("recommended"),
        "baseline_tps": (bench or {}).get("baseline_tps"),
    }
    if nxt:
        payload["next_ctx"] = nxt.get("num_ctx")
        payload["next_tps"] = nxt.get("tps")
        cur_tps = None
        for s in bench.get("steps") or []:
            if int(s.get("num_ctx") or 0) == current:
                cur_tps = s.get("tps")
        payload["current_tps"] = cur_tps
    return payload
