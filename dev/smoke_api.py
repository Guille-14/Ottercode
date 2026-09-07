"""Smoke profundo sobre la API: detecta NameError/errores de módulo sin esperar
al selftest completo. Usa el backend en :8099 (con mock Ollama en :11499).
Parada ante el primer 5xx, cosindome el detalle del error."""
# ruff: noqa: E501
import json
import re
import sys
import urllib.request

API = "http://127.0.0.1:8099"


def call(method, path, payload=None):
    url = API + path
    req = urllib.request.Request(url, method=method)
    if payload is not None:
        req.add_header("Content-Type", "application/json")
        req.data = json.dumps(payload).encode()
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            body = r.read().decode("utf-8", "replace")
            return r.status, body
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def sse_events(stream):
    types = []
    for m in re.finditer(r'event: (\S+)\n(?:data: (.*?)\n\n)', stream, re.S):
        typ, data = m.group(1), m.group(2)
        types.append(typ)
        if typ in ("agent_create_error", "task_error", "error"):
            print(f"      !! {typ}: {data[:300]}")
    return types


def main():
    fails = []
    checks = []

    def check(label, cond, detail=""):
        checks.append((label, cond))
        if not cond:
            fails.append(label)
        print(f"  [{'OK ' if cond else 'NG '}] {label}" + (f"  {detail}" if detail and not cond else ""))

    # 1. factory de agentes (SSE)
    st, body = call("POST", "/api/agents/create", {
        "description": "Especialista en frontends accesibles con React, diseno limpio en tonos claros. Sabe CSS, accesibilidad y componentes.",
        "model": "qwen3:4b"})
    ev = sse_events(body)
    check("factory create", st == 200 and "agent_created" in ev and "agent_create_error" not in ev, str(ev))

    # 2. registro creció
    st, body = call("GET", "/api/agents")
    try:
        n = len(json.loads(body).get("agents", []))
    except Exception:
        n = -1
    check("agents registro creció", st == 200 and n >= 25, f"n={n}")

    # 3. misión en cadena con bucle (SSE)
    st, body = call("POST", "/api/task", {"task": "smoke landing", "model": "qwen3:4b", "loop_mode": True})
    ev = sse_events(body)
    m = re.search(r'event: session_id\ndata: \{"task_id": "([0-9]+-[0-9]+-[a-f0-9]+)"', body)
    task_id = m.group(1) if m else ""
    check("mision completa", st == 200 and "task_done" in ev and "task_error" not in ev, str(ev))

    # 4. tree/workspace/zip tras la misión
    st, body = call("GET", "/api/tree")
    check("tree", st == 200 and "index.html" in body, body[:80] if st != 200 else "")
    st, body = call("GET", f"/api/task/{task_id}/zip") if task_id else (404, "")
    check("zip", st == 200, f"st={st} task_id={task_id}")

    # 5. chat (1 turno developer)
    st, body = call("POST", "/api/task", {"task": "hola", "model": "qwen3:4b", "mode": "chat"})
    ev = sse_events(body)
    check("chat", st == 200 and "task_done" in ev and "task_error" not in ev, str(ev))

    # 6. skills toggle + read_file
    st, body = call("POST", "/api/skills/enable", {"name": "base64_code", "enable": False})
    check("skill off", st == 200 and json.loads(body).get("enabled") is False, body[:120])
    st, body = call("POST", "/api/skills/enable", {"name": "base64_code", "enable": True})
    check("skill on", st == 200 and json.loads(body).get("enabled") is True, body[:120])

    # 7. perfiles
    st, body = call("GET", "/api/profiles")
    check("profiles", st == 200, body[:80])

    # 8. historial
    st, body = call("GET", "/api/history")
    check("history", st == 200, body[:80])

    # 9. health/status/pulse/activity/ps
    for p in ("/api/status", "/api/pulse", "/api/activity", "/api/ps", "/api/skills"):
        st, body = call("GET", p)
        check(f"GET {p}", st == 200, f"st={st}")

    print(f"\n  → {len(checks) - len(fails)}/{len(checks)} OK")
    if fails:
        print("  FALLOS:", fails)
        sys.exit(1)


if __name__ == "__main__":
    main()