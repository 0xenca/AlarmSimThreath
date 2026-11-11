"""Operaciones sobre alarmas (incidencias), compartidas por el procesador, los
workflows y la interfaz web."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from .. import settings_store
from ..bus import bus
from ..models import Alarm, Comment, Event, Panel, utcnow

OPEN_STATES = ("pending", "new", "in_progress")


def alarm_summary(a: Alarm) -> dict:
    return {
        "id": a.id, "status": a.status, "priority": a.priority, "title": a.title, "account": a.account,
        "client": a.client.name if a.client else "",
        "client_group": a.client.group if a.client else "", "category": a.category, "event_count": a.event_count,
        "restored": a.restored, "opened_at": a.opened_at.isoformat() + "Z",
    }


def add_comment(db: Session, alarm: Alarm | None, text: str, kind: str = "system", user_id: int | None = None,
                event_id: int | None = None, client_id: int | None = None) -> Comment:
    c = Comment(alarm_id=alarm.id if alarm else None, text=text, kind=kind, user_id=user_id, event_id=event_id,
                client_id=client_id if client_id else (alarm.client_id if alarm else None))
    db.add(c)
    return c


def find_open_alarm(db: Session, panel: Panel | None, account: str, include_pending: bool = False) -> Alarm | None:
    states = OPEN_STATES if include_pending else ("new", "in_progress")
    q = db.query(Alarm).filter(Alarm.status.in_(states))
    q = q.filter(Alarm.panel_id == panel.id) if panel else q.filter(Alarm.panel_id.is_(None), Alarm.account == account)
    window = int(settings_store.get("group_window_minutes") or 0)
    if window > 0:
        q = q.filter(Alarm.last_event_at >= utcnow() - timedelta(minutes=window))
    return q.order_by(Alarm.id.desc()).first()


def attach(alarm: Alarm, ev: Event) -> None:
    ev.alarm_id = alarm.id
    alarm.event_count = (alarm.event_count or 0) + 1
    alarm.last_event_at = utcnow()
    if ev.category == "restore":
        alarm.restored = True
    elif ev.priority < alarm.priority:
        alarm.priority = ev.priority
        alarm.category = ev.category
        alarm.title = _title(ev)
        alarm.restored = False


def _title(ev: Event) -> str:
    show_zone = ev.zone and ev.zone != "0" and ev.category not in ("open", "close")
    return ev.description + (f" · zona {ev.zone}" if show_zone else "")


def create_alarm(db: Session, ev: Event, status: str = "new", priority: int | None = None,
                 title: str | None = None) -> Alarm:
    a = Alarm(panel_id=ev.panel_id, client_id=ev.client_id, status=status,
              priority=priority if priority is not None else ev.priority, category=ev.category,
              title=title or _title(ev), account=ev.account, event_count=0)
    db.add(a)
    db.flush()
    attach(a, ev)
    if status == "pending":
        add_comment(db, a, "Alarma retenida en buffer por workflow", kind="workflow", event_id=ev.id)
    return a


def set_status(db: Session, alarm: Alarm, status: str, user_id: int | None = None, resolution: str = "",
               comment: str | None = None, kind: str = "system") -> None:
    prev = alarm.status
    alarm.status = status
    now = utcnow()
    if status == "in_progress" and not alarm.acknowledged_at:
        alarm.acknowledged_at = now
        if user_id:
            alarm.assigned_user_id = user_id
    if status == "closed":
        alarm.closed_at = now
        alarm.closed_by_id = user_id
        alarm.resolution = resolution or alarm.resolution
    if status in ("new", "in_progress") and prev == "closed":
        alarm.closed_at = None
    if comment:
        add_comment(db, alarm, comment, kind=kind, user_id=user_id)


def publish(alarm: Alarm, action: str) -> None:
    bus.publish("alarm", {"action": action, "alarm": alarm_summary(alarm)})
