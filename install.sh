#!/usr/bin/env bash
# Instalador de un comando: venv, deps Python, npm y build del frontend.
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
echo "OtterCode listo. Arranque:"
echo "  .venv/bin/python -m uvicorn backend:app --host 0.0.0.0 --port 8000"
echo "Permisos de herramientas: ON por defecto. YOLO en la UI o /yolo."
echo "Tests: OTTERCODE_ASK_PERMISSIONS=0 .venv/bin/python dev/selftest.py"
