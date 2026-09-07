#!/usr/bin/env python3
"""Launcher de OtterCode — lanza backend + abre navegador."""
import subprocess
import webbrowser
import time
import sys
import os

PORT = int(os.environ.get("OTTERCODE_PORT", "8000"))
HOST = os.environ.get("OTTERCODE_HOST", "127.0.0.1")

# Usa el python del venv si existe (ahí está uvicorn/backend instalado)
BASE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(BASE, ".venv", "bin", "python")
if not os.path.exists(PY):
    PY = sys.executable


def main():
    proc = subprocess.Popen(
        [PY, "-m", "uvicorn", "backend:app",
         "--host", HOST, "--port", str(PORT), "--log-level", "warning"],
        cwd=BASE,
    )
    time.sleep(1.5)
    webbrowser.open(f"http://{HOST}:{PORT}")
    print(f"🦦 OtterCode corriendo en http://{HOST}:{PORT}")
    print("Ctrl+C para parar")
    try:
        proc.wait()
    except KeyboardInterrupt:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
        print("\n🦦 OtterCode detenido.")


if __name__ == "__main__":
    main()
