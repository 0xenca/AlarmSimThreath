"""Aplicación FastAPI: interfaz web, API REST y arranque de los receptores."""
from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from . import __version__
from .bootstrap import init_db, recover_after_restart
from .config import config
from .engine.processor import processor, supervision_loop
from .receivers import dc09_server, surgard_server
from .web import api, routes_admin, routes_clients, routes_ops
from .web.deps import BASE, CSRFError, ForcePassword, Forbidden, LoginRequired, render

log = logging.getLogger("alarmhub")


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    recover_after_restart()
    await processor.start()
    servers = []
    bg: list[asyncio.Task] = [asyncio.create_task(supervision_loop(), name="supervision")]
    if config.receivers_enabled:
        try:
            servers += await dc09_server.start(config.dc09_host, config.dc09_tcp_port, config.dc09_udp_port)
            srv = await surgard_server.start(config.dc09_host, config.surgard_port)
            if srv:
                servers.append(srv)
        except OSError as exc:
            log.error("No se pudo abrir el puerto del receptor: %s", exc)
    yield
    for t in bg:
        t.cancel()
    for srv in servers:
        srv.close()
    await processor.stop()


def create_app() -> FastAPI:
    app = FastAPI(title="AlarmHub", version=__version__, lifespan=lifespan, docs_url="/api/docs",
                  redoc_url=None, openapi_url="/api/openapi.json")
    app.add_middleware(SessionMiddleware, secret_key=config.secret_key, session_cookie="alarmhub_session",
                       max_age=12 * 3600, same_site="lax", https_only=config.secure_cookies)
    app.mount("/static", StaticFiles(directory=str(BASE / "static")), name="static")
    app.include_router(routes_ops.router)
    app.include_router(routes_clients.router)
    app.include_router(routes_admin.router)
    app.include_router(api.router)

    @app.exception_handler(LoginRequired)
    async def _login(request: Request, exc: LoginRequired):
        if request.url.path.startswith("/api/") or request.url.path == "/stream":
            return JSONResponse({"detail": "No autenticado"}, 401)
        return RedirectResponse("/login", 303)

    @app.exception_handler(ForcePassword)
    async def _force(request: Request, exc: ForcePassword):
        return RedirectResponse("/profile", 303)

    @app.exception_handler(Forbidden)
    async def _forbidden(request: Request, exc: Forbidden):
        return render(request, "error.html", code=403, message=f"No tiene permiso ({exc.perm})")

    @app.exception_handler(CSRFError)
    async def _csrf(request: Request, exc: CSRFError):
        return HTMLResponse("Token CSRF inválido. Recargue la página e inténtelo de nuevo.", 400)

    @app.get("/health", include_in_schema=False)
    def health():
        return {"ok": True, "version": __version__, "queue": processor.queue.qsize()}

    return app


app = create_app()
