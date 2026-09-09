# OtterCode — cerebro Obsidian: vault, notas de misión, recall y memoria
from __future__ import annotations
from backend.config import *  # noqa: F401,F403
from backend.runstate import OtterRun  # noqa: E402
from backend.ollama import _ollama_ndjson_text  # noqa: E402
from backend.config import LLM_BACKEND, NUM_CTX_DEFAULT, OLLAMA_BASE_URL  # noqa: E402
from backend.agents import _chat_base  # noqa: E402
from backend.ollama import *  # noqa: F401,F403
from backend.runstate import *  # noqa: F401,F403
from fastapi import APIRouter  # noqa
router = APIRouter(tags=["vault"])


def _memory_finish(run: OtterRun, status: str) -> Optional[str]:
    """Escribe la nota de misión en el vault y la anuncia en el transcript."""
    rel = memory_note_for_run(run, status)
    if rel:
        run.transcript.append({
            "kind": "system",
            "text": f"🧠 Memoria guardada en el cerebro Obsidian: {rel} "
                    f"(Perfil actualizado).",
        })
    return rel



_VAULT_CFG_PATH = Path(__file__).resolve().parent.parent / ".otter_vault.json"
_VAULT_SKIP_DIRS = {".obsidian", ".trash", ".git", "node_modules"}
_VAULT_MAX_NOTES = 400


def default_vault_dir() -> Path:
    """Vault persistente fuera de /tmp: ~/.ottercode/vault o %APPDATA%/OtterCode/vault."""
    if os.name == "nt":
        root = Path(os.environ.get("APPDATA") or Path.home()) / "OtterCode"
    else:
        root = Path.home() / ".ottercode"
    vault = root / "vault"
    vault.mkdir(parents=True, exist_ok=True)
    return vault


def _load_vault_cfg() -> None:
    """Al arrancar: vault persistente. Ignora rutas /tmp (se pierden al reiniciar)."""
    if os.environ.get("OTTERCODE_VAULT", "").strip():
        return
    path = ""
    try:
        cfg = json.loads(_VAULT_CFG_PATH.read_text(encoding="utf-8"))
        path = str(cfg.get("path", "")).strip()
    except (OSError, json.JSONDecodeError):
        path = ""
    ephemeral = (not path) or path.startswith("/tmp") or path.startswith("/var/tmp")
    if ephemeral or not Path(path).is_dir():
        dest = default_vault_dir()
        try:
            _VAULT_CFG_PATH.write_text(
                json.dumps({"path": str(dest)}, ensure_ascii=False), encoding="utf-8"
            )
        except OSError:
            pass
        os.environ["OTTERCODE_VAULT"] = str(dest)
        return
    os.environ["OTTERCODE_VAULT"] = path


_load_vault_cfg()


def _vault_root_checked() -> Optional[Path]:
    raw = os.environ.get("OTTERCODE_VAULT", "").strip()
    if not raw:
        return None
    root = Path(raw).resolve()
    return root if root.is_dir() else None


class VaultConfigRequest(BaseModel):
    path: str = Field(min_length=1, max_length=500)


@router.get(Route.VAULT_STATUS)
def api_vault_status() -> Dict[str, Any]:
    root = _vault_root_checked()
    if root is None:
        return {"configured": False, "path": "", "notes": 0}
    notes = sum(1 for p in root.rglob("*.md")
                if not any(part in _VAULT_SKIP_DIRS for part in p.parts))
    return {"configured": True, "path": str(root), "notes": notes}


@router.post(Route.VAULT_CONFIG)
def api_vault_config(req: VaultConfigRequest) -> Dict[str, Any]:
    raw = req.path.strip()
    root = Path(raw).expanduser().resolve()
    if not root.is_dir():
        raise HTTPException(status_code=400, detail=f"La carpeta no existe: {raw}")
    os.environ["OTTERCODE_VAULT"] = str(root)
    try:
        _VAULT_CFG_PATH.write_text(
            json.dumps({"path": str(root)}, ensure_ascii=False), encoding="utf-8"
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"No se pudo guardar la config: {exc}")
    notes = sum(1 for p in root.rglob("*.md")
                if not any(part in _VAULT_SKIP_DIRS for part in p.parts))
    return {"ok": True, "configured": True, "path": str(root), "notes": notes}


