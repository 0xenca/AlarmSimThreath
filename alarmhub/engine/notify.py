"""Canales de notificación: email (SMTP), webhook HTTP y Telegram."""
from __future__ import annotations

import asyncio
import logging
import smtplib
import ssl
from email.message import EmailMessage
from typing import Any

import httpx

from .. import settings_store
from ..db import session_scope
from ..models import NotificationLog

log = logging.getLogger("alarmhub.notify")


def _record(channel: str, target: str, subject: str, ok: bool, error: str = "",
            alarm_id: int | None = None, event_id: int | None = None) -> None:
    try:
        with session_scope() as db:
            db.add(NotificationLog(channel=channel, target=target[:255], subject=subject[:255],
                                   status="ok" if ok else "error", error=error, alarm_id=alarm_id, event_id=event_id))
    except Exception:  # pragma: no cover
        log.exception("No se pudo registrar la notificación")


def _send_email_sync(to: list[str], subject: str, body: str, html: bool = False) -> None:
    host = settings_store.get("smtp_host")
    if not host:
        raise RuntimeError("SMTP no configurado (Configuración > Correo)")
    port = int(settings_store.get("smtp_port") or 587)
    security = settings_store.get("smtp_security")
    msg = EmailMessage()
    msg["From"] = settings_store.get("smtp_from")
    msg["To"] = ", ".join(to)
    msg["Subject"] = subject
    if html:
        msg.set_content("Este mensaje requiere un cliente con soporte HTML.")
        msg.add_alternative(body, subtype="html")
    else:
        msg.set_content(body)
    ctx = ssl.create_default_context()
    if security == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(host, port, timeout=20, context=ctx)
    else:
        server = smtplib.SMTP(host, port, timeout=20)
    try:
        if security == "starttls":
            server.starttls(context=ctx)
        if settings_store.get("smtp_user"):
            server.login(settings_store.get("smtp_user"), settings_store.get("smtp_password"))
        server.send_message(msg)
    finally:
        try:
            server.quit()
        except Exception:
            pass


async def send_email(to: list[str], subject: str, body: str, html: bool = False,
                     alarm_id: int | None = None, event_id: int | None = None) -> None:
    target = ", ".join(to)
    try:
        await asyncio.to_thread(_send_email_sync, to, subject, body, html)
        _record("email", target, subject, True, alarm_id=alarm_id, event_id=event_id)
    except Exception as exc:
        _record("email", target, subject, False, str(exc), alarm_id, event_id)
        raise


async def send_webhook(url: str, method: str = "POST", headers: dict[str, str] | None = None,
                       body: str | None = None, json_body: Any = None, timeout: float = 10.0,
                       alarm_id: int | None = None, event_id: int | None = None) -> int:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.request(method.upper(), url, headers=headers or {}, content=body, json=json_body)
        ok = resp.status_code < 400
        _record("webhook", url, f"{method} {resp.status_code}", ok, "" if ok else resp.text[:500], alarm_id, event_id)
        if not ok:
            raise RuntimeError(f"HTTP {resp.status_code}")
        return resp.status_code
    except httpx.HTTPError as exc:
        _record("webhook", url, method, False, str(exc), alarm_id, event_id)
        raise


async def send_telegram(bot_token: str, chat_id: str, text: str,
                        alarm_id: int | None = None, event_id: int | None = None) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={"chat_id": chat_id, "text": text})
        ok = resp.status_code < 400
        _record("telegram", chat_id, text[:80], ok, "" if ok else resp.text[:500], alarm_id, event_id)
        if not ok:
            raise RuntimeError(f"Telegram HTTP {resp.status_code}")
    except httpx.HTTPError as exc:
        _record("telegram", chat_id, text[:80], False, str(exc), alarm_id, event_id)
        raise
