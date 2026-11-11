from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from fastapi import Depends, Header, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import __version__
from ..config import config
from ..db import get_db
from ..models import User

BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE / "templates"))


class LoginRequired(Exception):
    pass


class ForcePassword(Exception):
    pass


class CSRFError(Exception):
    pass


def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(24)
        request.session["csrf"] = tok
    return tok


def flash(request: Request, message: str, level: str = "success") -> None:
    request.session.setdefault("flash", []).append([level, message])


def current_user(request: Request, db: Session = Depends(get_db),
                 x_api_key: str | None = Header(default=None)) -> User | None:
    if not config.auth_enabled:
        return None
    user = None
    if x_api_key:
        user = db.query(User).filter_by(api_key=x_api_key).first()
    elif request.session.get("uid"):
        user = db.get(User, request.session["uid"])
    if user is None:
        raise LoginRequired()
    if user.must_change_password and not x_api_key and request.url.path not in ("/account", "/logout"):
        raise ForcePassword()
    request.state.user = user
    return user


async def checked_form(request: Request) -> dict[str, Any]:
    form = await request.form()
    if not secrets.compare_digest(str(form.get("csrf", "")), request.session.get("csrf", "") or "x"):
        raise CSRFError()
    data: dict[str, Any] = {}
    for key in form.keys():
        vals = form.getlist(key)
        data[key] = vals if key.endswith("[]") else vals[-1]
    return data


def check_json_csrf(request: Request, x_csrf: str | None = Header(default=None),
                    x_api_key: str | None = Header(default=None)) -> None:
    """Las llamadas JSON desde el navegador llevan X-CSRF; las de API, X-API-Key."""
    if x_api_key or not config.auth_enabled:
        return
    if not x_csrf or not secrets.compare_digest(x_csrf, request.session.get("csrf", "") or "x"):
        raise CSRFError()


def render(request: Request, name: str, **ctx: Any):
    base = {"user": getattr(request.state, "user", None), "auth": config.auth_enabled, "csrf": csrf_token(request),
            "flashes": request.session.pop("flash", []), "version": __version__}
    base.update(ctx)
    return templates.TemplateResponse(request, name, base)


def b(v: Any) -> bool:
    return str(v).lower() in ("1", "on", "true", "yes", "si")


def s(v: Any) -> str:
    return "" if v is None else str(v).strip()
