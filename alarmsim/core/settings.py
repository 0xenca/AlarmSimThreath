"""Parámetros editables desde la web (tabla settings)."""
from __future__ import annotations

from typing import Any

from ..db import session_scope
from ..models import Setting

DEFAULTS: dict[str, Any] = {
    "receiver_mode": "ack",          # respuesta del receptor de pruebas: ack / nak / duh / silent
    "receiver_keys": {},             # abonado -> clave hex adicional para el receptor de pruebas
    "history_limit": 50000,          # nº máximo de mensajes en el histórico
}


def get(key: str) -> Any:
    with session_scope() as db:
        row = db.get(Setting, key)
        return row.value if row else DEFAULTS.get(key)


def put(key: str, value: Any) -> None:
    with session_scope() as db:
        row = db.get(Setting, key)
        if row:
            row.value = value
        else:
            db.add(Setting(key=key, value=value))
