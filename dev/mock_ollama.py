#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
================================================================================
 OTTERCODE · dev/mock_ollama.py — Ollama simulada (MODO DEMO / SELFTEST)
================================================================================
 Servidor HTTP mínimo que imita la API de Ollama para probar OtterCode de
 punta a punta SIN GPU, SIN modelo descargado y sin consumir VRAM.

   python3 dev/mock_ollama.py [puerto]        # por defecto 11435
   OTTERCODE_OLLAMA=http://localhost:11435 uvicorn backend:app --port 8000

 OTTER_MOCK_REVIEW_FAILS=1 → el Revisor rechaza la primera auditoría (para
 ver el Bucle Infinito de correcciones en acción).

 El mock rotea por marcadores del prompt (mismos que usa el backend):
   - keep_alive == 0                 → flush VRAM (estado PS desciarga)
   - "MISIÓN DEL EQUIPO"              → 🧠 Arquitecto (plan con delegación)
   - "CHAT DIRECTO"                   → chat directo (texto simple)
   - "INVESTIGACIÓN DE CONTEXTO"      → 🔬 Investigador: 1ª = list_dir,
      continuación                     → informe de contexto (texto)
   - "AUDITORÍA" / "REVISOR"          → 🔍 Revisor: 1ª = execute_bash (test),
      continuación                     → "Aprobado" o corrección
   - "MISIÓN — BALSA" / "MODO CORRECCIÓN" → 💻 Programador: write_file
   - "CONTINUACIÓN … PROGRAMADOR"     → 💻 Programador: finalizar
================================================================================
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from typing import Optional

MODEL = "qwen3.8-distill-64k"

REVIEW_SHOULD_FAIL_FIRST = os.environ.get("OTTER_MOCK_REVIEW_FAILS", "") == "1"
REVIEW_STATE = {"audits": 0}
PS_STATE = {"loaded": False}


# ---------------------------------------------------------------------------
# Respuestas de los agentes
# ---------------------------------------------------------------------------

PLAN = (
    "## Objetivo\n"
    "Crear una landing page dark-mode en un único archivo HTML para la "
    "cafetería La Balsa (Villena).\n\n"
    "## Subtareas y delegación\n"
    "1. 🔬 Investigador: comprobar si el workspace ya contiene archivos y su estructura.\n"
    "2. 💻 Programador: crear index.html completo (hero, menú con precios, horarios, footer).\n"
    "3. 🔍 Revisor: auditar semántica y validar que el HTML sea bien formado.\n\n"
    "## Plan de acción\n"
    "1. Estructura semántica (header hero, main con menú, section horarios, footer).\n"
    "2. Estilos inline dark-mode con acentos cyan y tipografía monoespaciada.\n"
    "3. Menú con 4 productos y precios; horarios de la semana.\n"
    "4. Footer con contacto y crédito de la balsa OtterCode.\n"
)

_CONTEXT = (
    "## INFORME DE CONTEXTO\n"
    "### Estado del workspace\n"
    "- El workspace está vacío: no hay archivos previos que colisionen.\n"
    "- No se detecta estructura de proyecto existente.\n"
    "### Archivos relevantes\n"
    "- (ninguno)\n"
    "### Recomendaciones para el Programador\n"
    "1. Crear index.html como único entregable, con CSS inline.\n"
    "2. Mantener la semántica simple y accesible (un solo archivo).\n"
)

_CONTENT = (
    "<!DOCTYPE html>\n"
    '<html lang="es">\n'
    '<head>\n'
    '  <meta charset="UTF-8">\n'
    '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
    "  <title>La Balsa — Cafetería</title>\n"
    "  <style>\n"
    "    body{margin:0;background:#0a0e14;color:#d7e2ee;font-family:ui-monospace,monospace}\n"
    "    .hero{padding:56px 24px;text-align:center}\n"
    "    .hero h1{color:#22d3ee;margin:0;font-size:42px}\n"
    "    .menu{display:flex;gap:16px;justify-content:center;flex-wrap:wrap;padding:24px}\n"
    "    .card{border:1px solid #1e2a3a;border-radius:10px;padding:18px;min-width:180px}\n"
    "    .card h3{color:#22d3ee;margin:0 0 8px}\n"
    "    section{padding:24px;text-align:center}\n"
    "    footer{padding:28px;text-align:center;color:#7d8ea3;border-top:1px solid #1e2a3a}\n"
    "  </style>\n"
    "</head>\n"
    "<body>\n"
    '  <header class="hero">\n'
    "    <h1>🦦 La Balsa</h1>\n"
    "    <p>Cafetería de especialidad · Villena</p>\n"
    "  </header>\n"
    '  <main class="menu">\n'
    '    <div class="card"><h3>Cortado de balsa</h3><p>2,50 €</p></div>\n'
    '    <div class="card"><h3>Flat white</h3><p>3,20 €</p></div>\n'
    '    <div class="card"><h3>Chai de nutria</h3><p>3,80 €</p></div>\n'
    '    <div class="card"><h3>Brownie de concha</h3><p>4,00 €</p></div>\n'
    "  </main>\n"
    "  <section>\n"
    "    <h2>Horarios</h2>\n"
    "    <p>Lun–Vie 8:00–20:00 · Sáb 9:00–18:00</p>\n"
    "  </section>\n"
    "  <footer>Hecho por la balsa OtterCode 🦦</footer>\n"
    "</body>\n"
    "</html>"
)

