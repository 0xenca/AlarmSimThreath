"""Contexto de un evento: diccionario plano usado por triggers, condiciones y plantillas."""
from __future__ import annotations

import fnmatch
import re
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from ..models import Event, PanelUser, Zone
from ..protocols.codes import CATEGORIES


def to_local(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt.replace(tzinfo=timezone.utc).astimezone()


def fmt_dt(dt: datetime | None) -> str:
    loc = to_local(dt)
    return loc.strftime("%d/%m/%Y %H:%M:%S") if loc else ""


def event_context(db: Session, ev: Event) -> dict[str, Any]:
    panel, client = ev.panel, ev.client
    zone_name = user_name = ""
    if panel and ev.zone:
        z = db.query(Zone).filter_by(panel_id=panel.id, number=ev.zone).first()
        zone_name = z.name if z else ""
    if panel and ev.user:
        u = db.query(PanelUser).filter_by(panel_id=panel.id, number=ev.user).first()
        user_name = u.name if u else ""
    return {
        "event_id": ev.id,
        "received_at": fmt_dt(ev.received_at),
        "account": ev.account,
        "line_prefix": ev.line_prefix,
        "code": ev.code,
        "sia_code": ev.code if not ev.qualifier else "",
        "cid_code": ev.code[1:] if ev.qualifier else "",
        "qualifier": ev.qualifier or "",
        "description": ev.description,
        "category": ev.category,
        "category_name": CATEGORIES.get(ev.category, ev.category),
        "priority": ev.priority,
        "zone": ev.zone or "",
        "zone_name": zone_name,
        "partition": ev.partition or "",
        "user": ev.user or "",
        "user_name": user_name,
        "text": ev.text or "",
        "protocol": ev.protocol,
        "remote": ev.remote,
        "encrypted": ev.encrypted,
        "test_mode": ev.test_mode,
        "unknown_account": panel is None,
        "panel_id": panel.id if panel else None,
        "panel_name": panel.name if panel else "",
        "panel_location": panel.location if panel else "",
        "armed_state": panel.armed_state if panel else "",
        "client_id": client.id if client else None,
        "client_code": client.code if client else "",
        "client_name": client.name if client else "(sin cliente)",
        "client_group": client.group if client else "",
        "client_address": ", ".join(x for x in [client.address, client.postal_code, client.city] if x) if client else "",
        "client_phone": client.phone if client else "",
        "client_email": client.email if client else "",
        "alarm_id": ev.alarm_id,
        "raw": ev.raw,
    }


# ------------------------------------------------------------------ matching
def _as_list(v: Any) -> list[str]:
    if v is None or v == "":
        return []
    if isinstance(v, (list, tuple, set)):
        return [str(x).strip() for x in v if str(x).strip()]
    return [x.strip() for x in str(v).split(",") if x.strip()]


def code_matches(patterns: list[str], ctx: dict[str, Any]) -> bool:
    candidates = {str(ctx.get("code", "")).upper()}
    if ctx.get("cid_code"):
        candidates.add(str(ctx["cid_code"]))
    for p in patterns:
        p = p.upper()
        for c in candidates:
            if fnmatch.fnmatchcase(c, p):
                return True
    return False


def _in_schedule(schedule: dict[str, Any], now: datetime | None = None) -> bool:
    now = now or datetime.now()
    days = schedule.get("days")
    if days not in (None, "", []) and now.weekday() not in [int(d) for d in _as_list(days)]:
        return False
    start, end = schedule.get("from"), schedule.get("to")
    if not start or not end:
        return True
    cur = now.strftime("%H:%M")
    if start <= end:
        return start <= cur < end
    return cur >= start or cur < end  # franja que cruza medianoche


def matches(trigger: dict[str, Any] | None, ctx: dict[str, Any]) -> bool:
    """Evalúa un bloque de condiciones (trigger de workflow o paso 'condition').

    Todas las condiciones presentes deben cumplirse (AND); dentro de cada lista basta
    con que coincida un elemento (OR). Soporta comodines ``*`` y ``?``.
    """
    t = trigger or {}
    if t.get("exclude_test", True) and ctx.get("test_mode"):
        return False
    if (cats := _as_list(t.get("categories"))) and ctx.get("category") not in cats:
        return False
    if (codes := _as_list(t.get("codes"))) and not code_matches(codes, ctx):
        return False
    if (ex := _as_list(t.get("exclude_codes"))) and code_matches(ex, ctx):
        return False
    if (accs := _as_list(t.get("accounts"))) and not any(fnmatch.fnmatchcase(str(ctx.get("account", "")), a.upper()) for a in accs):
        return False
    if (cids := _as_list(t.get("client_ids"))) and str(ctx.get("client_id")) not in cids:
        return False
    if (groups := _as_list(t.get("client_groups"))) and ctx.get("client_group") not in groups:
        return False
    if (zones := _as_list(t.get("zones"))) and str(ctx.get("zone")) not in zones:
        return False
    if (parts := _as_list(t.get("partitions"))) and str(ctx.get("partition")) not in parts:
        return False
    if (protos := _as_list(t.get("protocols"))) and ctx.get("protocol") not in protos:
        return False
    if t.get("armed_state") and ctx.get("armed_state") != t["armed_state"]:
        return False
    if t.get("max_priority") not in (None, "") and int(ctx.get("priority", 5)) > int(t["max_priority"]):
        return False
    if t.get("unknown_account") is not None and bool(ctx.get("unknown_account")) != bool(t["unknown_account"]):
        return False
    if t.get("text_regex") and not re.search(t["text_regex"], f"{ctx.get('description','')} {ctx.get('text','')}", re.I):
        return False
    if t.get("schedule") and not _in_schedule(t["schedule"]):
        return False
    if t.get("not_schedule") and _in_schedule(t["not_schedule"]):
        return False
    for f in t.get("fields", []) or []:  # [{"field": "zone_name", "op": "regex", "value": "Puerta"}]
        if not _field_cond(f, ctx):
            return False
    return True


def _field_cond(f: dict[str, Any], ctx: dict[str, Any]) -> bool:
    val = ctx.get(f.get("field", ""))
    op = f.get("op", "eq")
    target = f.get("value")
    sval = "" if val is None else str(val)
    if op == "eq":
        return sval == str(target)
    if op == "ne":
        return sval != str(target)
    if op == "in":
        return sval in _as_list(target)
    if op == "not_in":
        return sval not in _as_list(target)
    if op == "regex":
        return re.search(str(target), sval, re.I) is not None
    if op == "empty":
        return sval == ""
    if op == "not_empty":
        return sval != ""
    try:
        a, b = float(sval), float(target)
    except (TypeError, ValueError):
        return False
    return {"gt": a > b, "lt": a < b, "ge": a >= b, "le": a <= b}.get(op, False)


class _SafeDict(dict):
    def __missing__(self, key: str) -> str:
        return "{" + key + "}"


def render(template: str, ctx: dict[str, Any]) -> str:
    try:
        return str(template).format_map(_SafeDict({k: ("" if v is None else v) for k, v in ctx.items()}))
    except (ValueError, IndexError):
        return str(template)
