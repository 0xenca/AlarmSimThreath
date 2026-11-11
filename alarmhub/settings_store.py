"""Parámetros de negocio editables desde la web (tabla ``settings``)."""
from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from .models import Setting

DEFAULTS: dict[str, Any] = {
    "site_name": "AlarmHub CRA",
    "alarm_categories": ["fire", "panic", "medical", "alarm", "tamper", "supervision", "trouble"],
    "group_window_minutes": 30,          # agrupar señales del mismo panel en la misma alarma abierta
    "dc09_timestamp_tolerance": 60,      # segundos (0 = no comprobar)
    "dc09_reject_unknown": False,        # NAK a abonados no dados de alta
    "dc09_default_key": "",              # clave AES por defecto (hex) para abonados sin clave
    "dc09_default_key_format": "hex",
    "smtp_host": "",
    "smtp_port": 587,
    "smtp_security": "starttls",         # starttls / ssl / none
    "smtp_user": "",
    "smtp_password": "",
    "smtp_from": "alarmhub@localhost",
    "events_retention_days": 0,          # 0 = conservar siempre
    "sound_alerts": True,
}

_cache: dict[str, Any] = {}


def load(db: Session) -> None:
    _cache.clear()
    for s in db.query(Setting).all():
        _cache[s.key] = s.value


def get(key: str) -> Any:
    return _cache.get(key, DEFAULTS.get(key))


def all_settings() -> dict[str, Any]:
    return {k: get(k) for k in DEFAULTS}


def set_many(db: Session, values: dict[str, Any]) -> None:
    for key, value in values.items():
        row = db.get(Setting, key)
        if row is None:
            db.add(Setting(key=key, value=value))
        else:
            row.value = value
        _cache[key] = value
    db.commit()
