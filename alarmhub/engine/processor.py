"""Procesador central: recibe mensajes de todos los receptores, los persiste,
los asocia a panel/cliente, genera o agrupa alarmas y lanza los workflows.

Los mensajes se procesan en serie a través de una cola para garantizar el orden
y evitar contención en la base de datos. El receptor espera a que el mensaje esté
guardado antes de enviar el ACK al panel (no se confirma nada que no esté persistido).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from sqlalchemy.orm import Session

from .. import settings_store
from ..bus import bus
from ..db import session_scope
from ..models import Alarm, Event, Panel, utcnow
from ..protocols.normalized import IncomingMessage, NormalizedEvent, from_sia
from . import alarms as alarm_ops
from .context import event_context
from .workflows import engine

log = logging.getLogger("alarmhub.processor")


@dataclass
class _Item:
    msg: IncomingMessage
    future: asyncio.Future


@dataclass
class ProcessResult:
    event_ids: list[int] = field(default_factory=list)
    panel_id: int | None = None
    known: bool = False


def find_panel(db: Session, account: str, line_prefix: str = "") -> Panel | None:
    if not account:
        return None
    candidates = db.query(Panel).filter(Panel.account == account.upper(), Panel.active.is_(True)).all()
    for p in candidates:
        if p.line_prefix and p.line_prefix == line_prefix:
            return p
    for p in candidates:
        if not p.line_prefix:
            return p
    return None


class Processor:
    def __init__(self) -> None:
        self.queue: asyncio.Queue[_Item] = asyncio.Queue(maxsize=10000)
        self._task: asyncio.Task | None = None
        self.stats: dict[str, Any] = {"messages": 0, "events": 0, "link_tests": 0, "errors": 0,
                                      "nak": 0, "started": utcnow().isoformat()}

    async def start(self) -> None:
        self.queue = asyncio.Queue(maxsize=10000)  # ligada al bucle de eventos actual
        self._task = asyncio.create_task(self._loop(), name="processor")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        await engine.shutdown()

    async def submit(self, msg: IncomingMessage) -> ProcessResult:
        fut = asyncio.get_running_loop().create_future()
        await self.queue.put(_Item(msg, fut))
        return await fut

    async def _loop(self) -> None:
        while True:
            item = await self.queue.get()
            try:
                result, post = self._process(item.msg)
                if not item.future.done():
                    item.future.set_result(result)
                self._after_commit(post)
            except Exception as exc:
                self.stats["errors"] += 1
                log.exception("Error procesando mensaje %r", item.msg.raw)
                if not item.future.done():
                    item.future.set_exception(exc)

    # ------------------------------------------------------------------ núcleo
    def _process(self, msg: IncomingMessage) -> tuple[ProcessResult, list[dict[str, Any]]]:
        now = utcnow()
        self.stats["messages"] += 1
        post: list[dict[str, Any]] = []
        res = ProcessResult()
        with session_scope() as db:
            panel = find_panel(db, msg.account, msg.line_prefix)
            events = list(msg.events)
            if panel:
                res.panel_id, res.known = panel.id, True
                if msg.protocol != "INTERNAL":
                    panel.last_seen = now
                    panel.last_remote = msg.remote
                    if panel.supervision_failed:
                        panel.supervision_failed = False
                        events.append(from_sia("SUPR"))
            if msg.is_link_test:
                self.stats["link_tests"] += 1
            if not events:
                if panel:
                    bus.publish("link", {"panel_id": panel.id, "account": panel.account})
                return res, post

            cats = set(settings_store.get("alarm_categories") or [])
            for nev in events:
                ev = self._store_event(db, msg, nev, panel, now)
                ctx = event_context(db, ev)
                waiters = engine.check_waiters(ctx)
                consumed = any(w.step.get("consume", True) for w in waiters)
                workflows = [] if consumed else engine.match_workflows(db, ctx)
                created = self._handle_alarm(db, ev, panel, cats, waiters, workflows)
                db.flush()
                ctx["alarm_id"] = ev.alarm_id
                ctx["alarm_created"] = created
                for w in waiters:
                    if w.run.alarm_id is None and ev.alarm_id:
                        w.run.alarm_id = ev.alarm_id
                res.event_ids.append(ev.id)
                post.append({"ctx": ctx, "waiters": waiters, "workflows": workflows,
                             "alarm": alarm_ops.alarm_summary(ev.alarm) if ev.alarm else None})
                self.stats["events"] += 1
        return res, post

    def _store_event(self, db: Session, msg: IncomingMessage, nev: NormalizedEvent, panel: Panel | None,
                     now) -> Event:
        ev = Event(received_at=now, account=(msg.account or "").upper(), line_prefix=msg.line_prefix,
                   receiver=msg.receiver, protocol=msg.protocol, transport=msg.transport, remote=msg.remote,
                   encrypted=msg.encrypted, sequence=msg.sequence, code=nev.code, qualifier=nev.qualifier,
                   description=nev.description[:255], category=nev.category, priority=nev.priority,
                   zone=nev.zone, partition=nev.partition, user=nev.user, text=(nev.text or None),
                   panel_timestamp=msg.panel_timestamp, raw=msg.raw, extra=nev.extra or {})
        if panel:
            ev.panel_id = panel.id
            ev.client_id = panel.client_id
            ev.test_mode = panel.in_test
            panel.last_event_at = now
            if nev.category == "open":
                panel.armed_state = "desarmado"
            elif nev.category == "close":
                panel.armed_state = "armado"
        db.add(ev)
        db.flush()
        return ev

    def _handle_alarm(self, db: Session, ev: Event, panel: Panel | None, cats: set[str], waiters,
                      workflows) -> bool:
        """Asocia el evento a una alarma. Devuelve True si se ha creado una alarma nueva."""
        # 1) señal esperada por un workflow: se une a la alarma de ese workflow
        for w in waiters:
            if w.run.alarm_id:
                a = db.get(Alarm, w.run.alarm_id)
                if a and a.status != "closed":
                    alarm_ops.attach(a, ev)
                    return False
        if ev.test_mode:
            return False
        # 2) restauraciones: se asocian a la alarma abierta (si existe)
        if ev.category == "restore":
            a = alarm_ops.find_open_alarm(db, panel, ev.account, include_pending=True)
            if a:
                alarm_ops.attach(a, ev)
            return False
        if ev.category not in cats:
            return False
        # 3) agrupación en alarma abierta o creación
        a = alarm_ops.find_open_alarm(db, panel, ev.account)
        if a:
            alarm_ops.attach(a, ev)
            return False
        hold = any((wf.definition or {}).get("hold_alarm") for wf in workflows)
        alarm_ops.create_alarm(db, ev, status="pending" if hold else "new")
        return True

    def _after_commit(self, post: list[dict[str, Any]]) -> None:
        for p in post:
            ctx = p["ctx"]
            bus.publish("event", {"event": {k: ctx[k] for k in (
                "event_id", "received_at", "account", "code", "description", "category", "priority", "zone",
                "zone_name", "client_name", "client_group", "alarm_id", "protocol")}})
            if p["alarm"]:
                bus.publish("alarm", {"action": "event", "alarm": p["alarm"]})
            for w in p["waiters"]:
                if not w.future.done():
                    w.future.set_result(ctx)
            if p["workflows"]:
                engine.start_runs(p["workflows"], ctx)


processor = Processor()


# ---------------------------------------------------------------------- tareas periódicas
async def supervision_loop(interval: int = 30) -> None:
    """Genera un evento interno SUPV cuando un panel supera su tiempo de supervisión."""
    last_cleanup = utcnow() - timedelta(days=1)
    while True:
        try:
            now = utcnow()
            pending: list[tuple[str, str]] = []
            with session_scope() as db:
                for p in db.query(Panel).filter(Panel.active.is_(True), Panel.supervision_minutes > 0,
                                                Panel.supervision_failed.is_(False)):
                    ref = p.last_seen or p.created_at
                    if ref and now - ref > timedelta(minutes=p.supervision_minutes):
                        p.supervision_failed = True
                        pending.append((p.account, p.line_prefix))
            for account, prefix in pending:
                await processor.submit(IncomingMessage(protocol="INTERNAL", account=account, line_prefix=prefix,
                                                       transport="internal", raw="supervision timeout",
                                                       events=[from_sia("SUPV")]))
            if now - last_cleanup > timedelta(hours=6):
                last_cleanup = now
                days = int(settings_store.get("events_retention_days") or 0)
                if days > 0:
                    with session_scope() as db:
                        n = db.query(Event).filter(Event.received_at < now - timedelta(days=days),
                                                   Event.alarm_id.is_(None)).delete(synchronize_session=False)
                        if n:
                            log.info("Retención: eliminadas %s señales antiguas", n)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Error en el bucle de supervisión")
        await asyncio.sleep(interval)
