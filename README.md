# OtterCode Neo

Orquestador **local** de agentes autónomos estilo Arena AI: Ollama + MCP + RAG sobre FastAPI y un frontend React/Vite con tema oscuro/claro. Los agentes razonan, escriben archivos con herramientas y los resultados afloran en un **panel de artefactos** en vivo.

## Quickstart

```bash
bash install.sh
# o a mano:
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
- **Tema**: claro Trade Republic por defecto; se puede invertir (oscuro) con el botón luna/sol de la cabecera.

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

## Variables de entorno de Ollama (velocidad / VRAM)

Hay que exportarlas **en el proceso del daemon Ollama** (no basta con el backend) y reiniciar Ollama. OtterCode avisa en el log de arranque si faltan; no bloquea.

| Variable | Valor | Efecto |
| --- | --- | --- |
| `OLLAMA_FLASH_ATTENTION` | `1` | Atención flash: menos VRAM, más contexto |
| `OLLAMA_KV_CACHE_TYPE` | `q8_0` | KV-cache cuantizado |
| `OLLAMA_MAX_LOADED_MODELS` | `1` | Un modelo a la vez (RTX 8 GB) |
| `OLLAMA_NUM_PARALLEL` | `1` | Sin lotes concurrentes que inflen el KV |

**Linux / macOS** (systemd user o shell):

```bash
export OLLAMA_FLASH_ATTENTION=1
export OLLAMA_KV_CACHE_TYPE=q8_0
export OLLAMA_MAX_LOADED_MODELS=1
export OLLAMA_NUM_PARALLEL=1
# si Ollama corre como servicio:
# Linux: sudo systemctl edit ollama  → [Service] Environment=...
# macOS: launchctl setenv OLLAMA_FLASH_ATTENTION 1  (y las demás) y reinicia Ollama
ollama serve
```

**Windows** (PowerShell, sesión actual o variables de usuario):

```powershell
$env:OLLAMA_FLASH_ATTENTION="1"
$env:OLLAMA_KV_CACHE_TYPE="q8_0"
$env:OLLAMA_MAX_LOADED_MODELS="1"
$env:OLLAMA_NUM_PARALLEL="1"
# Persistente: Configuración → Sistema → Acerca de → Configuración avanzada → Variables de entorno
ollama serve
```

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