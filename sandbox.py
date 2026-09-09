"""
sandbox.py · Motor de aislamiento real con Bubblewrap (bwrap) para OtterCode v3.0
"""

import os
import shutil
import subprocess
from pathlib import Path
from typing import List, Tuple


class SandboxExecutor:
    def __init__(self, workdir: Path, share_net: bool = False):
        self.workdir = Path(workdir).resolve()
        env_net = os.environ.get("OTTERCODE_SANDBOX_NET", "0").strip().lower() in ("1", "true", "yes")
        # Por defecto --unshare-net. Red solo si share_net o OTTERCODE_SANDBOX_NET=1
        # (whitelist efectiva: Ollama/MCP viven FUERA del bwrap, no dentro).
        self.share_net = bool(share_net or env_net)
        self._bwrap_path = shutil.which("bwrap")
        self._available = self._check_bwrap()

    def _check_bwrap(self) -> bool:
        if not self._bwrap_path:
            return False
        try:
            # Prueba rápida si bwrap soporta namespaces de usuario
            res = subprocess.run(
                [self._bwrap_path, "--unshare-user", "--version"],
                capture_output=True,
                timeout=2,
            )
            return res.returncode == 0
        except Exception:
            return False

    def is_available(self) -> bool:
        return self._available

    def run(self, cmd, timeout: int = 120) -> dict:
        """Ejecuta un comando dentro de bwrap aislando el filesystem y recursos."""
        if isinstance(cmd, str):
            cmd = ["bash", "-c", cmd]
        if not self._available:
            required = os.environ.get("OTTERCODE_SANDBOX_REQUIRED", "1").strip().lower() not in ("0", "false", "no")
            if required:
                return {
                    "returncode": 1, "stdout": "", "timeout": False,
                    "stderr": "SANDBOX_REQUIRED: no hay bwrap. Instala bubblewrap o pon OTTERCODE_SANDBOX_REQUIRED=0.",
                }
            try:
                res = subprocess.run(
                    cmd,
                    cwd=str(self.workdir),
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                return {"returncode": res.returncode, "stdout": res.stdout, "stderr": res.stderr, "timeout": False}
            except subprocess.TimeoutExpired:
                return {"returncode": -1, "stdout": "", "stderr": "Timeout: el comando excedió el tiempo límite.", "timeout": True}
            except Exception as exc:
                return {"returncode": 1, "stdout": "", "stderr": str(exc), "timeout": False}

        # Construcción del comando bwrap
        bwrap_args = [
            self._bwrap_path,
            # Sistema de archivos base de solo lectura
            "--ro-bind", "/", "/",
            # Directorio temporal /tmp aislado
            "--tmpfs", "/tmp",
            "--proc", "/proc",
            "--dev", "/dev",
            # Workspace con permisos de escritura
            "--bind", str(self.workdir), str(self.workdir),
            # Aislamiento de namespaces
            "--unshare-pid",
            "--unshare-ipc",
            "--die-with-parent",
            "--new-session",
            "--chdir", str(self.workdir),
            "--clearenv",
        ]

        # Pasar variables de entorno esenciales limpias
        for key in ["PATH", "HOME", "USER", "LANG", "LC_ALL", "PYTHONPATH"]:
            val = os.environ.get(key)
            if val:
                bwrap_args.extend(["--setenv", key, val])

        if not self.share_net:
            bwrap_args.append("--unshare-net")

        bwrap_args.append("--")
        bwrap_args.extend(cmd)

        try:
            res = subprocess.run(
                bwrap_args,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            return {"returncode": res.returncode, "stdout": res.stdout, "stderr": res.stderr, "timeout": False}
        except subprocess.TimeoutExpired:
            return {"returncode": -1, "stdout": "", "stderr": "Timeout: el comando en sandbox excedió el tiempo límite.", "timeout": True}
        except Exception as exc:
            return {"returncode": 1, "stdout": "", "stderr": f"Error sandbox bwrap: {exc}", "timeout": False}
