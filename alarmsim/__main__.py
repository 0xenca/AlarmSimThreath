"""Línea de comandos.

  python -m alarmsim run                        arranca la web (y el receptor de pruebas si está configurado)
  python -m alarmsim send --host H --port P --account 1234 --code BA --zone 3 --partition 1 \\
         --text "Puerta" --url https://... --key 0011..  [--udp] [--cid E] [--count 5]
  python -m alarmsim set-password admin
"""
from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import sys


def _send(args: argparse.Namespace) -> int:
    from .core.sender import send
    from .protocol import dc09

    key = dc09.parse_key(args.key, "ascii" if args.key_ascii else "hex") if args.key else None
    xdata = {"I": args.text, "V": args.url, "X": args.lon, "Y": args.lat, "L": args.location, "R": args.room,
             "S": args.site, "M": args.mac}
    for item in args.x or []:
        letter, _, value = item.partition("=")
        xdata[letter.upper()] = value
    if args.null:
        token, data = "NULL", ""
    elif args.cid:
        token, data = "ADM-CID", dc09.adm_cid_data(args.cid, args.code, args.partition or "0", args.zone or args.user or "0")
    else:
        token = "SIA-DCS"
        data = dc09.sia_dcs_data([dc09.SIAEvent(args.code, args.zone or "", args.partition or "", args.user or "")],
                                 time_ti=args.ti or "", inline_text=args.inline_text or "")
    rc = 0

    async def run() -> None:
        nonlocal rc
        for i in range(args.count):
            m = dc09.build_message(token, args.account, data, xdata, dc09.BuildOptions(
                sequence=args.seq + i, receiver=args.receiver, line=args.line, key=key,
                timestamp=not args.no_timestamp, timestamp_offset=args.ts_offset))
            print(">>", m.plaintext)
            if args.dry_run:
                print("   ", m.printable)
                continue
            r = await send(args.host, args.port, "udp" if args.udp else "tcp", m.frame, key, m.sequence,
                           args.timeout, args.retries)
            print(f"<< {r.result}" + (f" {r.latency_ms} ms" if r.latency_ms is not None else "")
                  + (f"  {r.error}" if r.error else ""))
            if r.result != "ACK":
                rc = 2
            if i < args.count - 1:
                await asyncio.sleep(args.interval)

    asyncio.run(run())
    return rc


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="alarmsim", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd")
    sub.add_parser("run")
    sub.add_parser("init")
    sp = sub.add_parser("set-password")
    sp.add_argument("username", nargs="?", default="admin")
    sp.add_argument("--password")
    sd = sub.add_parser("send", help="Envía una alarma desde la consola")
    sd.add_argument("--host", default="127.0.0.1")
    sd.add_argument("--port", type=int, default=7000)
    sd.add_argument("--udp", action="store_true")
    sd.add_argument("--account", default="1234")
    sd.add_argument("--receiver", default="0", help="Número de receptor R ('' para omitir)")
    sd.add_argument("--line", default="0", help="Prefijo de línea L")
    sd.add_argument("--code", default="BA", help="SIA (BA) o Contact ID (130) con --cid")
    sd.add_argument("--cid", metavar="Q", help="Enviar ADM-CID con calificador E/R/P")
    sd.add_argument("--null", action="store_true", help="Prueba de enlace NULL")
    sd.add_argument("--zone")
    sd.add_argument("--partition")
    sd.add_argument("--user")
    sd.add_argument("--ti", help="Hora SIA 'ti' HH:MM")
    sd.add_argument("--inline-text", help="Texto SIA en línea ^...^")
    sd.add_argument("--text", help="Texto de alarma [I]")
    sd.add_argument("--url", help="URL de verificación [V]")
    sd.add_argument("--lat", help="Latitud [Y]")
    sd.add_argument("--lon", help="Longitud [X]")
    sd.add_argument("--location", help="Ubicación [L]")
    sd.add_argument("--room", help="Sala [R]")
    sd.add_argument("--site", help="Nombre del sitio [S]")
    sd.add_argument("--mac", help="MAC [M]")
    sd.add_argument("-x", action="append", metavar="L=valor", help="Otro campo extendido, p. ej. -x H=10:00:00,11-11-2026")
    sd.add_argument("--key", help="Clave AES (hex; ASCII con --key-ascii)")
    sd.add_argument("--key-ascii", action="store_true")
    sd.add_argument("--no-timestamp", action="store_true")
    sd.add_argument("--ts-offset", type=int, default=0, help="Desfase de la marca de tiempo en segundos")
    sd.add_argument("--seq", type=int, default=1)
    sd.add_argument("--count", type=int, default=1)
    sd.add_argument("--interval", type=float, default=1.0)
    sd.add_argument("--timeout", type=float, default=10.0)
    sd.add_argument("--retries", type=int, default=0)
    sd.add_argument("--dry-run", action="store_true", help="Solo muestra la trama")
    args = ap.parse_args(argv)

    if args.cmd == "send":
        return _send(args)

    from .config import config
    logging.basicConfig(level=config.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.cmd in (None, "run"):
        import uvicorn
        uvicorn.run("alarmsim.main:app", host=config.web_host, port=config.web_port,
                    log_level=config.log_level.lower(), proxy_headers=True)
        return 0

    from .bootstrap import init_db
    if args.cmd == "init":
        init_db()
        return 0
    pwd = args.password or getpass.getpass("Contraseña: ")
    if len(pwd) < 8:
        print("Mínimo 8 caracteres", file=sys.stderr)
        return 1
    init_db(admin_password=pwd if args.username == "admin" else None)
    from .core.security import hash_password
    from .db import session_scope
    from .models import User
    with session_scope() as db:
        u = db.query(User).filter_by(username=args.username).first() or User(username=args.username)
        u.password_hash = hash_password(pwd)
        u.must_change_password = False
        db.add(u)
    print(f"Contraseña de '{args.username}' actualizada")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