CORRECTION = (
    "=== ARCHIVO: index.html ===\n"
    "Corrección: el <head> debe incluir el meta viewport.\n"
    "<head>\n"
    '  <meta charset="UTF-8">\n'
    '  <meta name="viewport" content="width=device-width, initial-scale=1.0">\n'
    "</head>"
)

CHAT_REPLY = (
    "¡Hola, cabo! Modo chat directo activado. Puedo inspeccionar el workspace, "
    "escribir archivos o ejecutar tests según me pidas. ¿Qué construimos hoy?"
)

# --- Fábrica de Agentes Dinámicos (perfiles generados por "la IA") ---------

_FACTORY_SQL = {
    "nombre": "El SQL Maestro",
    "rol": "Optimización de consultas SQL lentas",
    "system_prompt": (
        "Eres El SQL Maestro, especialista en diagnosticar y optimizar consultas "
        "SQL lentas de la balsa OtterCode. Tu objetivo es identificar cuellos de "
        "botella (índices faltantes, patrón N+1, scans completos) y dejar por "
        "escrito la versión optimizada de cada consulta.\n"
        "REGLA DE HIERRO: NO modifiques datos ni esquemas (prohibido DROP, "
        "DELETE o ALTER). NO inventes estadísticas: inspecciona el workspace con "
        "tree/list_dir y lee los archivos con read_file. Trabaja con tus skills, "
        "deja tu informe en un archivo si tienes write_file y cierra SIEMPRE tu "
        "turno emitindo {\"tool\": \"finalizar\", \"arguments\": {\"resumen\": \"...\"}}."
    ),
    "icon": "🗄️",
    "color_neon": "#ff9f43",
    "tools_disponibles": ["read_file", "list_dir", "tree", "execute_bash", "write_file"],
}

_FACTORY_SEC = {
    "nombre": "La Guardián de Seguridad",
    "rol": "Auditoría de seguridad ofensiva",
    "system_prompt": (
        "Eres La Guardián de Seguridad, experta en red team y auditoría de "
        "seguridad de la balsa OtterCode. Buscas inyecciones, path traversal, "
        "exposición de secretos y comandos peligrosos.\n"
        "REGLA DE HIERRO: NO modifiques ningún archivo: tu misión es detectar y "
        "documentar, nunca reescribir. NO ejecutes comandos destructivos. "
        "Cierra SIEMPRE tu turno emitindo finalizar con el resumen de riesgos."
    ),
    "icon": "🔐",
    "color_neon": "#f368e0",
    "tools_disponibles": ["read_file", "list_dir", "tree", "execute_bash"],
}

_FACTORY_GENERIC = {
    "nombre": "El Especialista",
    "rol": "Análisis de dominio a medida",
    "system_prompt": (
        "Eres El Especialista, analista de dominio de la balsa OtterCode "
        "desplegado para una misión concreta. Tu objetivo es producir un informe "
        "de análisis accionable para el Programador.\n"
        "REGLA DE HIERRO: NO salgas de tu especialidad ni regeneres archivos "
        "ajenos a tu cometido. NO inventes datos del workspace. Cierra SIEMPRE "
        "tu turno emitindo finalizar con el resumen que pasará al Programador."
    ),
    "icon": "🧪",
    "color_neon": "#54a0ff",
    "tools_disponibles": ["read_file", "list_dir", "tree"],
}




def _tool_json(tool: str, arguments: dict) -> str:
    return (
        "```json\n"
        + json.dumps({"tool": tool, "arguments": arguments}, ensure_ascii=False)
        + "\n```\n"
    )


