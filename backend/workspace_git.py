"""Git de misión: rama otter/<task_id> y worktree opcional."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any, Dict


def _run(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True, timeout=20,
    )


def ensure_mission_git(workdir: Path, task_id: str) -> Dict[str, Any]:
    """Inicializa git en el workspace y crea la rama otter/<id>.

    OTTERCODE_GIT=0 lo desactiva (tests). Worktree si OTTERCODE_WORKTREE apunta
    a un repo existente.
    """
    if os.environ.get("OTTERCODE_GIT", "1").strip() in ("0", "false", "no"):
        return {"ok": False, "reason": "disabled"}
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    branch = f"otter/{task_id}"[:80]
    root = os.environ.get("OTTERCODE_WORKTREE", "").strip()
    if root:
        repo = Path(root).expanduser().resolve()
        if repo.is_dir() and (repo / ".git").exists():
            dest = workdir
            if dest.exists() and any(dest.iterdir()):
                pass
            else:
                r = _run(repo, "worktree", "add", "-b", branch, str(dest))
                if r.returncode == 0:
                    return {"ok": True, "mode": "worktree", "branch": branch, "path": str(dest)}
    git_dir = workdir / ".git"
    if not git_dir.exists():
        _run(workdir, "init", "-q")
        _run(workdir, "config", "user.email", "otter@local")
        _run(workdir, "config", "user.name", "OtterCode")
    _run(workdir, "checkout", "-B", branch)
    return {"ok": True, "mode": "branch", "branch": branch, "path": str(workdir)}


def maybe_auto_rag(workdir: Path) -> None:
    """Indexa el workspace en segundo plano al abrirlo (OTTERCODE_AUTO_RAG=1)."""
    if os.environ.get("OTTERCODE_AUTO_RAG", "1").strip() in ("0", "false", "no"):
        return
    stamp = Path(workdir) / ".otter_rag.indexed"
    if stamp.exists():
        return

    def _job() -> None:
        try:
            import tools as tools_mod
            ex = tools_mod.ToolExecutor(workdir)
            ex.index_workspace()
            stamp.write_text("ok", encoding="utf-8")
        except Exception:
            pass

    import threading
    threading.Thread(target=_job, daemon=True, name="otter-rag").start()
