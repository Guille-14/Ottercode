#!/usr/bin/env bash
# Instalador de un comando: venv, deps, Ollama, flags de velocidad y arranque.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"
python3 -m venv .venv
.venv/bin/pip install -U pip
.venv/bin/pip install -e .
if command -v npm >/dev/null 2>&1; then
  npm install
  npm run build
fi

if ! command -v ollama >/dev/null 2>&1; then
  echo "Aviso: ollama no está en PATH. Instálalo para generar en local."
else
  if ! pgrep -x ollama >/dev/null 2>&1 && ! curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    echo "Arrancando ollama serve…"
    ollama serve >/tmp/ottercode-ollama.log 2>&1 &
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1 && break
      sleep 0.5
    done
  fi
fi

_warn_flag() {
  local k="$1"
  if [ -z "${!k:-}" ]; then
    echo "Aviso: $k no está en el entorno del daemon Ollama (FLASH_ATTENTION / KV_CACHE más rápidos). Ver README."
  fi
}
_warn_flag OLLAMA_FLASH_ATTENTION
_warn_flag OLLAMA_KV_CACHE_TYPE

echo "OtterCode listo. Arrancando backend en :8000…"
echo "Permisos de herramientas: ON por defecto. YOLO en la UI o /yolo."
exec .venv/bin/python -m uvicorn backend:app --host 0.0.0.0 --port 8000
