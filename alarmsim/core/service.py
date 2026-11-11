"""Operaciones de alto nivel: enviar una alarma de un panel y registrarla."""
from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import func

from ..db import session_scope
from ..models import Panel, SentMessage, Target
from ..protocol import dc09
from . import settings
from .builder import prepare
from .bus import bus
from .sender import SendResult, send

# Un lock por panel garantiza números de secuencia ordenados
_locks: dict[int, asyncio.Lock] = {}


def _record(panel: Panel | None, target: Target | None, source: str, token: str, summary: str,
            msg: dc09.BuiltMessage | None, res: SendResult, frame_text: str = "") -> dict[str, Any]:
    with session_scope() as db:
        row = SentMessage(source=source, panel_id=panel.id if panel else None, account=panel.account if panel else "",
                          target=target.label if target else "", token=token, summary=summary,
                          sequence=msg.sequence if msg else "", encrypted=bool(msg and msg.encrypted),
                          plaintext=msg.plaintext if msg else "", frame=msg.printable if msg else frame_text,
                          result=res.result, response=res.response, latency_ms=res.latency_ms,
                          attempts=res.attempts, error=res.error)
        db.add(row)
        db.flush()
        data = {"id": row.id, "created_at": row.created_at.isoformat() + "Z", "source": source,
                "account": row.account, "summary": summary, "result": res.result, "latency_ms": res.latency_ms,
                "sequence": row.sequence, "encrypted": row.encrypted, "error": res.error,
                "frame": row.frame, "plaintext": row.plaintext, "response": res.response, "attempts": res.attempts,
                "details": res.details}
    bus.publish("sent", {"message": {k: v for k, v in data.items() if k not in ("plaintext", "details")}})
    return data


def preview(panel_id: int, spec: dict[str, Any]) -> dict[str, Any]:
    with session_scope() as db:
        panel = db.get(Panel, panel_id)
        if panel is None:
            raise ValueError("Panel no encontrado")
        _ = panel.zones, panel.users, panel.target
        prep = prepare(panel, panel.target, spec, panel.sequence % 9999 + 1)
    m = prep.message
    return {"summary": prep.summary, "frame": m.printable, "plaintext": m.plaintext, "encrypted": m.encrypted,
            "bytes": len(m.frame), "hex": m.frame.hex(" ").upper(), "sequence": m.sequence}


async def send_spec(panel_id: int, spec: dict[str, Any], source: str = "manual") -> dict[str, Any]:
    lock = _locks.setdefault(panel_id, asyncio.Lock())
    async with lock:
        with session_scope() as db:
            panel = db.get(Panel, panel_id)
            if panel is None:
                raise ValueError("Panel no encontrado")
            target = panel.target
            if target is None:
                raise ValueError(f"El panel {panel.account} no tiene receptora destino")
            _ = panel.zones, panel.users
            seq = (spec.get("options") or {}).get("sequence") or panel.sequence % 9999 + 1
            prep = prepare(panel, target, spec, int(seq))
            panel.sequence = int(seq)
        res = await send(target.host, target.port, target.transport, prep.message.frame, prep.key,
                         prep.message.sequence, target.timeout, target.retries, target.keep_alive)
        return _record(panel, target, source, prep.token, prep.summary, prep.message, res)


async def send_raw(target_id: int, body: str, key_hex: str = "", fix_crc: bool = True) -> dict[str, Any]:
    """Envía una trama escrita a mano. Con fix_crc se calculan CRC y longitud automáticamente."""
    with session_scope() as db:
        target = db.get(Target, target_id)
        if target is None:
            raise ValueError("Destino no encontrado")
    body = body.strip().replace("<LF>", "").replace("<CR>", "")
    data = dc09.frame(body) if fix_crc else (b"\n" + body.encode(dc09.ENC, "replace") + b"\r")
    key = dc09.parse_key(key_hex) if key_hex else None
    m = dc09.FRAME_RE.match(data.decode(dc09.ENC, "replace").strip())
    seq = m.group("seq") if m else "0000"
    res = await send(target.host, target.port, target.transport, data, key, seq, target.timeout, target.retries,
                     target.keep_alive)
    printable = data.decode(dc09.ENC, "replace").replace("\n", "<LF>").replace("\r", "<CR>")
    return _record(None, target, "manual-raw", "RAW", body[:120], None, res, printable)


def trim_history() -> None:
    limit = int(settings.get("history_limit") or 0)
    if limit <= 0:
        return
    with session_scope() as db:
        max_id = db.query(func.max(SentMessage.id)).scalar() or 0
        if max_id > limit:
            db.query(SentMessage).filter(SentMessage.id <= max_id - limit).delete(synchronize_session=False)
