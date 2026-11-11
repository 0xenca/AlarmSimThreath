"""Login, panel principal, cola de alarmas, histórico de señales y tiempo real."""
from __future__ import annotations

import asyncio
import csv
import io
import json
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse, StreamingResponse
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from ..bus import bus
from ..db import get_db
from ..engine import alarms as alarm_ops
from ..engine.processor import processor
from ..models import Alarm, Client, Event, Panel, User, WorkflowRun, utcnow
from ..receivers import dc09_server, surgard_server
from ..security import hash_password, verify_password
from .deps import (audit, can_see_client, checked_form, current_user, flash, render, require, s, scope)

router = APIRouter()


# ------------------------------------------------------------------ autenticación
@router.get("/login")
def login_form(request: Request):
    return render(request, "login.html")


@router.post("/login")
def login(request: Request, form: dict = Depends(checked_form), db: Session = Depends(get_db)):
    user = db.query(User).filter_by(username=s(form.get("username"))).first()
    if not user or not user.active or not verify_password(form.get("password", ""), user.password_hash):
        audit(db, request, None, "login_failed", "user", details=s(form.get("username")))
        db.commit()
        flash(request, "Usuario o contraseña incorrectos", "danger")
        return RedirectResponse("/login", 303)
    request.session.clear()
    request.session["uid"] = user.id
    user.last_login = utcnow()
    audit(db, request, user, "login", "user", user.id)
    db.commit()
    return RedirectResponse("/profile" if user.must_change_password else "/", 303)


@router.get("/logout")
def logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", 303)


@router.get("/profile")
def profile(request: Request, user: User = Depends(current_user)):
    return render(request, "profile.html")


