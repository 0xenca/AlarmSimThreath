#!/usr/bin/env python3
"""Simulador de paneles SIA DC-09 (TCP/UDP, cifrado AES opcional).

Ejemplos:
  # Alarma de robo zona 3 en el abonado 1234
  python tools/dc09_sim.py --account 1234 --sia "Nri1/BA3"

  # Contact ID cifrado (AES-128 en hex) por UDP
  python tools/dc09_sim.py --account 5678 --cid "1130 01 003" --key 000102030405060708090A0B0C0D0E0F --udp

  # Prueba de enlace (NULL) cada 60 s
  python tools/dc09_sim.py --account 1234 --null --repeat 0 --interval 60

  # Escenario de verificación por doble zona
  python tools/dc09_sim.py --account 1234 --scenario double-knock

  # Carga: 50 paneles, 20 eventos cada uno
  python tools/dc09_sim.py --load 50 --repeat 20 --account-base 9000
"""
from __future__ import annotations

import argparse
import asyncio
import os
import random
import socket
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from alarmhub.protocols import dc09  # noqa: E402

SCENARIOS = {
    "double-knock": [("SIA-DCS", "Nri1/BA3", 0), ("SIA-DCS", "Nri1/BA5", 5)],
    "single-knock": [("SIA-DCS", "Nri1/BA3", 0)],
    "fire": [("SIA-DCS", "Nri1/FA12", 0), ("SIA-DCS", "Nri1/FR12", 20)],
    "ac-fail-restore": [("ADM-CID", "1301 00 000", 0), ("ADM-CID", "3301 00 000", 30)],
    "open-close": [("SIA-DCS", "Nri1/OP2", 0), ("SIA-DCS", "Nri1/CL2", 10)],
    "panic": [("ADM-CID", "1120 01 000", 0)],
}


def build(token: str, account: str, data: str, seq: int, key: bytes | None, line: str, receiver: str) -> bytes:
    payload = f"#{account}|{data}" if token != "NULL" else ""
    return dc09.build_message(token, account, payload, sequence=seq, receiver=receiver, line=line, key=key)


def describe_reply(reply: bytes, key: bytes | None) -> str:
    text = reply.decode("latin-1").strip()
    if '"ACK"' in text or '"*ACK"' in text:
        return "ACK ✔"
    if '"NAK"' in text:
        return "NAK ✘"
    if '"DUH"' in text:
        return "DUH ?"
    return f"respuesta desconocida: {text!r}"


def send_tcp(host: str, port: int, frames: list[bytes], key: bytes | None, timeout: float = 10) -> list[str]:
    out = []
    with socket.create_connection((host, port), timeout=timeout) as s:
        for f in frames:
            s.sendall(f)
            buf = b""
            while b"\r" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
            out.append(describe_reply(buf, key))
    return out


def send_udp(host: str, port: int, frame: bytes, key: bytes | None, timeout: float = 10) -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.settimeout(timeout)
        s.sendto(frame, (host, port))
        try:
            data, _ = s.recvfrom(4096)
        except socket.timeout:
            return "sin respuesta (timeout)"
        return describe_reply(data, key)


async def load_test(args: argparse.Namespace, key: bytes | None) -> None:
    codes = ["Nri1/BA{z}", "Nri1/BR{z}", "Nri1/OP1", "Nri1/CL1", "Nri1/RP", "Nri1/TA{z}", "Nri1/AT", "Nri1/AR"]
    ok = fail = 0
    start = time.time()

    async def panel(i: int) -> None:
        nonlocal ok, fail
        acct = str(args.account_base + i)
        reader, writer = await asyncio.open_connection(args.host, args.port)
        for n in range(args.repeat or 1):
            data = random.choice(codes).format(z=random.randint(1, 16))
            writer.write(build("SIA-DCS", acct, data, n + 1, key, args.line, args.receiver))
            await writer.drain()
            reply = await reader.readuntil(b"\r")
            if b"ACK" in reply:
                ok += 1
            else:
                fail += 1
        writer.close()

    await asyncio.gather(*(panel(i) for i in range(args.load)))
    dt = time.time() - start
    print(f"{ok} ACK, {fail} fallos en {dt:.1f} s ({(ok + fail) / dt:.0f} msg/s)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=7000)
    ap.add_argument("--udp", action="store_true")
    ap.add_argument("--account", default="1234")
    ap.add_argument("--line", default="0", help="Prefijo de línea (L)")
    ap.add_argument("--receiver", default="0", help="Número de receptor (R)")
    ap.add_argument("--key", help="Clave AES (hex 32/48/64 caracteres, o ASCII con --key-ascii)")
    ap.add_argument("--key-ascii", action="store_true")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--sia", help="Bloque SIA-DCS, p. ej. 'Nri1/BA3'")
    g.add_argument("--cid", help="Bloque ADM-CID, p. ej. '1130 01 003'")
    g.add_argument("--null", action="store_true", help="Prueba de enlace NULL")
    g.add_argument("--scenario", choices=sorted(SCENARIOS))
    g.add_argument("--load", type=int, help="Prueba de carga con N paneles concurrentes (TCP)")
    ap.add_argument("--account-base", type=int, default=9000)
    ap.add_argument("--repeat", type=int, default=1, help="Número de envíos (0 = infinito)")
    ap.add_argument("--interval", type=float, default=1.0)
    args = ap.parse_args()

    key = dc09.parse_key(args.key, "ascii" if args.key_ascii else "hex") if args.key else None

    if args.load:
        asyncio.run(load_test(args, key))
        return 0

    if args.scenario:
        seq = 1
        last = 0
        for token, data, at in SCENARIOS[args.scenario]:
            time.sleep(max(0, at - last))
            last = at
            frame = build(token, args.account, data, seq, key, args.line, args.receiver)
            reply = send_udp(args.host, args.port, frame, key) if args.udp else \
                send_tcp(args.host, args.port, [frame], key)[0]
            print(f"[{time.strftime('%H:%M:%S')}] {token} {data} -> {reply}")
            seq += 1
        return 0

    token, data = ("NULL", "") if args.null else (("ADM-CID", args.cid) if args.cid else ("SIA-DCS", args.sia or "Nri1/BA1"))
    n = 0
    while args.repeat == 0 or n < args.repeat:
        n += 1
        frame = build(token, args.account, data, n, key, args.line, args.receiver)
        reply = send_udp(args.host, args.port, frame, key) if args.udp else send_tcp(args.host, args.port, [frame], key)[0]
        print(f"[{time.strftime('%H:%M:%S')}] #{n} {token} {data} {'🔒' if key else ''} -> {reply}")
        if args.repeat == 0 or n < args.repeat:
            time.sleep(args.interval)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
