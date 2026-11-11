"""Motor de workflows.

Un workflow es un documento JSON::

    {
      "trigger":   { ...condiciones (ver context.matches)... },
      "hold_alarm": false,          # true = la alarma nace retenida en buffer (invisible al operador)
      "steps":     [ {"type": "...", ...}, ... ]
    }

Pasos disponibles (ver STEP_DOCS): delay, wait_for, count, condition, hold_alarm,
release_alarm, close_alarm, create_alarm, set_priority, assign, tag, comment,
email, webhook, telegram, notify, stop.

Las esperas (``wait_for``) son en memoria. Al reiniciar el servicio, las
ejecuciones pendientes se marcan como interrumpidas y las alarmas retenidas se
liberan a la cola (fail-safe: nunca se pierde una alarma).
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Awaitable, Callable

from sqlalchemy.orm import Session

from ..bus import bus
from ..db import session_scope
from ..models import Alarm, Client, Event, User, Workflow, WorkflowRun, utcnow
from . import alarms as alarm_ops
from . import notify
from .context import _as_list, code_matches, matches, render

log = logging.getLogger("alarmhub.workflows")


STEP_DOCS: dict[str, dict[str, Any]] = {
    "delay": {"desc": "Espera N segundos", "example": {"type": "delay", "seconds": 30}},
    "wait_for": {
        "desc": "Espera otra señal del mismo panel. Si llega ejecuta on_match, si no on_timeout. "
                "consume=true evita que la señal recibida dispare sus propios workflows.",
        "example": {"type": "wait_for", "codes": ["BA", "E130"], "categories": [], "same_zone": False,
                    "different_zone": True, "timeout": 120, "consume": True,
                    "on_match": [{"type": "release_alarm", "comment": "Alarma confirmada por segunda zona"}],
                    "on_timeout": [{"type": "close_alarm", "resolution": "auto",
                                    "comment": "No confirmada en 120 s"}]},
    },
    "count": {
        "desc": "Cuenta señales del panel en una ventana de tiempo (incluida la actual)",
        "example": {"type": "count", "codes": ["BA"], "window": 600, "threshold": 3, "same_zone": True,
                    "then": [{"type": "set_priority", "value": 1}], "else": []},
    },
    "condition": {
        "desc": "Evalúa condiciones (mismo formato que el trigger)",
        "example": {"type": "condition", "match": {"schedule": {"days": [5, 6], "from": "00:00", "to": "23:59"}},
                    "then": [], "else": []},
    },
    "hold_alarm": {"desc": "Retiene la alarma en buffer (oculta en la cola)", "example": {"type": "hold_alarm"}},
    "release_alarm": {"desc": "Libera la alarma a la cola del operador",
                      "example": {"type": "release_alarm", "comment": "Liberada"}},
    "close_alarm": {"desc": "Cierra la alarma (solo si la originó este evento o está en buffer; force=true para "
                             "cerrarla siempre)", "example": {"type": "close_alarm", "resolution": "auto",
                                                              "comment": "Cerrada por workflow"}},
    "create_alarm": {"desc": "Crea una alarma aunque la categoría no la genere",
                     "example": {"type": "create_alarm", "priority": 3, "title": "{description} ({client_name})"}},
    "set_priority": {"desc": "Cambia la prioridad (1 = máxima)", "example": {"type": "set_priority", "value": 1}},
    "assign": {"desc": "Asigna la alarma a un usuario", "example": {"type": "assign", "username": "operador1"}},
    "tag": {"desc": "Añade una etiqueta a la alarma", "example": {"type": "tag", "value": "verificar-video"}},
    "comment": {"desc": "Añade un comentario a la alarma", "example": {"type": "comment", "text": "Nota: {zone_name}"}},
    "email": {
        "desc": "Envía un correo. Destinos especiales: @contacts (contactos con aviso por email), @client",
        "example": {"type": "email", "to": "@contacts, guardia@empresa.com",
                    "subject": "[{priority}] {description} - {client_name}",
                    "body": "Abonado {account}\nCliente {client_name}\nZona {zone} {zone_name}\nHora {received_at}"},
    },
    "webhook": {
        "desc": "Petición HTTP. Sin 'body' se envía el contexto completo en JSON",
        "example": {"type": "webhook", "url": "https://example.com/hook", "method": "POST",
                    "headers": {"Authorization": "Bearer XXX"}},
    },
    "telegram": {"desc": "Mensaje a Telegram",
                 "example": {"type": "telegram", "bot_token": "123:ABC", "chat_id": "-100123",
                             "text": "🚨 {description} - {client_name} zona {zone}"}},
    "notify": {"desc": "Aviso emergente a los operadores conectados",
               "example": {"type": "notify", "message": "{description} en {client_name}", "level": "warning"}},
    "stop": {"desc": "Detiene la ejecución del workflow", "example": {"type": "stop"}},
}


class StopRun(Exception):
    pass


@dataclass
class Waiter:
    run: "Run"
    panel_id: int | None
    account: str
    step: dict[str, Any]
    origin_zone: str
    origin_partition: str
    future: asyncio.Future = field(default_factory=lambda: asyncio.get_running_loop().create_future())

    def matches(self, ctx: dict[str, Any]) -> bool:
        if self.panel_id is not None:
            if ctx.get("panel_id") != self.panel_id:
                return False
        elif ctx.get("account") != self.account:
            return False
        s = self.step
        if (codes := _as_list(s.get("codes"))) and not code_matches(codes, ctx):
            return False
        if (cats := _as_list(s.get("categories"))) and ctx.get("category") not in cats:
            return False
        if s.get("same_zone") and str(ctx.get("zone")) != self.origin_zone:
            return False
        if s.get("different_zone") and str(ctx.get("zone")) == self.origin_zone:
            return False
        if s.get("same_partition") and str(ctx.get("partition")) != self.origin_partition:
            return False
        if s.get("match") and not matches({"exclude_test": False, **s["match"]}, ctx):
            return False
        return True


class Run:
    def __init__(self, engine: "WorkflowEngine", run_id: int, wf: Workflow, ctx: dict[str, Any]):
        self.engine = engine
        self.id = run_id
        self.workflow_id = wf.id
        self.workflow_name = wf.name
        self.definition = wf.definition or {}
        self.ctx = dict(ctx)
        self.alarm_id: int | None = ctx.get("alarm_id")
        # La alarma "pertenece" al workflow si la creó el evento que lo disparó
        self.owns_alarm = bool(ctx.get("alarm_created"))
        self.held = bool(self.definition.get("hold_alarm")) and self.owns_alarm
        self.lines: list[str] = []
        self.status = "running"

    def log(self, msg: str) -> None:
        stamp = utcnow().replace(microsecond=0).isoformat()
        self.lines.append(f"{stamp} {msg}")
        log.debug("run %s: %s", self.id, msg)

    def save(self, status: str | None = None, finished: bool = False) -> None:
        if status:
            self.status = status
        with session_scope() as db:
            r = db.get(WorkflowRun, self.id)
            if r:
                r.status = self.status
                r.log = list(self.lines)
                r.alarm_id = self.alarm_id
                if finished:
                    r.finished_at = utcnow()


Handler = Callable[[Run, dict[str, Any]], Awaitable[None]]


class WorkflowEngine:
    def __init__(self) -> None:
        self.waiters: list[Waiter] = []
        self.tasks: set[asyncio.Task] = set()
        self.run_tasks: dict[int, asyncio.Task] = {}
        self.handlers: dict[str, Handler] = {
            "delay": self._delay, "wait_for": self._wait_for, "count": self._count, "condition": self._condition,
            "hold_alarm": self._hold, "release_alarm": self._release, "close_alarm": self._close,
            "create_alarm": self._create_alarm, "set_priority": self._set_priority, "assign": self._assign,
            "tag": self._tag, "comment": self._comment, "email": self._email, "webhook": self._webhook,
            "telegram": self._telegram, "notify": self._notify, "stop": self._stop,
        }

    # -------------------------------------------------------------- llamadas desde el procesador
    def check_waiters(self, ctx: dict[str, Any]) -> list[Waiter]:
        hit = [w for w in self.waiters if not w.future.done() and w.matches(ctx)]
        for w in hit:
            self.waiters.remove(w)
        return hit

    @staticmethod
    def match_workflows(db: Session, ctx: dict[str, Any]) -> list[Workflow]:
        out = []
        for wf in db.query(Workflow).filter_by(enabled=True).order_by(Workflow.order, Workflow.id):
            try:
                ok = matches((wf.definition or {}).get("trigger"), ctx)
            except Exception as exc:  # trigger mal definido
                log.warning("Trigger inválido en workflow %s: %s", wf.id, exc)
                ok = False
            if ok:
                out.append(wf)
                if wf.stop_processing:
                    break
        return out

    def start_runs(self, workflows: list[Workflow], ctx: dict[str, Any]) -> None:
        for wf in workflows:
            with session_scope() as db:
                r = WorkflowRun(workflow_id=wf.id, event_id=ctx.get("event_id"), alarm_id=ctx.get("alarm_id"),
                                status="running", log=[])
                db.add(r)
                db.flush()
                run_id = r.id
            run = Run(self, run_id, wf, ctx)
            task = asyncio.create_task(self._execute(run))
            self.tasks.add(task)
            self.run_tasks[run_id] = task
            task.add_done_callback(self.tasks.discard)
            task.add_done_callback(lambda _t, rid=run_id: self.run_tasks.pop(rid, None))

    def cancel_run(self, run_id: int) -> bool:
        task = self.run_tasks.get(run_id)
        if task and not task.done():
            task.cancel()
            return True
        return False

    # -------------------------------------------------------------- ejecución
    async def _execute(self, run: Run) -> None:
        run.log(f"Inicio workflow '{run.workflow_name}' por evento #{run.ctx.get('event_id')} "
                f"{run.ctx.get('code')} cuenta {run.ctx.get('account')}")
        status = "done"
        try:
            await self.run_steps(run, run.definition.get("steps", []))
        except StopRun:
            run.log("Workflow detenido (stop)")
        except asyncio.CancelledError:
            status = "cancelled"
            run.log("Ejecución cancelada")
        except Exception as exc:  # pragma: no cover - defensivo
            status = "error"
            run.log(f"Error: {exc}")
            log.exception("Error en workflow %s", run.workflow_id)
        finally:
            self._failsafe_release(run)
            run.log("Fin")
            run.save(status, finished=True)

    def _failsafe_release(self, run: Run) -> None:
        if not run.held or not run.alarm_id:
            return
        with session_scope() as db:
            a = db.get(Alarm, run.alarm_id)
            if a and a.status == "pending":
                alarm_ops.set_status(db, a, "new", comment="Liberada automáticamente al terminar el workflow "
                                                          f"'{run.workflow_name}' sin decisión", kind="workflow")
                db.flush()
                alarm_ops.publish(a, "released")
                run.log("Fail-safe: alarma liberada a la cola")

    async def run_steps(self, run: Run, steps: list[dict[str, Any]] | None) -> None:
        for step in steps or []:
            stype = step.get("type", "")
            handler = self.handlers.get(stype)
            if handler is None:
                run.log(f"Paso desconocido '{stype}' (ignorado)")
                continue
            try:
                await handler(run, step)
            except (StopRun, asyncio.CancelledError):
                raise
            except Exception as exc:
                run.log(f"Error en paso {stype}: {exc}")
                if step.get("stop_on_error"):
                    raise StopRun() from exc

    def _ctx(self, run: Run) -> dict[str, Any]:
        return {**run.ctx, "alarm_id": run.alarm_id or ""}

    # -------------------------------------------------------------- pasos
    async def _delay(self, run: Run, step: dict[str, Any]) -> None:
        secs = float(step.get("seconds", 0))
        run.log(f"Espera {secs:g} s")
        await asyncio.sleep(secs)

    async def _wait_for(self, run: Run, step: dict[str, Any]) -> None:
        timeout = float(step.get("timeout", 60))
        w = Waiter(run=run, panel_id=run.ctx.get("panel_id"), account=run.ctx.get("account", ""), step=step,
                   origin_zone=str(run.ctx.get("zone", "")), origin_partition=str(run.ctx.get("partition", "")))
        self.waiters.append(w)
        run.log(f"Esperando señal {step.get('codes') or step.get('categories') or '(cualquiera)'} durante {timeout:g} s")
        run.save("waiting")
        try:
            other = await asyncio.wait_for(asyncio.shield(w.future), timeout)
        except asyncio.TimeoutError:
            run.log("Tiempo agotado: no llegó la señal esperada")
            run.save("running")
            await self.run_steps(run, step.get("on_timeout"))
            return
        finally:
            if w in self.waiters:
                self.waiters.remove(w)
        run.log(f"Recibida señal esperada #{other.get('event_id')} {other.get('code')} zona {other.get('zone')}")
        run.ctx.update({f"matched_{k}": v for k, v in other.items()})
        run.save("running")
        await self.run_steps(run, step.get("on_match"))

    async def _count(self, run: Run, step: dict[str, Any]) -> None:
        window = int(step.get("window", 300))
        threshold = int(step.get("threshold", 2))
        since = utcnow() - timedelta(seconds=window)
        with session_scope() as db:
            q = db.query(Event).filter(Event.received_at >= since)
            if run.ctx.get("panel_id"):
                q = q.filter(Event.panel_id == run.ctx["panel_id"])
            else:
                q = q.filter(Event.account == run.ctx.get("account"))
            if cats := _as_list(step.get("categories")):
                q = q.filter(Event.category.in_(cats))
            if step.get("same_zone"):
                q = q.filter(Event.zone == (run.ctx.get("zone") or None))
            evs = q.all()
        codes = _as_list(step.get("codes"))
        n = sum(1 for e in evs if not codes or code_matches(codes, {"code": e.code,
                                                                    "cid_code": e.code[1:] if e.qualifier else ""}))
        run.ctx["count"] = n
        ok = n >= threshold
        run.log(f"Recuento: {n} señales en {window} s (umbral {threshold}) -> {'then' if ok else 'else'}")
        await self.run_steps(run, step.get("then") if ok else step.get("else"))

    async def _condition(self, run: Run, step: dict[str, Any]) -> None:
        ok = matches({"exclude_test": False, **(step.get("match") or {})}, self._ctx(run))
        run.log(f"Condición -> {'then' if ok else 'else'}")
        await self.run_steps(run, step.get("then") if ok else step.get("else"))

    def _with_alarm(self, run: Run, fn: Callable[[Session, Alarm], str | None]) -> None:
        if not run.alarm_id:
            run.log("Sin alarma asociada: paso omitido")
            return
        with session_scope() as db:
            a = db.get(Alarm, run.alarm_id)
            if a is None:
                run.log("La alarma ya no existe")
                return
            action = fn(db, a)
            db.flush()
            if action:
                alarm_ops.publish(a, action)

    async def _hold(self, run: Run, step: dict[str, Any]) -> None:
        def fn(db: Session, a: Alarm) -> str | None:
            if a.status == "new" and (run.owns_alarm or step.get("force")):
                alarm_ops.set_status(db, a, "pending", comment=f"Retenida por workflow '{run.workflow_name}'",
                                     kind="workflow")
                run.held = True
                run.log("Alarma retenida en buffer")
                return "held"
            run.log(f"No se retiene: estado {a.status}")
            return None
        self._with_alarm(run, fn)

    async def _release(self, run: Run, step: dict[str, Any]) -> None:
        def fn(db: Session, a: Alarm) -> str | None:
            if a.status == "pending":
                alarm_ops.set_status(db, a, "new", kind="workflow",
                                     comment=render(step.get("comment") or f"Liberada por workflow '{run.workflow_name}'",
                                                    self._ctx(run)))
                run.log("Alarma liberada a la cola del operador")
                return "released"
            return None
        self._with_alarm(run, fn)

    async def _close(self, run: Run, step: dict[str, Any]) -> None:
        def fn(db: Session, a: Alarm) -> str | None:
            if a.status == "closed":
                return None
            if not step.get("force"):
                if a.status == "in_progress":
                    run.log("La alarma está en gestión por un operador: no se cierra")
                    return None
                if not run.owns_alarm and a.status != "pending":
                    run.log("La alarma ya existía y está en la cola: no se cierra (use force)")
                    return None
            alarm_ops.set_status(db, a, "closed", resolution=step.get("resolution", "auto"), kind="workflow",
                                 comment=render(step.get("comment") or f"Cerrada por workflow '{run.workflow_name}'",
                                                self._ctx(run)))
            run.log("Alarma cerrada")
            return "closed"
        self._with_alarm(run, fn)

    async def _create_alarm(self, run: Run, step: dict[str, Any]) -> None:
        with session_scope() as db:
            ev = db.get(Event, run.ctx.get("event_id"))
            if ev is None:
                return
            if run.alarm_id:
                run.log("El evento ya tiene alarma")
                return
            status = "pending" if step.get("hold") else "new"
            a = alarm_ops.create_alarm(db, ev, status=status, priority=step.get("priority"),
                                       title=render(step["title"], self._ctx(run)) if step.get("title") else None)
            if status == "pending":
                run.held = True
            db.flush()
            run.alarm_id = a.id
            run.owns_alarm = True
            alarm_ops.publish(a, "created")
            run.log(f"Alarma #{a.id} creada")

    async def _set_priority(self, run: Run, step: dict[str, Any]) -> None:
        def fn(db: Session, a: Alarm) -> str:
            a.priority = int(step.get("value", 1))
            run.log(f"Prioridad -> {a.priority}")
            return "updated"
        self._with_alarm(run, fn)

    async def _assign(self, run: Run, step: dict[str, Any]) -> None:
        def fn(db: Session, a: Alarm) -> str | None:
            u = db.query(User).filter_by(username=step.get("username", "")).first()
            if not u:
                run.log(f"Usuario '{step.get('username')}' no existe")
                return None
            a.assigned_user_id = u.id
            run.log(f"Asignada a {u.username}")
            return "updated"
        self._with_alarm(run, fn)

    async def _tag(self, run: Run, step: dict[str, Any]) -> None:
        def fn(db: Session, a: Alarm) -> str:
            tag = render(step.get("value", ""), self._ctx(run))
            if tag and tag not in (a.tags or []):
                a.tags = [*(a.tags or []), tag]
            return "updated"
        self._with_alarm(run, fn)

    async def _comment(self, run: Run, step: dict[str, Any]) -> None:
        def fn(db: Session, a: Alarm) -> None:
            alarm_ops.add_comment(db, a, render(step.get("text", ""), self._ctx(run)), kind="workflow")
            return None
        self._with_alarm(run, fn)

    def _recipients(self, run: Run, spec: str) -> list[str]:
        out: list[str] = []
        for item in _as_list(render(spec, self._ctx(run))):
            if item in ("@contacts", "@client"):
                cid = run.ctx.get("client_id")
                if not cid:
                    continue
                with session_scope() as db:
                    c = db.get(Client, cid)
                    if c is None:
                        continue
                    if item == "@client" and c.email:
                        out.append(c.email)
                    if item == "@contacts":
                        out += [ct.email for ct in c.contacts if ct.notify_email and ct.email]
            else:
                out.append(item)
        return list(dict.fromkeys(out))

    async def _email(self, run: Run, step: dict[str, Any]) -> None:
        to = self._recipients(run, step.get("to", ""))
        if not to:
            run.log("Email sin destinatarios")
            return
        ctx = self._ctx(run)
        subject = render(step.get("subject", "[AlarmHub] {description} - {client_name}"), ctx)
        body = render(step.get("body", "{received_at}\n{account} {client_name}\n{code} {description}\n"
                                       "Zona {zone} {zone_name}\n{client_address}"), ctx)
        await notify.send_email(to, subject, body, html=bool(step.get("html")), alarm_id=run.alarm_id,
                                event_id=run.ctx.get("event_id"))
        run.log(f"Email enviado a {', '.join(to)}")

    async def _webhook(self, run: Run, step: dict[str, Any]) -> None:
        ctx = self._ctx(run)
        url = render(step["url"], ctx)
        headers = {k: render(v, ctx) for k, v in (step.get("headers") or {}).items()}
        if step.get("body"):
            body = render(step["body"] if isinstance(step["body"], str) else json.dumps(step["body"]), ctx)
            code = await notify.send_webhook(url, step.get("method", "POST"), headers, body=body,
                                             alarm_id=run.alarm_id, event_id=ctx.get("event_id"))
        else:
            payload = {k: v for k, v in ctx.items() if k != "raw"}
            code = await notify.send_webhook(url, step.get("method", "POST"), headers, json_body=payload,
                                             alarm_id=run.alarm_id, event_id=ctx.get("event_id"))
        run.log(f"Webhook {url} -> HTTP {code}")

    async def _telegram(self, run: Run, step: dict[str, Any]) -> None:
        ctx = self._ctx(run)
        await notify.send_telegram(step["bot_token"], str(step["chat_id"]),
                                   render(step.get("text", "{description} - {client_name}"), ctx),
                                   alarm_id=run.alarm_id, event_id=ctx.get("event_id"))
        run.log("Telegram enviado")

    async def _notify(self, run: Run, step: dict[str, Any]) -> None:
        bus.publish("toast", {"level": step.get("level", "info"),
                              "message": render(step.get("message", "{description}"), self._ctx(run)),
                              "alarm_id": run.alarm_id})
        run.log("Aviso enviado a operadores")

    async def _stop(self, run: Run, step: dict[str, Any]) -> None:
        raise StopRun()

    # -------------------------------------------------------------- utilidades
    async def shutdown(self) -> None:
        for t in list(self.tasks):
            t.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)


def validate_definition(defn: Any) -> list[str]:
    errors: list[str] = []
    if not isinstance(defn, dict):
        return ["La definición debe ser un objeto JSON"]
    if "trigger" in defn and not isinstance(defn["trigger"], dict):
        errors.append("'trigger' debe ser un objeto")

    def walk(steps: Any, path: str) -> None:
        if steps is None:
            return
        if not isinstance(steps, list):
            errors.append(f"{path} debe ser una lista de pasos")
            return
        for i, st in enumerate(steps):
            p = f"{path}[{i}]"
            if not isinstance(st, dict) or "type" not in st:
                errors.append(f"{p}: cada paso necesita 'type'")
                continue
            if st["type"] not in STEP_DOCS:
                errors.append(f"{p}: tipo desconocido '{st['type']}'")
            if st["type"] == "webhook" and not st.get("url"):
                errors.append(f"{p}: webhook necesita 'url'")
            if st["type"] == "telegram" and not (st.get("bot_token") and st.get("chat_id")):
                errors.append(f"{p}: telegram necesita 'bot_token' y 'chat_id'")
            for key in ("on_match", "on_timeout", "then", "else"):
                walk(st.get(key), f"{p}.{key}")

    walk(defn.get("steps", []), "steps")
    return errors


engine = WorkflowEngine()
