"""API REST v1 (autenticación por cabecera ``X-API-Key`` o sesión web)."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..db import get_db
from ..engine import alarms as alarm_ops
from ..engine.processor import processor
from ..models import Alarm, Client, Event, Panel, User
from ..protocols.normalized import IncomingMessage, from_any_code
from ..receivers import dc09_server, surgard_server
from .deps import scope

router = APIRouter(prefix="/api/v1", tags=["api"])


def api_user(request: Request, x_api_key: str | None = Header(default=None),
             db: Session = Depends(get_db)) -> User:
    user = None
    if x_api_key:
        user = db.query(User).filter_by(api_key=x_api_key, active=True).first()
    elif request.session.get("uid"):
        user = db.get(User, request.session["uid"])
    if user is None or not user.active:
        raise HTTPException(401, "API key inválida")
    return user


def need(user: User, perm: str) -> None:
    if not user.has(perm):
        raise HTTPException(403, f"Permiso requerido: {perm}")


class IngestEvent(BaseModel):
    account: str = Field(..., max_length=16)
    code: str = Field(..., description="SIA (BA), Contact ID (E130/R130/1130) o código propio")
    zone: str | None = None
    partition: str | None = None
    user: str | None = None
    text: str | None = None
    line_prefix: str = ""
    source: str = "HTTP"
    description: str | None = None
    category: str | None = None
    priority: int | None = None
    extra: dict[str, Any] = {}


@router.post("/ingest")
async def ingest(body: IngestEvent | list[IngestEvent], request: Request, user: User = Depends(api_user)):
    """Recepción genérica para dispositivos IP, cámaras, IoT, otras plataformas..."""
    need(user, "api.ingest")
    items = body if isinstance(body, list) else [body]
    ids: list[int] = []
    for it in items:
        ev = from_any_code(it.code, zone=it.zone, partition=it.partition, user=it.user, text=it.text)
        if it.description:
            ev.description = it.description
        if it.category:
            ev.category = it.category
        if it.priority:
            ev.priority = it.priority
        ev.extra = it.extra
        msg = IncomingMessage(protocol=it.source[:16].upper() or "HTTP", account=it.account.upper(),
                              line_prefix=it.line_prefix, raw=it.model_dump_json(), transport="http",
                              remote=request.client.host if request.client else "", events=[ev])
        res = await processor.submit(msg)
        ids += res.event_ids
    return {"ok": True, "event_ids": ids}


def _alarm_json(a: Alarm) -> dict:
    d = alarm_ops.alarm_summary(a)
    d.update({"client_id": a.client_id, "panel_id": a.panel_id, "resolution": a.resolution, "tags": a.tags,
              "closed_at": a.closed_at.isoformat() + "Z" if a.closed_at else None})
    return d


def _event_json(e: Event) -> dict:
    return {"id": e.id, "received_at": e.received_at.isoformat() + "Z", "account": e.account, "code": e.code,
            "description": e.description, "category": e.category, "priority": e.priority, "zone": e.zone,
            "partition": e.partition, "user": e.user, "protocol": e.protocol, "panel_id": e.panel_id,
            "client_id": e.client_id, "alarm_id": e.alarm_id, "encrypted": e.encrypted, "raw": e.raw}


@router.get("/alarms")
def api_alarms(status: str = "open", limit: int = 100, user: User = Depends(api_user), db: Session = Depends(get_db)):
    need(user, "alarms.view")
    q = scope(db.query(Alarm), user, Alarm.client_id)
    if status == "open":
        q = q.filter(Alarm.status.in_(("new", "in_progress")))
    elif status:
        q = q.filter(Alarm.status == status)
    return [_alarm_json(a) for a in q.order_by(Alarm.priority, Alarm.id.desc()).limit(min(limit, 1000))]


@router.get("/alarms/{alarm_id}")
def api_alarm(alarm_id: int, user: User = Depends(api_user), db: Session = Depends(get_db)):
    need(user, "alarms.view")
    a = db.get(Alarm, alarm_id)
    if not a:
        raise HTTPException(404)
    d = _alarm_json(a)
    d["events"] = [_event_json(e) for e in a.events]
    d["comments"] = [{"text": c.text, "kind": c.kind, "at": c.created_at.isoformat() + "Z",
                      "user": c.user.username if c.user else None} for c in a.comments]
    return d


class CloseBody(BaseModel):
    resolution: str = "other"
    comment: str = ""


@router.post("/alarms/{alarm_id}/close")
def api_alarm_close(alarm_id: int, body: CloseBody, user: User = Depends(api_user), db: Session = Depends(get_db)):
    need(user, "alarms.handle")
    a = db.get(Alarm, alarm_id)
    if not a:
        raise HTTPException(404)
    alarm_ops.set_status(db, a, "closed", user_id=user.id, resolution=body.resolution,
                         comment=body.comment or "Cerrada por API", kind="note")
    db.commit()
    alarm_ops.publish(a, "close")
    return _alarm_json(a)


@router.get("/events")
def api_events(account: str | None = None, since_id: int = 0, limit: int = 100, user: User = Depends(api_user),
               db: Session = Depends(get_db)):
    need(user, "events.view")
    q = scope(db.query(Event), user, Event.client_id).filter(Event.id > since_id)
    if account:
        q = q.filter(Event.account == account.upper())
    order = Event.id if since_id else Event.id.desc()
    return [_event_json(e) for e in q.order_by(order).limit(min(limit, 1000))]


@router.get("/clients")
def api_clients(user: User = Depends(api_user), db: Session = Depends(get_db)):
    need(user, "clients.view")
    return [{"id": c.id, "code": c.code, "name": c.name, "group": c.group, "city": c.city, "phone": c.phone,
             "panels": [p.account for p in c.panels]}
            for c in scope(db.query(Client), user, Client.id).order_by(Client.name)]


class PanelBody(BaseModel):
    account: str
    line_prefix: str = ""
    client_code: str | None = None
    name: str = ""
    protocol: str = "SIA-DCS"
    enc_key: str = ""
    supervision_minutes: int = 0


@router.get("/panels")
def api_panels(user: User = Depends(api_user), db: Session = Depends(get_db)):
    need(user, "clients.view")
    return [{"id": p.id, "account": p.account, "line_prefix": p.line_prefix, "name": p.name,
             "client_id": p.client_id, "last_seen": p.last_seen.isoformat() + "Z" if p.last_seen else None,
             "supervision_failed": p.supervision_failed, "armed_state": p.armed_state}
            for p in scope(db.query(Panel), user, Panel.client_id).order_by(Panel.account)]


@router.post("/panels")
def api_panel_upsert(body: PanelBody, user: User = Depends(api_user), db: Session = Depends(get_db)):
    need(user, "clients.edit")
    p = db.query(Panel).filter_by(account=body.account.upper(), line_prefix=body.line_prefix).first() or Panel()
    p.account, p.line_prefix, p.name, p.protocol = body.account.upper(), body.line_prefix, body.name, body.protocol
    p.supervision_minutes = body.supervision_minutes
    if body.enc_key:
        p.enc_key = body.enc_key
    if body.client_code:
        c = db.query(Client).filter_by(code=body.client_code).first()
        if not c:
            raise HTTPException(404, "Cliente no encontrado")
        p.client_id = c.id
    db.add(p)
    db.commit()
    return {"id": p.id}


@router.get("/status")
def api_status(user: User = Depends(api_user)):
    return {"processor": processor.stats, "queue": processor.queue.qsize(), "dc09": dc09_server.STATUS,
            "surgard": surgard_server.STATUS}
