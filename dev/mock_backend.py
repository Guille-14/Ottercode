#!/usr/bin/env python3
"""Mock del orquestador OtterCode (API + SSE) — solo para E2E.

  python3 dev/mock_backend.py [puerto]   (default 11522)

Habla el subconjunto que consume la UI super-final:
  /api/status /api/models /api/agents /api/agents/create (SSE)
  /api/task (SSE) · /api/task/{id}/abort · /api/skill · /api/tree
  /api/task/{id}/zip · /api/ps · /api/flush · /_test/requests
La misión que contenga "ESPERA" se mantiene en streaming hasta que llega
el abort (para probar el botón ■ ABORTAR).
"""
from pathlib import Path
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

# Importamos el Enum centralizado para coherencia
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from events import SseEvent, sse

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 11522
TASK_ID = "20260822-000000-mock001"
ABORTED = set()
REQUESTS = []
LOCK = threading.Lock()

DYN = {
    "id": "dyn-647c26", "nombre": "El SQL Maestro", "rol": "Especialista SQL",
    "system_prompt": "REGLA DE HIERRO: solo SQL.", "icon": "🗄️",
    "color_neon": "#ff9f43", "tools_disponibles": ["write_file", "read_file", "finalizar"],
    "dynamic": True, "created_in": "dyn", "readonly": False,
}
CORE = [
    {"id": "architect", "nombre": "El Arquitecto", "rol": "Planificación", "system_prompt": "", "icon": "🧠", "color_neon": "#00e5ff", "tools_disponibles": [], "dynamic": False, "created_in": "core", "readonly": True},
    {"id": "researcher", "nombre": "El Investigador", "rol": "Contexto", "system_prompt": "", "icon": "🔬", "color_neon": "#a3ff5c", "tools_disponibles": ["tree", "read_file", "list_dir"], "dynamic": False, "created_in": "core", "readonly": True},
    {"id": "developer", "nombre": "El Programador", "rol": "Implementación", "system_prompt": "", "icon": "💻", "color_neon": "#ffbe4d", "tools_disponibles": ["write_file", "read_file", "tree"], "dynamic": False, "created_in": "core", "readonly": False},
    {"id": "reviewer", "nombre": "El Revisor", "rol": "QA", "system_prompt": "", "icon": "🔍", "color_neon": "#f368e0", "tools_disponibles": ["execute_bash", "read_file"], "dynamic": False, "created_in": "core", "readonly": True},
]
AGENTS = CORE + [DYN]  # mutable: la Fábrica añade aquí


