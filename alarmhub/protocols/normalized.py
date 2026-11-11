"""Representación normalizada de un evento, común a todos los protocolos."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .codes import DEFAULT_PRIORITY, cid_info, sia_info


@dataclass
class NormalizedEvent:
    code: str                      # "BA", "E130", "R401", "SUPV"...
    description: str
    category: str
    priority: int
    sia_code: str | None = None
    cid_code: str | None = None
    qualifier: str | None = None   # E (nuevo) / R (restore) / P (estado)
    zone: str | None = None
    partition: str | None = None
    user: str | None = None
    text: str | None = None        # texto libre que acompaña al evento
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class IncomingMessage:
    """Mensaje recibido por un receptor, ya validado, antes de persistir."""
    protocol: str                  # SIA-DCS, ADM-CID, NULL, SURGARD, HTTP, INTERNAL
    account: str
    line_prefix: str = ""
    receiver: str = ""
    sequence: str = ""
    raw: str = ""
    remote: str = ""
    transport: str = ""            # tcp / udp / http / internal
    encrypted: bool = False
    is_link_test: bool = False
    panel_timestamp: str | None = None
    events: list[NormalizedEvent] = field(default_factory=list)
    extended: dict[str, str] = field(default_factory=dict)


def from_sia(code: str, zone: str | None = None, partition: str | None = None, **kw: Any) -> NormalizedEvent:
    code = code.upper()
    desc, cat = sia_info(code)
    return NormalizedEvent(code=code, sia_code=code, description=desc, category=cat,
                           priority=DEFAULT_PRIORITY.get(cat, 5), zone=zone, partition=partition, **kw)


def from_cid(qualifier: str, code: str, partition: str | None = None, zone: str | None = None, **kw: Any) -> NormalizedEvent:
    q = {"E": "1", "R": "3", "P": "6"}.get(qualifier.upper(), qualifier)
    desc, cat = cid_info(code, q)
    letter = {"1": "E", "3": "R", "6": "P"}.get(q, "E")
    # En eventos de apertura/cierre el campo "zona" es el número de usuario
    user = None
    if code.startswith("4") and zone is not None:
        user = zone
    return NormalizedEvent(code=f"{letter}{code}", cid_code=code, qualifier=letter, description=desc,
                           category=cat, priority=DEFAULT_PRIORITY.get(cat, 5), zone=zone,
                           partition=partition, user=user, **kw)


def from_any_code(code: str, **kw: Any) -> NormalizedEvent:
    """Acepta 'BA', 'E130', 'R130', '1130', '3401'... (usado por la API HTTP)."""
    code = code.strip().upper()
    if len(code) == 2 and code.isalpha():
        return from_sia(code, **kw)
    if len(code) == 4 and code[0] in "ER P136" and code[1:].isdigit():
        return from_cid(code[0], code[1:], **kw)
    if len(code) == 3 and code.isdigit():
        return from_cid("E", code, **kw)
    if code in ("SUPV", "SUPR"):
        return from_sia(code, **kw)
    return NormalizedEvent(code=code, description=kw.get("text") or f"Evento {code}", category="info",
                           priority=5, **kw)
