"""Receptor DC-09 de pruebas integrado (TCP + UDP).

Sirve para probar el generador sin una receptora real: decodifica cada trama,
la muestra en la web y responde ACK / NAK / DUH o no responde, según la
configuración. Las claves se toman de los paneles dados de alta y de la tabla
``receiver_keys`` de la configuración.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from ..db import session_scope
from ..models import Panel, ReceivedMessage, Target
from ..protocol import dc09
from ..protocol.codes import CID, SIA
from . import settings
from .bus import bus

log = logging.getLogger("alarmsim.receiver")
STATUS: dict[str, Any] = {"listening": None, "received": 0}


def _key_for(account: str | None) -> bytes | None:
    if not account:
        return None
    extra = settings.get("receiver_keys") or {}
    if account.upper() in extra:
        return dc09.parse_key(extra[account.upper()])
    with session_scope() as db:
        p = db.query(Panel).filter(Panel.account == account.upper()).first()
        if p and p.enc_key:
            return dc09.parse_key(p.enc_key, p.key_format)
        if p and p.target and p.target.enc_key:
            return dc09.parse_key(p.target.enc_key, p.target.key_format)
        t = db.query(Target).filter(Target.enc_key != "").first()
        if t:
            return dc09.parse_key(t.enc_key, t.key_format)
    return None


def explain(p: dc09.Parsed) -> list[str]:
    """Descripción legible de los eventos contenidos en la trama."""
    data = p.data.split("|", 1)[1] if "|" in p.data else p.data
    out: list[str] = []
    if p.token == "ADM-CID":
        parts = data.replace(" ", "")
        if len(parts) >= 9:
            q, code, part, zone = parts[0], parts[1:4], parts[4:6], parts[6:9]
            q_txt = {"1": "nuevo", "3": "restauración", "6": "estado"}.get(q, q)
            out.append(f"{code} {CID.get(code, ('?',))[0]} ({q_txt}) partición {part} zona/usuario {zone}")
    elif p.token == "SIA-DCS":
        body = data[1:] if data[:1] in "NO" else data
        body = body.split("^", 1)[0]
        for chunk in body.split("/"):
            if len(chunk) >= 2 and chunk[:2].isupper():
                code = chunk[:2]
                out.append(f"{code} {SIA.get(code, ('?',))[0]} {chunk[2:]}")
            elif chunk:
                out.append(f"modificador {chunk[:2]}={chunk[2:]}")
    return out


async def handle(raw: bytes, remote: str, transport: str) -> bytes | None:
    mode = settings.get("receiver_mode") or "ack"
    account = dc09.peek_account(raw)
    rec: dict[str, Any] = {"remote": remote, "transport": transport, "raw": raw.decode(dc09.ENC, "replace")}
    reply: bytes | None
    try:
        key = _key_for(account)
        p = dc09.parse(raw, key)
        if not p.crc_ok or not p.length_ok:
            raise dc09.DC09Error("CRC o longitud incorrectos")
        decoded = {"token": p.token, "encrypted": p.encrypted, "sequence": p.sequence, "receiver": p.receiver,
                   "line": p.line, "account": p.account, "data": p.data, "xdata": p.xdata,
                   "timestamp": p.timestamp.isoformat() if p.timestamp else None, "events": explain(p),
                   "clock_skew_s": round((p.timestamp - datetime.now(timezone.utc)).total_seconds())
                   if p.timestamp else None}
        rec["decoded"] = decoded
        if mode == "silent":
            reply = None
        elif mode in ("nak", "duh"):
            reply = dc09.build_response(mode.upper(), p, key)
        else:
            reply = dc09.build_response("ACK", p, key)
        rec["response"] = "—" if reply is None else mode.upper()
    except (dc09.DC09Error, ValueError) as exc:
        rec.update(error=str(exc), response="NAK", decoded={})
        reply = dc09.build_response("NAK", None)
    STATUS["received"] += 1
    with session_scope() as db:
        row = ReceivedMessage(**rec)
        db.add(row)
        db.flush()
        rec["id"] = row.id
        rec["created_at"] = row.created_at.isoformat() + "Z"
    bus.publish("received", {"message": rec})
    return reply


async def _tcp(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    remote = f"{peer[0]}:{peer[1]}" if peer else "?"
    buf = b""
    try:
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                break
            buf += chunk
            frames, buf = dc09.split_stream(buf)
            for f in frames:
                reply = await handle(f, remote, "tcp")
                if reply:
                    writer.write(reply)
                    await writer.drain()
    except ConnectionError:
        pass
    finally:
        writer.close()


class _UDP(asyncio.DatagramProtocol):
    def connection_made(self, transport) -> None:
        self.transport = transport

    def datagram_received(self, data: bytes, addr) -> None:
        asyncio.create_task(self._handle(data, addr))

    async def _handle(self, data: bytes, addr) -> None:
        frames, _ = dc09.split_stream(data)
        for f in frames or [data.strip(b"\r\n")]:
            reply = await handle(f, f"{addr[0]}:{addr[1]}", "udp")
            if reply:
                self.transport.sendto(reply, addr)


async def start(host: str, port: int) -> list[Any]:
    if not port:
        return []
    srv = await asyncio.start_server(_tcp, host, port)
    tr, _ = await asyncio.get_running_loop().create_datagram_endpoint(_UDP, local_addr=(host, port))
    STATUS["listening"] = f"{host}:{port} (TCP+UDP)"
    log.info("Receptor de pruebas escuchando en %s:%s TCP/UDP", host, port)
    return [srv, tr]
