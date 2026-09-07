# OtterCode Neo

Orquestador **local** de agentes autónomos estilo Arena AI: Ollama + MCP + RAG sobre FastAPI y un frontend React/Vite con tema oscuro/claro. Los agentes razonan, escriben archivos con herramientas y los resultados afloran en un **panel de artefactos** en vivo.

## Quickstart

```bash
# Backend
python -m venv .venv && source .venv/bin/activate
pip install -e .

# Frontend
npm install
npm run build        # emite el bundle en static/
npm run mobile       # sincroniza la PWA (frontend/mobile/index.html)
```

Arranque (Ollama debe estar corriendo en `127.0.0.1:11434`):

```bash
.venv/bin/python -m uvicorn backend:app --host 127.0.0.1 --port 8099
```

Abre `http://127.0.0.1:8099`.

## Características

- **Agentes autónomos**: planificador, investigador, desarrollador y revisor encadenados con un loop de herramientas (modo `chat` en un solo paso).
- **Panel de artefactos en vivo**: sigue el último archivo creado mientras la misión hace streaming; preview sandbox de HTML/SVG (srcdoc endurecido, sin `<script>`), visor de código y acceso a Studio (pantalla completa).
- **Herramientas**: sandbox por contenedor (bubblewrap) para ejecuciones seguras, workspace, git, razonamiento, fetch, RAG vectorial (`sqlite-vec`) sobre el vault, bash, sistema de archivos y MCP (conexión única por servidor).
- **MCP**: singleton con event loop propio (soporta stdio y SSE sin cross-loop), `call_tool_sync` para uso desde el loop del agente.
- **Permisos**: herramientas sensibles requieren aprobación manual en la UI.
- **Autenticación local**: si `OTTERCODE_TOKEN` está definido, el frontend obtiene un token por loopback y lo inyecta vía `X-Otter-Token`; la API lo verifica con `hmac.compare_digest`.
- **Tema**: único, claro estilo Trade Republic (sin toggle oscuro).

## Variables de entorno

| Variable | Valor por defecto | Uso |
| --- | --- | --- |
| `OTTERCODE_OLLAMA` | `http://127.0.0.1:11434` | Base de Ollama |
| `OTTERCODE_API` | `http://127.0.0.1:8099` | Base del propio backend (SSE). No selecciona el transporte LLM. |
| `OTTERCODE_LLM_BACKEND` | `ollama` | Transporte: `ollama` o `openai` |
| `OTTERCODE_MODEL` | `qwen3:4b` | Modelo por defecto |
| `OTTERCODE_TOKEN` | — | Si se define, activa autenticación con token |
| `OTTERCODE_FLUSH_TIMEOUT` | `60` | Timeout del flush de VRAM |
| `OTTERCODE_MODELS_TTL` | `30` | TTL (s) de la caché de `/api/tags` |

## Estructura

```
backend/       FastAPI, agentes, herramientas, MCP y RAG
src/           Frontend React (chat, artefactos, Studio, screens)
static/        Bundle de producción (emite npm run build)
frontend/mobile/  PWA (index.html sincronizado)
dev/           Suite de auto-test sin GPU ni Ollama
```

## Testing

```bash
.venv/bin/python dev/selftest.py   # 21 casos, sin GPU
npm run lint                        # oxlint
npm run build                       # typecheck (tsc -b) + bundle
```