@router.post("/profile")
def profile_save(request: Request, form: dict = Depends(checked_form), user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    user.full_name = s(form.get("full_name"))
    user.email = s(form.get("email"))
    user.phone = s(form.get("phone"))
    new = form.get("new_password", "")
    if new:
        if not verify_password(form.get("current_password", ""), user.password_hash):
            flash(request, "La contraseña actual no es correcta", "danger")
            return RedirectResponse("/profile", 303)
        if len(new) < 8 or new != form.get("new_password2"):
            flash(request, "La nueva contraseña debe tener 8 caracteres o más y coincidir", "danger")
            return RedirectResponse("/profile", 303)
        user.password_hash = hash_password(new)
        user.must_change_password = False
        audit(db, request, user, "password_change", "user", user.id)
    elif user.must_change_password:
        flash(request, "Debe establecer una nueva contraseña", "warning")
        return RedirectResponse("/profile", 303)
    db.commit()
    flash(request, "Perfil actualizado")
    return RedirectResponse("/profile", 303)


# ------------------------------------------------------------------ dashboard
@router.get("/")
def dashboard(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    since = utcnow() - timedelta(hours=24)
    alarms_q = scope(db.query(Alarm), user, Alarm.client_id)
    open_counts = dict(alarms_q.filter(Alarm.status.in_(("pending", "new", "in_progress")))
                       .with_entities(Alarm.status, func.count()).group_by(Alarm.status).all())
    ev_q = scope(db.query(Event), user, Event.client_id).filter(Event.received_at >= since)
    by_cat = dict(ev_q.with_entities(Event.category, func.count()).group_by(Event.category).all())
    hourly_rows = ev_q.with_entities(Event.received_at).all()
    hours = [0] * 24
    now = utcnow()
    for (ts,) in hourly_rows:
        idx = 23 - int((now - ts).total_seconds() // 3600)
        if 0 <= idx < 24:
            hours[idx] += 1
    panels_total = db.query(Panel).filter_by(active=True).count()
    panels_supv = db.query(Panel).filter_by(active=True, supervision_failed=True).count()
    unknown = db.query(func.count(func.distinct(Event.account))).filter(Event.panel_id.is_(None),
                                                                        Event.received_at >= since).scalar()
    active = (alarms_q.filter(Alarm.status.in_(("new", "in_progress")))
              .order_by(Alarm.priority, Alarm.opened_at).limit(50).all())
    recent = scope(db.query(Event), user, Event.client_id).order_by(Event.id.desc()).limit(25).all()
    waiting = db.query(WorkflowRun).filter(WorkflowRun.status == "waiting").count()
    return render(request, "dashboard.html", open_counts=open_counts, by_cat=by_cat, hours=hours,
                  panels_total=panels_total, panels_supv=panels_supv, unknown=unknown, active=active,
                  recent=recent, waiting=waiting, stats=processor.stats, dc09=dc09_server.STATUS,
                  surgard=surgard_server.STATUS, queue_size=processor.queue.qsize(),
                  clients_total=db.query(Client).count())


# ------------------------------------------------------------------ alarmas
@router.get("/alarms")
def alarms_list(request: Request, status: str = "open", q: str = "", page: int = 1,
                user: User = Depends(require("alarms.view")), db: Session = Depends(get_db)):
    query = scope(db.query(Alarm), user, Alarm.client_id)
    if status == "open":
        query = query.filter(Alarm.status.in_(("new", "in_progress")))
    elif status:
        query = query.filter(Alarm.status == status)
    if q:
        like = f"%{q}%"
        query = query.outerjoin(Client, Alarm.client_id == Client.id).filter(
            or_(Alarm.account.ilike(like), Alarm.title.ilike(like), Client.name.ilike(like), Client.code.ilike(like)))
    total = query.count()
    order = (Alarm.priority, Alarm.opened_at) if status in ("open", "new", "in_progress", "pending") \
        else (Alarm.id.desc(),)
    items = query.order_by(*order).offset((page - 1) * 50).limit(50).all()
    return render(request, "alarms.html", items=items, status=status, q=q, page=page, total=total)


def _get_alarm(db: Session, user: User, alarm_id: int) -> Alarm:
    from fastapi import HTTPException
    a = db.get(Alarm, alarm_id)
    if a is None or not can_see_client(user, a.client) and a.client_id is not None:
        raise HTTPException(404, "Alarma no encontrada")
    return a


@router.get("/alarms/{alarm_id}")
def alarm_detail(alarm_id: int, request: Request, user: User = Depends(require("alarms.view")),
                 db: Session = Depends(get_db)):
    a = _get_alarm(db, user, alarm_id)
    history = []
    if a.panel_id:
        history = (db.query(Event).filter(Event.panel_id == a.panel_id, Event.alarm_id != a.id)
                   .order_by(Event.id.desc()).limit(20).all())
    runs = db.query(WorkflowRun).filter_by(alarm_id=a.id).order_by(WorkflowRun.id).all()
    operators = db.query(User).filter_by(active=True).order_by(User.username).all()
    return render(request, "alarm_detail.html", a=a, history=history, runs=runs, operators=operators)


@router.post("/alarms/{alarm_id}/action")
def alarm_action(alarm_id: int, request: Request, form: dict = Depends(checked_form),
                 user: User = Depends(require("alarms.handle")), db: Session = Depends(get_db)):
    a = _get_alarm(db, user, alarm_id)
    action = form.get("action")
    text = s(form.get("text"))
    if action == "take":
        alarm_ops.set_status(db, a, "in_progress", user_id=user.id, comment=f"Alarma atendida por {user.username}")
        a.assigned_user_id = user.id
    elif action == "comment" and text:
        alarm_ops.add_comment(db, a, text, kind=form.get("kind") or "note", user_id=user.id)
    elif action == "close":
        resolution = form.get("resolution") or "other"
        alarm_ops.set_status(db, a, "closed", user_id=user.id, resolution=resolution,
                             comment=text or f"Cerrada: {resolution}", kind="note")
    elif action == "reopen":
        alarm_ops.set_status(db, a, "in_progress", user_id=user.id, comment=f"Reabierta por {user.username}")
    elif action == "release":
        alarm_ops.set_status(db, a, "new", user_id=user.id, comment=f"Liberada manualmente por {user.username}")
    elif action == "assign":
        target = db.get(User, int(form.get("user_id") or 0))
        a.assigned_user_id = target.id if target else None
        alarm_ops.add_comment(db, a, f"Asignada a {target.username if target else 'nadie'}", user_id=user.id)
    elif action == "priority":
        a.priority = max(1, min(5, int(form.get("priority") or a.priority)))
        alarm_ops.add_comment(db, a, f"Prioridad cambiada a {a.priority}", user_id=user.id)
    audit(db, request, user, f"alarm_{action}", "alarm", a.id, text)
    db.commit()
    alarm_ops.publish(a, action or "updated")
    return RedirectResponse(f"/alarms/{a.id}", 303)


@router.post("/alarms/bulk")
def alarms_bulk(request: Request, form: dict = Depends(checked_form),
                user: User = Depends(require("alarms.handle")), db: Session = Depends(get_db)):
    ids = form.get("ids[]") or form.get("ids") or []
    if isinstance(ids, str):
        ids = [ids]
    n = 0
    for i in ids:
        a = _get_alarm(db, user, int(i))
        if a.status != "closed":
            alarm_ops.set_status(db, a, "closed", user_id=user.id, resolution=form.get("resolution") or "other",
                                 comment=s(form.get("text")) or "Cierre masivo", kind="note")
            n += 1
    audit(db, request, user, "alarm_bulk_close", "alarm", details=",".join(map(str, ids)))
    db.commit()
    flash(request, f"{n} alarmas cerradas")
    return RedirectResponse("/alarms", 303)


# ------------------------------------------------------------------ señales
def _events_query(db: Session, user: User, params: dict):
    q = scope(db.query(Event), user, Event.client_id)
    if params.get("account"):
        q = q.filter(Event.account.ilike(f"%{params['account']}%"))
    if params.get("client_id"):
        q = q.filter(Event.client_id == int(params["client_id"]))
    if params.get("panel_id"):
        q = q.filter(Event.panel_id == int(params["panel_id"]))
    if params.get("category"):
        q = q.filter(Event.category == params["category"])
    if params.get("code"):
        q = q.filter(Event.code.ilike(f"%{params['code']}%"))
    if params.get("protocol"):
        q = q.filter(Event.protocol == params["protocol"])
    if params.get("unknown"):
        q = q.filter(Event.panel_id.is_(None))
    for key in ("date_from", "date_to"):
        if params.get(key):
            try:
                dt = datetime.fromisoformat(params[key]).astimezone(timezone.utc).replace(tzinfo=None)
            except ValueError:
                continue
            q = q.filter(Event.received_at >= dt if key == "date_from" else Event.received_at <= dt)
    return q


@router.get("/events")
def events_list(request: Request, page: int = 1, user: User = Depends(require("events.view")),
                db: Session = Depends(get_db)):
    params = dict(request.query_params)
    q = _events_query(db, user, params)
    total = q.count()
    items = q.order_by(Event.id.desc()).offset((page - 1) * 100).limit(100).all()
    protocols = [p for (p,) in db.query(Event.protocol).distinct()]
    from urllib.parse import urlencode
    qs = urlencode({k: v for k, v in params.items() if k != "page"})
    return render(request, "events.html", items=items, params=params, page=page, total=total, protocols=protocols,
                  qs=qs)


@router.get("/events/export.csv")
def events_export(request: Request, user: User = Depends(require("events.export")), db: Session = Depends(get_db)):
    from ..engine.context import fmt_dt
    params = dict(request.query_params)
    q = _events_query(db, user, params).order_by(Event.id.desc()).limit(100000)

    def gen():
        buf = io.StringIO()
        w = csv.writer(buf, delimiter=";")
        w.writerow(["id", "fecha", "abonado", "cliente", "protocolo", "codigo", "descripcion", "categoria",
                    "prioridad", "zona", "particion", "usuario", "alarma", "remoto", "cifrado", "raw"])
        yield buf.getvalue()
        for e in q.yield_per(1000):
            buf.seek(0)
            buf.truncate()
            w.writerow([e.id, fmt_dt(e.received_at), e.account, e.client.name if e.client else "", e.protocol,
                        e.code, e.description, e.category, e.priority, e.zone or "", e.partition or "",
                        e.user or "", e.alarm_id or "", e.remote, "si" if e.encrypted else "no", e.raw])
            yield buf.getvalue()

    audit(db, request, user, "events_export", "event", details=str(params))
    db.commit()
    return StreamingResponse(gen(), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=senales.csv"})


@router.get("/events/{event_id}")
def event_detail(event_id: int, request: Request, user: User = Depends(require("events.view")),
                 db: Session = Depends(get_db)):
    from fastapi import HTTPException
    e = db.get(Event, event_id)
    if e is None or (e.client_id and not can_see_client(user, e.client)):
        raise HTTPException(404)
    from ..models import Comment
    comments = db.query(Comment).filter_by(event_id=e.id).order_by(Comment.id).all()
    runs = db.query(WorkflowRun).filter_by(event_id=e.id).all()
    return render(request, "event_detail.html", e=e, comments=comments, runs=runs)


@router.post("/events/{event_id}/comment")
def event_comment(event_id: int, request: Request, form: dict = Depends(checked_form),
                  user: User = Depends(require("events.view")), db: Session = Depends(get_db)):
    e = db.get(Event, event_id)
    if e and s(form.get("text")):
        alarm_ops.add_comment(db, None, s(form.get("text")), kind="note", user_id=user.id, event_id=e.id,
                              client_id=e.client_id)
        db.commit()
    return RedirectResponse(f"/events/{event_id}", 303)


# ------------------------------------------------------------------ tiempo real
@router.get("/stream")
async def stream(request: Request, user: User = Depends(current_user)):
    groups = list(user.client_groups or [])
    queue = bus.subscribe()

    async def gen():
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
                    continue
                if groups:
                    data = json.loads(payload)
                    group = (data.get("alarm") or data.get("event") or {}).get("client_group")
                    if data.get("type") in ("alarm", "event") and group not in groups:
                        continue
                yield f"data: {payload}\n\n"
        finally:
            bus.unsubscribe(queue)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
