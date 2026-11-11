"""Envío de tramas por TCP o UDP y análisis de la respuesta de la receptora."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field

from ..protocol import dc09


@dataclass
class SendResult:
    result: str                    # ACK / NAK / DUH / TIMEOUT / ERROR / RESPUESTA?
    response: str = ""
    latency_ms: float | None = None
    attempts: int = 1
    error: str = ""
    details: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.result == "ACK"


def interpret(reply: bytes, key: bytes | None, expected_seq: str) -> tuple[str, dict, str]:
    """Devuelve (resultado, detalles, error)."""
    frames, _ = dc09.split_stream(reply)
    if not frames:
        return "RESPUESTA?", {}, "Respuesta sin formato DC-09"
    try:
        p = dc09.parse(frames[0], key)
    except dc09.DC09Error as exc:
        return "RESPUESTA?", {}, str(exc)
    details = {"token": p.token, "sequence": p.sequence, "encrypted": p.encrypted, "crc_ok": p.crc_ok,
               "length_ok": p.length_ok, "timestamp": p.timestamp.isoformat() if p.timestamp else None,
               "decrypted": p.decrypted}
    err = ""
    if p.token == "ACK" and p.sequence != expected_seq:
        err = f"ACK con secuencia {p.sequence} distinta de la enviada {expected_seq}"
    if not p.crc_ok:
        err = (err + "; " if err else "") + "CRC de la respuesta incorrecto"
    return (p.token if p.token in ("ACK", "NAK", "DUH") else f"{p.token}?"), details, err


class _TCPPool:
    """Conexiones TCP persistentes opcionales (keep-alive) por destino."""

    def __init__(self) -> None:
        self.conns: dict[tuple[str, int], tuple[asyncio.StreamReader, asyncio.StreamWriter]] = {}
        self.locks: dict[tuple[str, int], asyncio.Lock] = {}

    def lock(self, key: tuple[str, int]) -> asyncio.Lock:
        return self.locks.setdefault(key, asyncio.Lock())

    async def get(self, host: str, port: int, timeout: float):
        conn = self.conns.get((host, port))
        if conn and not conn[1].is_closing():
            return conn
        conn = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
        self.conns[(host, port)] = conn
        return conn

    def drop(self, host: str, port: int) -> None:
        conn = self.conns.pop((host, port), None)
        if conn:
            conn[1].close()

    async def close_all(self) -> None:
        for host, port in list(self.conns):
            self.drop(host, port)


pool = _TCPPool()


async def _tcp_once(host: str, port: int, data: bytes, timeout: float, keep_alive: bool) -> bytes:
    if keep_alive:
        async with pool.lock((host, port)):
            try:
                reader, writer = await pool.get(host, port, timeout)
                writer.write(data)
                await writer.drain()
                reply = await asyncio.wait_for(reader.readuntil(b"\r"), timeout)
                return reply
            except Exception:
                pool.drop(host, port)
                raise
    reader, writer = await asyncio.wait_for(asyncio.open_connection(host, port), timeout)
    try:
        writer.write(data)
        await writer.drain()
        return await asyncio.wait_for(reader.readuntil(b"\r"), timeout)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


class _UDPClient(asyncio.DatagramProtocol):
    def __init__(self) -> None:
        self.future: asyncio.Future[bytes] = asyncio.get_running_loop().create_future()

    def datagram_received(self, data: bytes, addr) -> None:
        if not self.future.done():
            self.future.set_result(data)

    def error_received(self, exc: Exception) -> None:
        if not self.future.done():
            self.future.set_exception(exc)


async def _udp_once(host: str, port: int, data: bytes, timeout: float) -> bytes:
    loop = asyncio.get_running_loop()
    transport, proto = await loop.create_datagram_endpoint(_UDPClient, remote_addr=(host, port))
    try:
        transport.sendto(data)
        return await asyncio.wait_for(proto.future, timeout)
    finally:
        transport.close()


async def send(host: str, port: int, transport: str, data: bytes, key: bytes | None, sequence: str,
               timeout: float = 10.0, retries: int = 0, keep_alive: bool = False,
               wait_response: bool = True) -> SendResult:
    attempts = 0
    last = SendResult("ERROR")
    for attempt in range(max(0, retries) + 1):
        attempts = attempt + 1
        t0 = time.perf_counter()
        try:
            if transport == "udp":
                if not wait_response:
                    loop = asyncio.get_running_loop()
                    tr, _ = await loop.create_datagram_endpoint(asyncio.DatagramProtocol, remote_addr=(host, port))
                    tr.sendto(data)
                    tr.close()
                    return SendResult("SENT", attempts=attempts)
                reply = await _udp_once(host, port, data, timeout)
            else:
                reply = await _tcp_once(host, port, data, timeout, keep_alive)
        except asyncio.TimeoutError:
            last = SendResult("TIMEOUT", attempts=attempts, error=f"Sin respuesta en {timeout:g} s")
            continue
        except (OSError, asyncio.IncompleteReadError) as exc:
            last = SendResult("ERROR", attempts=attempts, error=str(exc) or exc.__class__.__name__)
            continue
        latency = (time.perf_counter() - t0) * 1000
        result, details, err = interpret(reply, key, sequence)
        last = SendResult(result, reply.decode(dc09.ENC, "replace").strip(), round(latency, 1), attempts, err, details)
        if result in ("ACK", "DUH"):
            return last
        # NAK -> el estándar indica reintentar
    return last
