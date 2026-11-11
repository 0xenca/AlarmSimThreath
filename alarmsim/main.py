"""Aplicación FastAPI del generador."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import __version__
from .bootstrap import init_db
from .config import config
from .core import receiver
from .core.runner import runner
from .core.sender import pool
from .web import api, pages
from .web.deps import BASE, CSRFError, ForcePassword, LoginRequired

log = logging.getLogger("alarmsim")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    runner.start_scheduler()
    servers = []
    try:
        servers = await receiver.start(config.test_receiver_host, config.test_receiver_port)
    except OSError as exc:
        log.error("No se pudo abrir el receptor de pruebas: %s", exc)
    yield
    await runner.shutdown()
    await pool.close_all()
    for srv in servers:
        srv.close()


def create_app() -> FastAPI:
    app = FastAPI(title="AlarmSim – generador SIA DC-09", version=__version__, lifespan=lifespan,
                  docs_url="/api/docs", redoc_url=None, openapi_url="/api/openapi.json")
    app.add_middleware(SessionMiddleware, secret_key=config.secret_key, session_cookie="alarmsim_session",
                       max_age=12 * 3600, same_site="lax")
    app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
    app.include_router(pages.router)
    app.include_router(api.router)

    @app.exception_handler(LoginRequired)
    async def _login(request: Request, exc: LoginRequired):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "No autenticado"}, 401)
        return RedirectResponse("/login", 303)

    @app.exception_handler(ForcePassword)
    async def _force(request: Request, exc: ForcePassword):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Debe cambiar la contraseña"}, 403)
        return RedirectResponse("/account", 303)

    @app.exception_handler(CSRFError)
    async def _csrf(request: Request, exc: CSRFError):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Token CSRF inválido; recargue la página"}, 400)
        return HTMLResponse("Token CSRF inválido. Recargue la página.", 400)

    @app.get("/health", include_in_schema=False)
    def health():
        return {"ok": True, "version": __version__}

    return app


app = create_app()
