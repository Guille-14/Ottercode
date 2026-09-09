#!/usr/bin/env bash
# Instalador: venv, deps, unidad systemd/launchd para Ollama (no `ollama serve &`).
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

install_ollama_unit() {
  if ! command -v ollama >/dev/null 2>&1; then
    echo "Aviso: ollama no está en PATH. Instálalo para generar en local."
    return 0
  fi
  if curl -sf http://127.0.0.1:11434/api/tags >/dev/null 2>&1; then
    echo "Ollama ya responde en :11434"
    return 0
  fi
  if command -v systemctl >/dev/null 2>&1 && [ "$(id -u)" -eq 0 ]; then
    UNIT=/etc/systemd/system/ollama.service
    if [ ! -f "$UNIT" ]; then
      cat > "$UNIT" <<'EOF'
[Unit]
Description=Ollama
After=network-online.target

[Service]
ExecStart=/usr/bin/ollama serve
Restart=always
Environment=OLLAMA_FLASH_ATTENTION=1
Environment=OLLAMA_KV_CACHE_TYPE=q8_0
Environment=OLLAMA_MAX_LOADED_MODELS=1

[Install]
WantedBy=multi-user.target
EOF
      systemctl daemon-reload
      systemctl enable --now ollama.service || true
    else
      systemctl start ollama.service || true
    fi
    return 0
  fi
  if [ "$(uname -s)" = "Darwin" ] && command -v launchctl >/dev/null 2>&1; then
    PLIST="$HOME/Library/LaunchAgents/com.ollama.serve.plist"
    if [ ! -f "$PLIST" ]; then
      mkdir -p "$(dirname "$PLIST")"
      cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.ollama.serve</string>
  <key>ProgramArguments</key><array><string>$(command -v ollama)</string><string>serve</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
</dict></plist>
EOF
      launchctl load "$PLIST" || true
    fi
    return 0
  fi
  echo "Aviso: no se pudo instalar unidad systemd/launchd. Arranca Ollama como servicio del sistema (no en background de este script)."
}

install_ollama_unit

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
