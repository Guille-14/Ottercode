from __future__ import annotations

from backend.home import cfg_get
from backend.environments.base import TerminalBackend


def get_backend(name: str = "") -> TerminalBackend:
    n = (name or cfg_get("terminal", "backend", default="local") or "local").lower()
    if n == "docker":
        from backend.environments.docker import DockerBackend
        return DockerBackend()
    if n == "ssh":
        from backend.environments.ssh import SshBackend
        return SshBackend()
    if n == "singularity":
        from backend.environments.singularity import SingularityBackend
        return SingularityBackend()
    if n == "modal":
        from backend.environments.modal import ModalBackend
        return ModalBackend()
    if n == "daytona":
        from backend.environments.daytona import DaytonaBackend
        return DaytonaBackend()
    if n == "vercel":
        from backend.environments.vercel import VercelBackend
        return VercelBackend()
    from backend.environments.local import LocalBackend
    return LocalBackend()
