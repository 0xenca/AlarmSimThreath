from __future__ import annotations

import secrets
from pathlib import Path
from typing import Any

from fastapi import Depends, Request
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from .. import __version__, settings_store
from ..db import get_db
from ..engine.context import fmt_dt
from ..models import ALARM_STATUS, PERMISSIONS, RESOLUTIONS, AuditLog, User
from ..protocols.codes import CATEGORIES

BASE = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE / "templates"))


class LoginRequired(Exception):
    pass


class Forbidden(Exception):
    def __init__(self, perm: str):
        self.perm = perm


class CSRFError(Exception):
    pass


class ForcePassword(Exception):
    pass


def csrf_token(request: Request) -> str:
    tok = request.session.get("csrf")
    if not tok:
        tok = secrets.token_urlsafe(24)
        request.session["csrf"] = tok
    return tok


def flash(request: Request, message: str, level: str = "success") -> None:
    request.session.setdefault("flash", []).append([level, message])


def pop_flash(request: Request) -> list[list[str]]:
    return request.session.pop("flash", [])


def current_user(request: Request, db: Session = Depends(get_db)) -> User:
    uid = request.session.get("uid")
    user = db.get(User, uid) if uid else None
    if user is None or not user.active:
        request.session.pop("uid", None)
        raise LoginRequired()
    if user.must_change_password and request.url.path not in ("/profile", "/logout"):
        raise ForcePassword()
    request.state.user = user
    return user


def require(perm: str):
    def dep(user: User = Depends(current_user)) -> User:
        if not user.has(perm):
            raise Forbidden(perm)
        return user
    return dep


async def checked_form(request: Request) -> dict[str, Any]:
    form = await request.form()
    if not secrets.compare_digest(str(form.get("csrf", "")), request.session.get("csrf", "") or "x"):
        raise CSRFError()
    data: dict[str, Any] = {}
    for key in form.keys():
        values = form.getlist(key)
        data[key] = values if len(values) > 1 or key.endswith("[]") else values[0]
    return data


def audit(db: Session, request: Request | None, user: User | None, action: str, object_type: str = "",
          object_id: Any = "", details: str = "") -> None:
    ip = request.client.host if request and request.client else ""
    db.add(AuditLog(user_id=user.id if user else None, username=user.username if user else "", action=action,
                    object_type=object_type, object_id=str(object_id or ""), details=details[:4000], ip=ip))


def render(request: Request, name: str, **ctx: Any):
    user = getattr(request.state, "user", None)
    base = {
        "user": user,
        "has": (lambda p: bool(user and user.has(p))),
        "csrf": csrf_token(request),
        "flashes": pop_flash(request),
        "site_name": settings_store.get("site_name"),
        "version": __version__,
        "fmt_dt": fmt_dt,
        "CATEGORIES": CATEGORIES,
        "ALARM_STATUS": ALARM_STATUS,
        "RESOLUTIONS": RESOLUTIONS,
        "PERMISSIONS": PERMISSIONS,
        "sound_alerts": settings_store.get("sound_alerts"),
    }
    base.update(ctx)
    return templates.TemplateResponse(request, name, base)


def visible_groups(user: User) -> list[str]:
    """Grupos de clientes visibles para el usuario (lista vacía = sin restricción)."""
    return list(user.client_groups or [])


def b(v: Any) -> bool:
    return str(v).lower() in ("1", "on", "true", "yes", "si")


def s(v: Any) -> str:
    return (v or "").strip() if isinstance(v, str) else ("" if v is None else str(v))


def scope(q, user: User, client_col):
    """Aplica la restricción por grupos de clientes del usuario a una consulta."""
    from sqlalchemy import select

    from ..models import Client

    groups = visible_groups(user)
    if not groups:
        return q
    return q.filter(client_col.in_(select(Client.id).where(Client.group.in_(groups))))


def can_see_client(user: User, client) -> bool:
    groups = visible_groups(user)
    return not groups or (client is not None and client.group in groups)
