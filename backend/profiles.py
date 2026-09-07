# OtterCode — perfiles de configuración (CRUD + activo)
from __future__ import annotations
from backend.config import *  # noqa: F401,F403
from backend.config import DEFAULT_MODEL, NUM_CTX_DEFAULT, WORKSPACE_ROOT  # noqa: E402


PROFILES_DIR = WORKSPACE_ROOT / "profiles"
PROFILES_DIR.mkdir(parents=True, exist_ok=True)
ACTIVE_PROFILE_PATH = WORKSPACE_ROOT / "profiles.json"

# Perfil activo en memoria (se carga al arranque)
_ACTIVE_PROFILE: Dict[str, Any] = {}


def _default_profile() -> Dict[str, Any]:
    """Perfil por defecto si no hay ninguno seleccionado."""
    return {
        "name": "default",
        "display_name": "🦦 Otter (Default)",
        "model": DEFAULT_MODEL,
        "temperature": 0.7,
        "top_p": 0.9,
        "num_ctx": NUM_CTX_DEFAULT,
        "system_override": "",
    }


def _load_profiles() -> List[Dict[str, Any]]:
    """Carga todos los perfiles del directorio profiles/."""
    profiles: List[Dict[str, Any]] = []
    if not PROFILES_DIR.is_dir():
        return profiles
    for f in sorted(PROFILES_DIR.glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "name" in data:
                profiles.append(data)
        except (json.JSONDecodeError, OSError):
            continue
    return profiles


def _ensure_default_profiles() -> None:
    """Crea los perfiles por defecto si el directorio está vacío."""
    if not PROFILES_DIR.is_dir():
        PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    existing = list(PROFILES_DIR.glob("*.json"))
    if existing:
        return
    defaults = [
        {"name": "default", "display_name": "🦦 Otter (Default)",
         "model": DEFAULT_MODEL, "temperature": 0.7, "top_p": 0.9,
         "num_ctx": NUM_CTX_DEFAULT, "system_override": ""},
        {"name": "coder", "display_name": "💻 Coder",
         "model": DEFAULT_MODEL, "temperature": 0.3, "top_p": 0.85,
         "num_ctx": NUM_CTX_DEFAULT,
         "system_override": "Eres un programador experto. Escribe código limpio, bien documentado y con buenas prácticas."},
        {"name": "writer", "display_name": "✍️ Writer",
         "model": DEFAULT_MODEL, "temperature": 0.9, "top_p": 0.95,
         "num_ctx": NUM_CTX_DEFAULT,
         "system_override": "Eres un escritor creativo y persuasivo. Escribe contenido atractivo y bien estructurado."},
    ]
    for p in defaults:
        path = PROFILES_DIR / f"{p['name']}.json"
        path.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_active_profile() -> None:
    """Carga el perfil activo desde profiles.json (o 'default' si no existe)."""
    global _ACTIVE_PROFILE
    try:
        data = json.loads(ACTIVE_PROFILE_PATH.read_text(encoding="utf-8"))
        name = data.get("active", "default")
    except (json.JSONDecodeError, OSError):
        name = "default"
    profiles = _load_profiles()
    for p in profiles:
        if p.get("name") == name:
            _ACTIVE_PROFILE = p
            return
    # Si el perfil referenciado no existe, usar el primero o default
    _ACTIVE_PROFILE = profiles[0] if profiles else _default_profile()


def _save_active_profile(name: str) -> None:
    """Persiste el perfil activo en profiles.json."""
    ACTIVE_PROFILE_PATH.write_text(
        json.dumps({"active": name}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    # Recargar en memoria
    _load_active_profile()


def _get_profile(name: str) -> Optional[Dict[str, Any]]:
    """Busca un perfil por nombre."""
    for p in _load_profiles():
        if p.get("name") == name:
            return p
    return None


def _validate_profile_name(name: str) -> None:
    """Valida que el nombre del perfil sea seguro para uso en paths."""
    import re
    if not re.fullmatch(r"[\w\-]+", name):
        raise ValueError(f"Nombre de perfil inválido: '{name}'. Solo se permiten letras, números, guiones y guiones bajos.")


def _save_profile(profile: Dict[str, Any]) -> None:
    """Guarda/actualiza un perfil en disco."""
    name = profile.get("name", "").strip()
    if not name:
        raise ValueError("El nombre del perfil es obligatorio.")
    _validate_profile_name(name)
    path = PROFILES_DIR / f"{name}.json"
    path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")


def _delete_profile(name: str) -> bool:
    """Elimina un perfil. No permite borrar 'default'."""
    if name == "default":
        return False
    _validate_profile_name(name)
    path = PROFILES_DIR / f"{name}.json"
    if path.exists():
        path.unlink()
        # Si era el activo, volver a default
        if _ACTIVE_PROFILE.get("name") == name:
            _save_active_profile("default")
        return True
    return False


# --- Endpoints de perfiles (se registran después de `app`) ---


class ProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50)
    display_name: str = Field(default="", max_length=100)
    model: str = Field(default=DEFAULT_MODEL, max_length=200)
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    top_p: float = Field(default=0.9, ge=0.0, le=1.0)
    num_ctx: int = Field(default=NUM_CTX_DEFAULT, ge=2048, le=131072)
    system_override: str = Field(default="", max_length=10000)


class ActiveProfileRequest(BaseModel):
    name: str = Field(min_length=1, max_length=50)


# ---------------------------------------------------------------------------