@router.get(Route.VAULT_GRAPH)
def api_vault_graph() -> Dict[str, Any]:
    """Grafo del vault: nodos=notas, aristas=[[enlaces]], tags más usados."""
    root = _vault_root_checked()
    if root is None:
        raise HTTPException(status_code=409, detail="Vault no configurado.")
    nodes: List[Dict[str, Any]] = []
    edges: List[Dict[str, Any]] = []
    tag_counter: Dict[str, int] = {}
    seen_ids: set = set()

    def _note_id(p: Path) -> str:
        return p.stem  # id corto estilo Obsidian: nombre sin .md

    md_files = [p for p in sorted(root.rglob("*.md"))
                if not any(part in _VAULT_SKIP_DIRS for part in p.parts)][:_VAULT_MAX_NOTES]
    stems: Dict[str, Path] = {}
    for p in md_files:
        rel = p.relative_to(root)
        folder = str(rel.parent) if str(rel.parent) != "." else ""
        nid = _note_id(p)
        stems.setdefault(nid.lower(), p)
        size = 0
        try:
            size = p.stat().st_size
        except OSError:
            pass
        nodes.append({"id": nid, "path": str(rel), "folder": folder, "size": size})
        seen_ids.add(nid)

    link_rx = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]*)?(?:\|[^\]]*)?\]\]")
    tag_rx = re.compile(r"(?<!\w)#([\wáéíóúñÁÉÍÓÚÑ\-]{2,40})")
    for p in md_files:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")[:60_000]
        except OSError:
            continue
        src = _note_id(p)
        targets = set()
        for m in link_rx.finditer(text):
            t = m.group(1).strip()
            key = t.lower()
            resolved = stems.get(key)
            tid = _note_id(resolved) if resolved else t
            if tid != src:
                targets.add(tid)
                if key not in stems:
                    # nota referenciada que aún no existe: nodo fantasma
                    if tid not in seen_ids:
                        seen_ids.add(tid)
                        nodes.append({"id": tid, "path": f"⚠ {t}.md", "folder": "", "size": 0,
                                      "ghost": True})
        for t in targets:
            edges.append({"source": src, "target": t})
        for m in tag_rx.finditer(text):
            tag = m.group(1).lower()
            if tag.isdigit():
                continue
            tag_counter[tag] = tag_counter.get(tag, 0) + 1

    top_tags = sorted(tag_counter.items(), key=lambda kv: -kv[1])[:20]
    return {"root": str(root), "nodes": nodes, "edges": edges,
            "tags": [{"tag": t, "count": c} for t, c in top_tags],
            "truncated": len(md_files) >= _VAULT_MAX_NOTES}


@router.get(Route.VAULT_NOTE)
def api_vault_note(path: str) -> Dict[str, Any]:
    """Devuelve una nota (markdown crudo) + backlinks desde todo el vault."""
    root = _vault_root_checked()
    if root is None:
        raise HTTPException(status_code=409, detail="Vault no configurado.")
    rel = path.strip().lstrip("/\\")
    if not rel or ".." in Path(rel).parts:
        raise HTTPException(status_code=400, detail="Ruta de nota inválida.")
    target = (root / rel).resolve()
    if not str(target).startswith(str(root)) or not target.is_file():
        raise HTTPException(status_code=404, detail=f"Nota no encontrada: {path}")
    try:
        content = target.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"No se pudo leer: {exc}")
    backlinks: List[str] = []
    stem = target.stem.lower()
    link_rx = re.compile(r"\[\[([^\]|#]+)")
    for p in root.rglob("*.md"):
        if p == target or any(part in _VAULT_SKIP_DIRS for part in p.parts):
            continue
        try:
            head = p.read_text(encoding="utf-8", errors="replace")[:60_000]
        except OSError:
            continue
        for m in link_rx.finditer(head):
            if m.group(1).strip().lower() == stem:
                backlinks.append(str(p.relative_to(root)))
                break
    return {"path": str(target.relative_to(root)), "content": content,
            "backlinks": backlinks[:30]}


# ---------------------------------------------------------------------------
# 🧠 MEMORIA AUTOMÁTICA (v3.3) — estilo "Claude con memoria en Obsidian":
# cada misión deja nota en <vault>/OtterCode/Misiones/, el Perfil del usuario
# crece con una línea por misión y ANTES de cada misión se inyecta el recuerdo
# relevante en el prompt. Todo automático, silencioso y a prueba de fallos.
# ---------------------------------------------------------------------------

_MEM_BASE = "OtterCode"


def _memory_mission_summary(run: Any, limit: int = 900) -> str:
    """Mejor resumen disponible de la misión: el 'finalizar' + cola del texto."""
    fin = ""
    for e in reversed(run.transcript):
        if e.get("kind") == "tool" and e.get("tool") == "finalizar" and e.get("ok"):
            fin = str(e.get("output", ""))
            break
    if not fin:
        for e in reversed(run.transcript):
            if e.get("kind") == "agent" and len(str(e.get("text", ""))) > 80:
                fin = str(e["text"])
                break
    return fin.strip()[:limit]


