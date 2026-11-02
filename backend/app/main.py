"""Application FastAPI : API REST + WebSocket + interface web (fichiers statiques du frontend) ; mode local d'abord."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api import routes_agents, routes_market, routes_system, routes_trading, ws
from .config import APP_NAME, APP_VERSION, get_settings, install_redaction, setup_logging
from .runtime import Runtime

log = logging.getLogger(__name__)


def create_app(runtime: Runtime | None = None) -> FastAPI:
    install_redaction, setup_logging()
    settings = get_settings()
    rt_holder = {"rt": runtime}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        rt = rt_holder["rt"] or Runtime()
        app.state.rt = rt
        app.state.hub = ws.Hub(rt)
        await rt.start()
        log.info("%s %s démarré — paper trading uniquement", APP_NAME, APP_VERSION)
        try:
            yield
        finally:
            await rt.stop()

    app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_methods=["*"], allow_headers=["*"], allow_credentials=False)
    for r in (routes_system.public, routes_system.router, routes_market.router, routes_agents.router, routes_trading.router, ws.router):
        app.include_router(r)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception):  # le détail interne n'est jamais exposé (ni les secrets)
        log.exception("erreur non gérée sur %s", request.url.path)
        return JSONResponse(status_code=500, content={"detail": "erreur interne du serveur"})

    dist = settings.frontend_dist
    if dist and Path(dist).exists():
        assets = Path(dist) / "assets"
        if assets.exists():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        def spa(path: str):
            if path.startswith("api/") or path == "ws":
                return JSONResponse(status_code=404, content={"detail": "introuvable"})
            f = (Path(dist) / path).resolve()
            if path and f.is_file() and Path(dist).resolve() in f.parents:
                return FileResponse(f)
            return FileResponse(Path(dist) / "index.html")
    return app


def check_public_binding(host: str) -> None:
    """Exposé sur Internet (hébergement web) : refuse de démarrer sans API_TOKEN fourni et assez long (un jeton généré localement ne suffit pas)."""
    import os
    if host in ("127.0.0.1", "localhost", "::1"):
        return
    token = os.environ.get("API_TOKEN", "")
    if len(token) < 24:
        raise SystemExit("Démarrage refusé : le backend écoute sur le réseau (BACKEND_HOST=%s). Définissez API_TOKEN (24 caractères minimum, "
                         "par ex. `openssl rand -hex 24`) : il protège l'accès à l'application." % host)


def run() -> None:  # python -m app.main
    import uvicorn
    setup_logging()
    s = get_settings()
    check_public_binding(s.host)
    uvicorn.run(create_app(), host=s.host, port=s.port, log_level="info")


if __name__ == "__main__":
    run()