RESEARCHER_FIRST = _tool_json("list_dir", {"path": "."})
REVIEWER_FIRST = _tool_json("execute_bash", {"cmd": "ls -la && echo TESTS-RUN"})
DEV_FIRST = _tool_json("write_file", {"filepath": "index.html", "content": _CONTENT})
DEV_FINISH = _tool_json("finalizar", {"resumen": "index.html creado con la landing completa."})

# --- Especialista dinámico inyectado en la cadena (meta-orquestación) -------

EXPERT_REPORT = (
    "# INFORME DEL ESPECIALISTA\n\n"
    "## Análisis\n"
    "- El workspace está preparado para la implementación de la misión.\n"
    "- Punto crítico detectado: el entregable debe mantener la semántica simple "
    "y accesible (un solo archivo, sin dependencias).\n\n"
    "## Recomendaciones para la implementación\n"
    "1. Prioriza la estructura semántica sobre el CSS decorativo.\n"
    "2. Añade los metadatos esenciales en el <head>.\n"
    "3. Verifica el resultado con un test de validación básico.\n"
)

EXPERT_FIRST = (
    "Reviso el contexto y dejo por escrito mi informe para el Programador.\n"
    + _tool_json(
        "write_file",
        {"filepath": "informe_especialista.md", "content": EXPERT_REPORT},
    )
)

EXPERT_CONT = "## INFORME DEL ESPECIALISTA\n\n" + EXPERT_REPORT


def _factory_profile(prompt: str) -> str:
    """Elige el perfil de la Fábrica según la descripción del usuario."""
    m = re.search(r"\[DESCRIPCIÓN DEL USUARIO\]\n(.*?)(?:\n\n|$)", prompt, re.DOTALL)
    desc = (m.group(1) if m else "").lower()
    if "sql" in desc or "consulta" in desc or "base de datos" in desc or "db" in desc:
        return json.dumps(_FACTORY_SQL, ensure_ascii=False, indent=2)
    if "segur" in desc or "hacker" in desc or "vulnerab" in desc:
        return json.dumps(_FACTORY_SEC, ensure_ascii=False, indent=2)
    return json.dumps(_FACTORY_GENERIC, ensure_ascii=False, indent=2)


def _route_text(text: str) -> Optional[str]:
    """Enruta por marcadores de un texto (orden de prioridad)."""
    if "COMPLETACIÓN OBLIGATORIA" in text:
        return "dev_continue"
    if "CORRECCIONES DEL REVISOR" in text:
        return "developer"
    if "CREACIÓN DE AGENTE DINÁMICO" in text:
        return "factory"
    if "COMISIÓN DEL ESPECIALISTA" in text:
        return "expert_cont" if "CONTINUACIÓN" in text else "expert"
    if "MISIÓN DEL EQUIPO" in text:
        return "architect_inject" if re.search(r"dyn-[0-9a-f]{6}", text) else "architect"
    if "AGENTE OTTER" in text:
        return "dev_continue" if "CONTINUACIÓN" in text else "developer"
    if "CHAT DIRECTO" in text:
        return "chat"
    if "CONTINUACIÓN DE TU TURNO — EL INVESTIGADOR" in text:
        return "researcher_cont"
    if "CONTINUACIÓN DE TU TURNO — EL REVISOR" in text:
        return "reviewer_cont"
    if "CONTINUACIÓN DE TU TURNO — EL PROGRAMADOR" in text:
        return "dev_continue"
    if "INVESTIGACIÓN DE CONTEXTO" in text:
        return "researcher"
    if "AUDITORÍA" in text:
        return "reviewer"
    if "MODO CORRECCIÓN" in text or "MISIÓN — BALSA OTTERCODE" in text:
        return "developer"
    return None


def route(body: dict) -> Optional[str]:
    prompt = body.get("prompt", "")
    if body.get("keep_alive") == 0:
        return "flush"
    if not prompt and body.get("messages"):
        msgs = body["messages"]
        # Buscar marcador más reciente
        found = None
        for msg in reversed(msgs):
            k = _route_text(msg.get("content", ""))
            if k:
                found = k
                break
        if found is None:
            return None
        # Si el ÚLTIMO mensaje NO tiene marcador → es continuation (tool result)
        last_k = _route_text(msgs[-1].get("content", ""))
        if last_k is None:
            _cont = {
                "developer": "dev_continue",
                "reviewer": "reviewer_cont",
                "researcher": "researcher_cont",
                "expert": "expert_cont",
            }
            return _cont.get(found, found)
        return found
    return _route_text(prompt)