# v4.0 · ESTUDIAR AL USUARIO: tras cada misión, una llamada corta extrae
# QUÉ tipo de persona eres (stack, estilo, gustos, proyectos) y lo acumula
# en <vault>/OtterCode/Perfil_Usuario.md. Estilo Hermes/Claude-memory.
_USER_INSIGHT_SYSTEM = (
    "Eres el perfilador del usuario de OtterCode. Analiza la misión y su "
    "resumen y extrae SOLO datos sobre el USUARIO (nunca del código): "
    "stack y preferencias técnicas, estilo de trabajo, proyectos recurrentes, "
    "gustos estéticos, idioma, nivel técnico. Máximo 4 líneas, cada una con "
    "formato '- <hecho conciso en español>'. Si no descubres nada nuevo sobre "
    "el usuario, responde exactamente: NADA"
)


def _extract_user_insights(run: Any, model: str = "") -> str:
    """Bullets sobre el usuario aprendidos de esta misión ('' si nada)."""
    material = (
        f"TAREA DEL USUARIO: {run.task_text[:600]}\n"
        f"RESULTADO: {_memory_mission_summary(run, 500)}"
    )
    num_ctx = getattr(run, "num_ctx", None) or NUM_CTX_DEFAULT
    model = model or pick_memory_llm_model() or getattr(run, "model", "")
    try:
        if LLM_BACKEND == "openai":
            resp = requests.post(
                f"{_chat_base()}/chat/completions",
                json={"model": model, "stream": False, "max_tokens": 256,
                      "messages": [{"role": "system", "content": _USER_INSIGHT_SYSTEM},
                                   {"role": "user", "content": material}]},
                timeout=(10, 90))
            resp.raise_for_status()
            data = resp.json()
            out = ((data.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        else:
            resp = requests.post(
                f"{OLLAMA_BASE_URL}/api/generate",
                json={"model": run.model, "prompt": material,
                      "system": _USER_INSIGHT_SYSTEM, "stream": False,
                      "options": {"num_ctx": int(num_ctx), "num_predict": 256, "num_gpu": 99}},
                timeout=(10, 90))
            resp.raise_for_status()
            out = _ollama_ndjson_text(resp.text) or ""
    except Exception:  # noqa: BLE001 — el perfilado jamás tumba una misión
        return ""
    out = out.strip()
    if not out or out.upper().startswith("NADA"):
        return ""
    lines = [ln.strip() for ln in out.splitlines() if ln.strip()][:4]
    return "\n".join(ln[:170] for ln in lines if ln.startswith("-")) or ""


def memory_note_for_run(run: Any, status: str) -> Optional[str]:
    """Escribe la nota de la misión en el vault y actualiza el Perfil.

    Devuelve la ruta relativa de la nota (para SSE/transcript) o None si no
    hay vault configurado o algo falla. Idempotente por misión (_memory_done).
    """
    if getattr(run, "_memory_done", True):
        return None
    run._memory_done = True                      # marca SIEMPRE: 1 intento
    root = _vault_root_checked()
    if root is None:
        return None
    try:
        base = root / _MEM_BASE
        misiones = base / "Misiones"
        misiones.mkdir(parents=True, exist_ok=True)
        fecha = datetime.now().strftime("%Y-%m-%d %H:%M")
        archivos = "\n".join(
            f"- `{f.get('path') if isinstance(f, dict) else getattr(f, 'path', str(f))}`"
            for f in (run.meta.get("files") or [])
        ) or "- (ninguno)"
        icono = {"done": "✅", "aborted": "⏹️", "error": "❌"}.get(status, "•")
        estado = {"done": "Completada", "aborted": "Abortada por el usuario",
                  "error": "Error"}.get(status, status)
        resumen = _memory_mission_summary(run)
        note = [
            f"---",
            f"tarea: {run.task_text[:120].replace(chr(10), ' ')}",
            f"fecha: {fecha}",
            f"estado: {status}",
            f"modo: {run.mode} · modelo: {run.model}",
            f"duracion_s: {run.meta.get('duration_s', 0)}",
            "---",
            "",
            f"# {icono} Misión {run.task_id}",
            "",
            f"**Tarea:** {run.task_text}",
            "",
            f"**Estado:** {estado} · {fecha} · duración {run.meta.get('duration_s', 0)}s",
            "",
            "**Archivos generados:**",
            archivos,
            "",
            "**Resumen del relevo:**",
            "",
            resumen if resumen else "(sin resumen disponible)",
            "",
            f"Relacionado: [[Perfil]]",
        ]
        rel_note = f"{_MEM_BASE}/Misiones/{run.task_id}.md"
        (misiones / f"{run.task_id}.md").write_text("\n".join(note), encoding="utf-8")

        # Perfil.md: bitácora cronológica del usuario (crece para siempre)
        perfil = base / "Perfil.md"
        if not perfil.exists():
            perfil.write_text(
                "# Perfil del usuario\n\nBitácora automática de OtterCode "
                "(cada línea es una misión; las notas completas viven en "
                "[[Misiones]]).\n",
                encoding="utf-8",
            )
        primera_linea = resumen.splitlines()[0][:100] if resumen else ""
        with perfil.open("a", encoding="utf-8") as fh:
            fh.write(f"- **{fecha}** · {icono} {run.task_text[:90]} → "
                     f"{primera_linea} · [[Misiones/{run.task_id}|detalle]]\n")

        # v4.0 · PERFIL_Usuario.md: lo que la balsa ha aprendido de TI
        from backend.memory import pick_memory_llm_model
        _mem_m = pick_memory_llm_model()
        if _mem_m:
            insights = _extract_user_insights(run, _mem_m)
            if insights:
                perfil_u = base / "Perfil_Usuario.md"
                if not perfil_u.exists():
                    perfil_u.write_text(
                        "# 🧠 Perfil del usuario\n\n"
                        "Lo que OtterCode sabe de ti: stack, estilo, gustos y "
                        "proyectos. Se actualiza solo tras cada misión y se "
                        "inyecta en las siguientes para adaptarse a ti.\n",
                        encoding="utf-8",
                    )
                with perfil_u.open("a", encoding="utf-8") as fh:
                    fh.write(f"\n## {fecha} — {run.task_text[:70]}\n{insights}\n")
        return rel_note
    except Exception:  # noqa: BLE001 — la memoria jamás tumba una misión
        return None


_WORD_RX = re.compile(r"[a-záéíóúñü0-9]{4,}", re.IGNORECASE)


def _memory_recall(task_text: str, max_chars: int = 2400,
                   max_notes: int = 3) -> str:
    """Recupera memoria relevante del vault para inyectarla en el prompt.

    Puntúa notas por solapamiento de palabras con la tarea; siempre incluye
    la cabecera del Perfil si existe. Silencioso ante cualquier error.
    """
    root = _vault_root_checked()
    if root is None or not task_text:
        return ""
    try:
        tokens = set(t.lower() for t in _WORD_RX.findall(task_text))
        if not tokens:
            return ""
        candidates: List[Tuple[int, Path]] = []
        files = [p for p in sorted(root.rglob("*.md"))
                 if not any(part in _VAULT_SKIP_DIRS for part in p.parts)][:300]
        perfil_rels: List[str] = []                    # Perfil_Usuario + Perfil
        for p in files:
            rel = str(p.relative_to(root)).replace("\\\\", "/")
            try:
                text = p.read_text(encoding="utf-8", errors="replace")[:8000]
            except OSError:
                continue
            if p.name.lower() in ("perfil.md", "perfil_usuario.md"):
                perfil_rels.append(rel)
                continue
            words = set(w.lower() for w in _WORD_RX.findall(text))
            score = len(tokens & words)
            if score >= 2:
                candidates.append((score, p))
        candidates.sort(key=lambda sp: -sp[0])

        blocks: List[str] = []
        used = 0
        # v4.0 · PRIMERO el perfil del usuario (lo que sé de ti), con presupuesto
        for rel in perfil_rels:
            try:
                head = (root / rel).read_text(encoding="utf-8",
                                              errors="replace")[-1600:]
                blocks.append(f"### LO QUE SÉ DEL USUARIO ({rel})\n{head}")
                used += len(head)
            except OSError:
                pass
        for _score, p in candidates[:max_notes]:
            if used >= max_chars:
                break
            rel = str(p.relative_to(root)).replace("\\\\", "/")
            budget = min(900, max_chars - used)
            try:
                body = p.read_text(encoding="utf-8", errors="replace")[:budget]
            except OSError:
                continue
            blocks.append(f"### Nota relacionada: [[{p.stem}]] ({rel})\n{body}")
            used += len(body) + 40
        return "\n\n".join(blocks)[:max_chars]
    except Exception:  # noqa: BLE001
        return ""