def sse(event: SseEvent, data):
    return f"event: {event.name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _read_body(self):
        n = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(n) or b"{}"
        try:
            return json.loads(raw)
        except Exception:
            return {}

    def _stream(self, chunks):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self._cors()
        self.end_headers()
        for c in chunks:
            if c is None:  # señal: esperar abort
                for _ in range(60):
                    if TASK_ID in ABORTED:
                        self.wfile.write(sse(SseEvent.task_aborted, {"task_id": TASK_ID}).encode())
                        self.wfile.flush()
                        return
                    time.sleep(0.1)
            else:
                self.wfile.write(c.encode())
                self.wfile.flush()
                time.sleep(0.01)

    def do_GET(self):  # noqa: N802
        u = urlparse(self.path)
        p = u.path
        q = parse_qs(u.query)
        if p == "/api/status":
            self._json({"ok": True, "version": "2.9.9-mock",
                        "ollama": {"ok": True, "url": "mock", "models": ["qwen3.8-distill-64k"], "models_count": 1},
                        "default_model": "qwen3.8-distill-64k", "running": False,
                        "workspace": "/tmp/mockws", "max_review_rounds": 25,
                        "dynamic_agents": 1, "api": "ollama"})
        elif p == "/api/models":
            self._json({"ollama_ok": True, "models": ["qwen3.8-distill-64k"]})
        elif p == "/api/agents":
            with LOCK:
                self._json({"agents": AGENTS})
        elif p == "/api/ps":
            self._json({"models": [{"name": "qwen3.8-distill-64k", "model": "qwen3.8-distill-64k",
                                    "size": 4700000000, "size_vram": 4300000000,
                                    "details": {"parameter_size": "7.6B", "quantization_level": "Q4_K_M"},
                                    "processor": "GPU", "expires_at": time.time() + 300}]})
        elif p == "/api/tree":
            self._json({"ok": True, "task_id": TASK_ID, "path": ".",
                        "tree": "📁 .\n├── 📄 index.html (1325 B)\n└── 📄 informe.md (453 B)"})
        elif re.fullmatch(r"/api/task/[\w\-]+/zip", p):
            body = b"PK\x03\x04MOCKZIPDATA"
            self.send_response(200)
            self.send_header("Content-Type", "application/zip")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", 'attachment; filename="ottercode-mock.zip"')
            self._cors()
            self.end_headers()
            self.wfile.write(body)
        elif p == "/_test/requests":
            with LOCK:
                self._json({"requests": REQUESTS})
        else:
            self._json({"error": "not found"}, 404)

    def do_POST(self):  # noqa: N802
        u = urlparse(self.path)
        p = u.path
        body = self._read_body()
        with LOCK:
            REQUESTS.append({"path": p, "body": body})

        if p == "/api/flush":
            self._json({"ok": True, "ms": 10, "detail": "ok"})
            return

        if p == "/api/skill":
            self._json({"ok": True, "output": "<html>mock</html>", "ms": 4,
                        "title": "read_file", "task_id": TASK_ID})
            return

        if re.fullmatch(r"/api/task/[\w\-]+/abort", p):
            ABORTED.add(TASK_ID)
            self._json({"ok": True})
            return

        if p == "/api/agents/create":
            new_agent = dict(DYN, id="dyn-mock99", nombre="El Mock Maestro", rol="Pruebas E2E", icon="🧪")
            with LOCK:
                AGENTS.append(new_agent)
            self._stream([
                sse(SseEvent.agent_create_start, {"session_id": "s-mock-1"}),
                sse(SseEvent.vram_flush, {"ok": True, "ms": 8}),
                sse(SseEvent.token, {"token": "Generando "}),
                sse(SseEvent.token, {"token": "perfil "}),
                sse(SseEvent.token, {"token": "técnico…"}),
                sse(SseEvent.agent_created, {"agent": new_agent, "session_id": "s-mock-1"}),
            ])
            return

        if p == "/api/task":
            task = (body.get("task") or "")
            if "ESPERA" in task:
                self._stream([
                    sse(SseEvent.session_id, {"task_id": TASK_ID}),
                    sse(SseEvent.task_start, {"task_id": TASK_ID, "task": task, "model": body.get("model", "m"),
                                       "loop_mode": body.get("loop_mode", False), "mode": "chain",
                                       "start_agent": body.get("start_agent", "architect"),
                                       "workspace": TASK_ID, "max_rounds": 25}),
                    sse(SseEvent.agent_start, {"agent": "architect", "iteration": 1, "name": CORE[0]["nombre"], "icon": CORE[0]["icon"], "role": CORE[0]["rol"]}),
                    sse(SseEvent.vram_flush, {"agent": "architect", "ok": True, "ms": 11}),
                    None,  # esperar abort
                ])
                return

            def ag(aid, name, icon, role, **extra):
                return sse(SseEvent.agent_start, {"agent": aid, "iteration": 1, "name": name, "icon": icon, "role": role, **extra})

            self._stream([
                sse(SseEvent.session_id, {"task_id": TASK_ID}),
                sse(SseEvent.task_start, {"task_id": TASK_ID, "task": task, "model": body.get("model", "m"),
                                   "loop_mode": body.get("loop_mode", False), "mode": "chain",
                                   "start_agent": body.get("start_agent", "architect"),
                                   "workspace": TASK_ID, "max_rounds": 25}),
                ag("architect", "El Arquitecto", "🧠", "Planificación"),
                sse(SseEvent.vram_flush, {"agent": "architect", "ok": True, "ms": 12}),
                sse(SseEvent.token, {"agent": "architect", "token": "Plan: "}),
                sse(SseEvent.token, {"agent": "architect", "token": "1) contexto 2) código 3) QA."}),
                sse(SseEvent.agent_end, {"agent": "architect", "iteration": 1}),
                sse(SseEvent.delegate, {"from": "architect", "to": "researcher", "text": "🧠 → Delegando a 🔬 el Investigador (análisis de contexto)"}),
                ag("researcher", "El Investigador", "🔬", "Contexto"),
                sse(SseEvent.vram_flush, {"agent": "researcher", "ok": True, "ms": 9}),
                sse(SseEvent.tool_call, {"id": "tc-1", "agent": "researcher", "iteration": 1, "tool": "tree", "args": {"path": "."}, "title": "tree(·)"}),
                sse(SseEvent.tool_result, {"id": "tc-1", "tool": "tree", "ok": True, "output": "📁 . (vacío)", "ms": 3}),
                sse(SseEvent.token, {"agent": "researcher", "token": "Contexto analizado: **nada previo**."}),
                sse(SseEvent.agent_end, {"agent": "researcher", "iteration": 1}),
                sse(SseEvent.agent_injected, {"agent": DYN, "position": "after_context", "reason": "el brief pide SQL"}),
                ag("dyn-647c26", "El SQL Maestro", "🗄️", "Especialista SQL", dynamic=True, color_neon="#ff9f43"),
                sse(SseEvent.vram_flush, {"agent": "dyn-647c26", "ok": True, "ms": 7}),
                sse(SseEvent.tool_call, {"id": "tc-2", "agent": "dyn-647c26", "iteration": 1, "tool": "write_file", "args": {"path": "informe.md", "content": "…"}, "title": "write_file(informe.md)"}),
                sse(SseEvent.tool_result, {"id": "tc-2", "tool": "write_file", "ok": True, "output": "informe.md (453 B)", "ms": 2}),
                sse(SseEvent.token, {"agent": "dyn-647c26", "token": "Informe SQL listo."}),
                sse(SseEvent.agent_end, {"agent": "dyn-647c26", "iteration": 1}),
                ag("developer", "El Programador", "💻", "Implementación"),
                sse(SseEvent.vram_flush, {"agent": "developer", "ok": True, "ms": 6}),
                sse(SseEvent.tool_call, {"id": "tc-3", "agent": "developer", "iteration": 1, "tool": "write_file", "args": {"path": "index.html", "content": "<html>…"}, "title": "write_file(index.html)"}),
                sse(SseEvent.tool_result, {"id": "tc-3", "tool": "write_file", "ok": True, "output": "index.html (1325 B)", "ms": 2}),
                sse(SseEvent.token, {"agent": "developer", "token": "Código escrito y verificado."}),
                sse(SseEvent.agent_end, {"agent": "developer", "iteration": 1}),
                ag("reviewer", "El Revisor", "🔍", "QA"),
                sse(SseEvent.vram_flush, {"agent": "reviewer", "ok": True, "ms": 5}),
                sse(SseEvent.tool_call, {"id": "tc-4", "agent": "reviewer", "iteration": 1, "tool": "execute_bash", "args": {"command": "python3 -m py_compile index.html"}, "title": "execute_bash(python3 -m py_compile index.html)"}),
                sse(SseEvent.tool_result, {"id": "tc-4", "tool": "execute_bash", "ok": True, "output": "compila ✓", "ms": 41}),
                sse(SseEvent.token, {"agent": "reviewer", "token": "Veredicto: **Aprobado** — compila y cumple el brief."}),
                sse(SseEvent.agent_end, {"agent": "reviewer", "iteration": 1}),
                sse(SseEvent.task_done, {"task_id": TASK_ID, "mode": "chain", "approved": True, "iterations": 1,
                                  "files": [{"path": "index.html", "size": 1325}, {"path": "informe.md", "size": 453}],
                                  "duration_s": 12.3, "review_verdict": "Aprobado: el código compila y cumple el brief.",
                                  "injected_agents": ["dyn-647c26"]}),
            ])
            return

        self._json({"error": "not found"}, 404)


if __name__ == "__main__":
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), H)
    print(f"mock-ottercode-backend listo en http://127.0.0.1:{PORT}", flush=True)
    srv.serve_forever()