def chunked(text: str, size: int = 14):
    return [text[i : i + size] for i in range(0, len(text), size)]


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # silencio
        pass

    def _send_json(self, obj: dict, code: int = 200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _stream(self, text: str, chat: bool = False):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for piece in chunked(text):
            if chat:
                line = json.dumps(
                    {"model": MODEL, "message": {"role": "assistant",
                     "content": piece}, "done": False}, ensure_ascii=False)
            else:
                line = json.dumps(
                    {"model": MODEL, "response": piece, "done": False},
                    ensure_ascii=False)
            self.wfile.write((line + "\n").encode("utf-8"))
            self.wfile.flush()
            time.sleep(0.004)
        final = json.dumps(
            {
                "model": MODEL,
                "response": "",
                "done": True,
                "eval_count": max(1, len(text) // 4),
                "eval_duration": 1_200_000_000,
                "total_duration": 1_600_000_000,
            }
        )
        self.wfile.write((final + "\n").encode("utf-8"))
        self.wfile.flush()

    def _stream_chat(self, text: str):
        """Stream NDJSON en formato /api/chat (message.content)."""
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for piece in chunked(text):
            line = json.dumps(
                {"model": MODEL,
                 "message": {"role": "assistant", "content": piece},
                 "done": False},
                ensure_ascii=False,
            )
            self.wfile.write((line + "\n").encode("utf-8"))
            self.wfile.flush()
            time.sleep(0.004)
        final = json.dumps(
            {
                "model": MODEL,
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "eval_count": max(1, len(text) // 4),
                "eval_duration": 1_200_000_000,
                "total_duration": 1_600_000_000,
            }
        )
        self.wfile.write((final + "\n").encode("utf-8"))
        self.wfile.flush()

    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/tags"):
            self._send_json({"models": [{"name": MODEL, "size": 4_100_000_000}]})
        elif self.path.startswith("/api/version"):
            self._send_json({"version": "0.12.9-otter-mock"})
        elif self.path.startswith("/api/ps"):
            if PS_STATE["loaded"]:
                expires = (
                    datetime.now(timezone.utc) + timedelta(minutes=5)
                ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                self._send_json(
                    {"models": [{"name": MODEL, "size_vram": 4_100_000_000,
                                 "size_ram": 0, "expires_at": expires}]}
                )
            else:
                self._send_json({"models": []})
        else:
            self._send_json({"error": "ruta no encontrada"}, 404)

    # --- Gestión de modelos (paridad CLI Ollama para el modo demo) ----------

    def _stream_ndjson(self, lines: list):
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.end_headers()
        for obj in lines:
            self.wfile.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
            self.wfile.flush()
            time.sleep(0.01)

    def _mock_pull(self, model: str):
        total = 4_100_000_000
        lines = [{"status": "pulling manifest"}]
        for pct in (5, 15, 30, 50, 75, 90, 100):
            lines.append({"status": f"downloading {model}",
                          "digest": f"sha256:{pct:064d}",
                          "total": total,
                          "completed": int(total * pct / 100)})
        lines.append({"status": "verifying sha256 digest"})
        lines.append({"status": "writing manifest"})
        lines.append({"status": "success"})
        self._stream_ndjson(lines)

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        try:
            return json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            return {}

    def do_DELETE(self):  # noqa: N802
        body = self._read_body()
        if self.path.startswith("/api/delete"):
            model = body.get("model", "")
            if not model or model == MODEL:
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()
            else:
                self._send_json({"error": f"modelo no encontrado: {model}"}, 404)
        else:
            self._send_json({"error": "ruta no encontrada"}, 404)

    def do_POST(self):  # noqa: N802
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except json.JSONDecodeError:
            body = {}
        if self.path.startswith("/api/pull"):
            self._mock_pull(body.get("model", MODEL))
        elif self.path.startswith("/api/create"):
            model = body.get("model", "modelo-nuevo")
            if not body.get("modelfile"):
                self._send_json({"error": "modelfile requerido"}, 400)
            else:
                self._stream_ndjson([
                    {"status": f"reading model metadata"},
                    {"status": f"gathering model components"},
                    {"status": "creating layer sha256:mockabc"},
                    {"status": f"writing manifest"},
                    {"status": "success", "model": model},
                ])
        elif self.path.startswith("/api/copy"):
            src, dst = body.get("source", ""), body.get("destination", "")
            if src and dst:
                self.send_response(200); self.send_header("Content-Length", "0"); self.end_headers()
            else:
                self._send_json({"error": "source y destination requeridos"}, 400)
        elif self.path.startswith("/api/show"):
            model = body.get("model", MODEL)
            if model in (MODEL,) or model.startswith(("qwen", "llama")) or ":latest" in model:
                self._send_json({
                    "license": "MIT License (mock)",
                    "modelfile": f'# Modelfile de {model}\nFROM {MODEL}\nPARAMETER temperature 0.7',
                    "parameters": "stop                           \"<|im_end|>\"\ntemperature                    0.7",
                    "template": "{{ if .System }}<|im_start|>system\n{{ .System }}<|im_end|>\n{{ end }}...",
                    "system": "",
                    "details": {
                        "parent_model": "",
                        "format": "gguf",
                        "family": "qwen2",
                        "families": ["qwen2"],
                        "parameter_size": "8.0B",
                        "quantization_level": "Q4_K_M",
                    },
                })
            else:
                self._send_json({"error": f"modelo no encontrado: {model!r}"}, 404)
        elif self.path.startswith("/api/embed"):
            self._send_json({
                "model": body.get("model", MODEL),
                "embeddings": [[0.001 * ((i * 37) % 100 - 50) for i in range(16)]],
            })
        elif self.path.startswith("/api/generate") or self.path.startswith("/api/chat"):
            # v6.0 · Fase 1 · FIX: /api/chat usa el MISMO dispatch que
            # /api/generate (antes tenía un mini-dispatch que solo miraba el
            # último mensaje user → todo acababa en el fallback genérico).
            if self.path.startswith("/api/chat"):
                kind = route({"messages": body.get("messages", [])})
            else:
                kind = route(body)
            _chat_mode = self.path.startswith("/api/chat")
            if kind is None:                     # prompt sin marcador → genérico
                kind = "chat"
            PS_STATE["loaded"] = kind != "flush"
            if kind == "flush":
                self._send_json(
                    {"model": MODEL, "response": "", "done": True, "eval_count": 0}
                )
            elif kind == "architect":
                _dyn = re.findall(r"(dyn-[0-9a-f]{6})", body.get("prompt", "")
                                  or "\n".join(m.get("content", "") for m in body.get("messages", [])))
                _inject = ""
                if _dyn:
                    _inject = (
                        "\n\n```json\n"
                        f'{{"inject_agents": ["{_dyn[0]}"], "reason": '
                        '"La misión se beneficia del experto disponible en la balsa"}\n'
                        "```\n"
                    )
                self._stream(PLAN + _inject, chat=_chat_mode)
            elif kind == "architect_inject":
                _ai_prompt = body.get("prompt", "") or "\n".join(
                    m.get("content", "") for m in body.get("messages", []))
                dyn_id = re.search(r"dyn-[0-9a-f]{6}", _ai_prompt).group(0)
                inject = (
                    "\n\n```json\n"
                    f'{{"inject_agents": ["{dyn_id}"], "reason": '
                    '"La misión se beneficia del experto disponible en la balsa"}\n'
                    "```\n"
                )
                self._stream(PLAN + inject, chat=_chat_mode)
            elif kind == "factory":
                _fp = body.get("prompt", "") or "\n".join(
                    m.get("content", "") for m in body.get("messages", []))
                profile = _factory_profile(_fp)
                self._stream(
                    "Analizando la descripción del especialista…\n"
                    "```json\n" + profile + "\n```\n"
                    "✓ Perfil técnico generado."
                , chat=_chat_mode)
            elif kind == "expert":
                self._stream(EXPERT_FIRST, chat=_chat_mode)
            elif kind == "expert_cont":
                self._stream(EXPERT_CONT, chat=_chat_mode)
            elif kind == "chat":
                self._stream(CHAT_REPLY, chat=_chat_mode)
            elif kind == "researcher":
                self._stream(RESEARCHER_FIRST, chat=_chat_mode)
            elif kind == "researcher_cont":
                self._stream(_CONTEXT, chat=_chat_mode)
            elif kind == "reviewer":
                REVIEW_STATE["audits"] += 1
                self._stream(REVIEWER_FIRST, chat=_chat_mode)
            elif kind == "reviewer_cont":
                if REVIEW_SHOULD_FAIL_FIRST and REVIEW_STATE["audits"] == 1:
                    self._stream(CORRECTION, chat=_chat_mode)
                else:
                    self._stream("Aprobado", chat=_chat_mode)
            elif kind == "dev_continue":
                self._stream(DEV_FINISH, chat=_chat_mode)
            elif kind == "developer":
                self._stream(DEV_FIRST, chat=_chat_mode)
            else:
                self._send_json({"error": f"Unknown route kind: {kind}"}, 400)
        else:
            self._send_json({"error": "ruta no encontrada"}, 404)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 11435
    print(f"🦦 Mock Ollama escuchando en http://localhost:{port}  (Ctrl+C para salir)")
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
