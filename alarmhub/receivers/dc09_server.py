"""Receptor SIA DC-09 sobre TCP y UDP."""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from .. import settings_store
from ..db import session_scope
from ..engine.processor import find_panel, processor
from ..models import utcnow
from ..protocols import dc09

log = logging.getLogger("alarmhub.dc09")

STATUS: dict[str, Any] = {"tcp": None, "udp": None, "connections": 0, "last_message": None, "rejected": 0}


def _lookup_key(account: str | None, prefix: str) -> tuple[bytes | None, bool, bool]:
    """Devuelve (clave, panel_conocido, requiere_cifrado)."""
    with session_scope() as db:
        panel = find_panel(db, account or "", prefix)
        if panel and panel.enc_key:
            return dc09.parse_key(panel.enc_key, panel.key_format), True, panel.require_encryption
        default = settings_store.get("dc09_default_key")
        key = dc09.parse_key(default, settings_store.get("dc09_default_key_format")) if default else None
        return key, panel is not None, bool(panel and panel.require_encryption)


async def handle_frame(raw: bytes, remote: str, transport: str) -> bytes:
    """Procesa una trama y devuelve la respuesta (ACK / NAK / DUH) ya enmarcada."""
    STATUS["last_message"] = utcnow().isoformat()
    account, prefix, _ = dc09.peek_account(raw)
    try:
        key, known, require_enc = _lookup_key(account, prefix)
    except ValueError as exc:
        log.error("Clave AES inválida para %s: %s", account, exc)
        key, known, require_enc = None, True, False
    try:
        fr = dc09.parse_frame(raw, key)
        if not dc09.timestamp_ok(fr, int(settings_store.get("dc09_timestamp_tolerance") or 0)):
            raise dc09.DC09Error("Marca de tiempo fuera de tolerancia", frame=fr)
        if require_enc and not fr.encrypted:
            raise dc09.DC09Error("El abonado exige cifrado y el mensaje llegó en claro", frame=fr)
        if not known and settings_store.get("dc09_reject_unknown") and fr.token != "NULL":
            raise dc09.DC09Error(f"Abonado desconocido {fr.account}", frame=fr)
    except dc09.DC09Error as exc:
        STATUS["rejected"] += 1
        processor.stats["nak"] += 1
        log.warning("%s %s de %s: %s | %r", exc.response, transport, remote, exc, raw[:120])
        return dc09.build_duh(exc.frame) if exc.response == "DUH" else dc09.build_nak()

    msg = dc09.to_incoming(fr, remote, transport)
    await processor.submit(msg)
    return dc09.build_ack(fr, key if fr.encrypted else None)


class _TCPHandler:
    async def __call__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        remote = f"{peer[0]}:{peer[1]}" if peer else "?"
        STATUS["connections"] += 1
        buf = b""
        try:
            while True:
                chunk = await asyncio.wait_for(reader.read(4096), timeout=300)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > 65536:
                    buf = b""
                frames, buf = dc09.split_stream(buf)
                for raw in frames:
                    try:
                        resp = await handle_frame(raw, remote, "tcp")
                    except Exception:
                        log.exception("Error interno procesando trama")
                        resp = dc09.build_nak()
                    writer.write(resp)
                    await writer.drain()
        except (asyncio.TimeoutError, ConnectionError):
            pass
        finally:
            STATUS["connections"] -= 1
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


class _UDPProtocol(asyncio.DatagramProtocol):
    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transport = transport  # type: ignore[assignment]

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        asyncio.create_task(self._handle(data, addr))

    async def _handle(self, data: bytes, addr: tuple[str, int]) -> None:
        frames, _ = dc09.split_stream(data)
        for raw in frames or [data.strip(b"\r\n")]:
            try:
                resp = await handle_frame(raw, f"{addr[0]}:{addr[1]}", "udp")
            except Exception:
                log.exception("Error interno procesando datagrama")
                resp = dc09.build_nak()
            self.transport.sendto(resp, addr)  # type: ignore[attr-defined]


async def start(host: str, tcp_port: int, udp_port: int) -> list[Any]:
    servers: list[Any] = []
    if tcp_port:
        srv = await asyncio.start_server(_TCPHandler(), host, tcp_port)
        servers.append(srv)
        STATUS["tcp"] = f"{host}:{tcp_port}"
        log.info("Receptor DC-09 TCP escuchando en %s:%s", host, tcp_port)
    if udp_port:
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(_UDPProtocol, local_addr=(host, udp_port))
        servers.append(transport)
        STATUS["udp"] = f"{host}:{udp_port}"
        log.info("Receptor DC-09 UDP escuchando en %s:%s", host, udp_port)
    return servers
