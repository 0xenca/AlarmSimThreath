"""Receptor Sur-Gard MLR2 (Contact ID sobre TCP) para integrar receptoras físicas
o software que reenvía en este formato."""
from __future__ import annotations

import asyncio
import logging

from ..engine.processor import processor
from ..protocols import surgard

log = logging.getLogger("alarmhub.surgard")
STATUS: dict[str, object] = {"tcp": None}


async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername")
    remote = f"{peer[0]}:{peer[1]}" if peer else "?"
    buf = b""
    try:
        while True:
            chunk = await reader.read(4096)
            if not chunk:
                break
            buf += chunk
            while surgard.TERMINATOR in buf:
                raw, _, buf = buf.partition(surgard.TERMINATOR)
                msg = surgard.parse(raw, remote)
                if msg is None:
                    log.warning("Mensaje Sur-Gard no reconocido: %r", raw)
                    writer.write(surgard.NAK)
                else:
                    if not msg.is_link_test:
                        await processor.submit(msg)
                    writer.write(surgard.ACK)
                await writer.drain()
    except ConnectionError:
        pass
    finally:
        writer.close()


async def start(host: str, port: int) -> asyncio.base_events.Server | None:
    if not port:
        return None
    srv = await asyncio.start_server(_handle, host, port)
    STATUS["tcp"] = f"{host}:{port}"
    log.info("Receptor Sur-Gard MLR2 escuchando en %s:%s", host, port)
    return srv
