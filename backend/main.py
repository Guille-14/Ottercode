# OtterCode — fábrica de la aplicación FastAPI (estáticos, PWA, tokens)
from __future__ import annotations
from backend.env_settings import load_otter_env  # noqa: E402
load_otter_env()
import hmac
import os
from typing import Any
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from events import Route
from backend.config import (
    MOBILE_DIR, APP_VERSION, warn_ollama_speed_env, STATIC_DIR, _load_identity
)
from backend.profiles import _load_active_profile  # noqa: E402
from backend.history import cleanup_empty_tasks, load_history  # noqa: E402
from backend.db import _migrate_json_to_db, init_db  # noqa: E402
from backend.config import APP_VERSION, MOBILE_DIR, STATIC_DIR, _load_identity, warn_ollama_speed_env  # noqa: E402
from backend.config import _load_identity  # noqa
from backend.db import (
    init_db, _migrate_json_to_db
)
from backend.history import (
    load_history, cleanup_empty_tasks
)
from backend.profiles import (
    _ensure_default_profiles, _load_active_profile
)
from backend.profiles import _ensure_default_profiles, _load_active_profile  # noqa
from backend.routes import router as api_router  # noqa
from backend.vault import router as vault_router  # noqa

# ── bootstrap de arranque (equivalente al módulo plano original) ──────────
load_history()
_n_cleaned = cleanup_empty_tasks()
if _n_cleaned:
    print(f"🧹 Limpieza de arranque: {_n_cleaned} carpeta(s) de tareas vacías eliminada(s).")
_load_identity()
init_db()
_migrate_json_to_db()
_ensure_default_profiles()
_load_active_profile()
warn_ollama_speed_env()
from backend.router import preload_router  # noqa: E402
preload_router()


app = FastAPI(title="OtterCode API", version=APP_VERSION)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost", "http://localhost:*",
        "http://127.0.0.1", "http://127.0.0.1:*",
        "http://0.0.0.0", "http://0.0.0.0:*",
    ],
    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1|[a-z0-9.-]+\.e2b\.app)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)
# v3.3 · Frontend modular: CSS y JS viven en /static (cacheables, editables)
if STATIC_DIR.is_dir():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ── v3.4 · SEGURIDAD DE RED ──────────────────────────────────────────────────
# Por defecto la balsa escucha en 127.0.0.1. Si se expone a la LAN
# (OTTERCODE_HOST=0.0.0.0), conviene OTTERCODE_TOKEN: sin el token correcto
# toda /api/* responde 401 (la denylist de bash NO es un muro de seguridad).
def _auth_ok(request: Request) -> bool:
    expected = os.environ.get("OTTERCODE_TOKEN", "").strip()
    if not expected:
        return True
    got = request.headers.get("x-otter-token", "").strip()
    if not got:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            got = auth[7:].strip()
    return hmac.compare_digest(got, expected)


def _is_loopback(request: Request) -> bool:
    host = ""
    if request.client:
        host = request.client.host or ""
    return host == "127.0.0.1" or host == "::1" or host == "localhost"


@app.middleware("http")
async def _token_guard(request: Request, call_next: Any):
    if request.url.path.startswith("/api/") and request.url.path != Route.AUTH_TOKEN and not _auth_ok(request):
        return JSONResponse(
            {"detail": "Token requerido: envía X-Otter-Token (OTTERCODE_TOKEN)."},
            status_code=401,
        )
    return await call_next(request)


@app.get(Route.AUTH_TOKEN)
def api_auth_token(request: Request) -> JSONResponse:
    """Entrega el token solo a clientes locales. Con OTTERCODE_TOKEN sin
    configurar devuelve vacío (la autenticación está desactivada)."""
    expected = os.environ.get("OTTERCODE_TOKEN", "").strip()
    if not expected:
        return JSONResponse({"token": ""})
    if not _is_loopback(request):
        return JSONResponse({"detail": "Acceso no permitido."}, status_code=403)
    return JSONResponse({"token": expected})

# ------------------------------- Frontend ---------------------------------

@app.get(Route.INDEX)
def index() -> FileResponse:
    """UI principal (chat en vivo + misiones + studio)."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get(Route.FAVICON)
def favicon() -> Response:
    """Sin favicon real: 204 vacío para no ensuciar con 404."""
    return Response(status_code=204)


# --------------------------- PWA móvil (nativa) ---------------------------

@app.get(Route.MOBILE)
def mobile_index() -> FileResponse:
    """SPA móvil (PWA instalable: manifiesto + service worker + offline)."""
    return FileResponse(MOBILE_DIR / "index.html")


@app.get(Route.MOBILE_MANIFEST)
def mobile_manifest() -> JSONResponse:
    manifest = {
        "name": "OtterCode — Balsa de Agentes",
        "short_name": "OtterCode",
        "description": "Orquestación de agentes locales con meta-orquestación "
                       "(modelos en el dispositivo vía llama.cpp / MLC Chat).",
        "id": "/m",
        "start_url": "/m",
        "scope": "/m",
        "display": "standalone",
        "orientation": "portrait",
        "background_color": "#FFFFFF",
        "theme_color": "#FFFFFF",
        "lang": "es",
        "icons": [
            {"src": "/m/icon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any"},
            {"src": "/m/icon-512.png", "sizes": "512x512", "type": "image/png", "purpose": "any maskable"},
        ],
    }
    return JSONResponse(manifest, media_type="application/manifest+json")


@app.get(Route.MOBILE_SW)
def mobile_service_worker() -> FileResponse:
    return FileResponse(MOBILE_DIR / "sw.js", media_type="application/javascript")


@app.get(Route.MOBILE_ICON_192)
def mobile_icon_192() -> FileResponse:
    return FileResponse(MOBILE_DIR / "icon-192.png")


@app.get(Route.MOBILE_ICON_512)
def mobile_icon_512() -> FileResponse:
    return FileResponse(MOBILE_DIR / "icon-512.png")


# ------------------------------- Estado -----------------------------------


app.include_router(api_router)
app.include_router(vault_router)
from backend.neo_routes import router as neo_router  # noqa: E402
app.include_router(neo_router)
