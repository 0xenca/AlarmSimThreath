"""Línea de comandos:  python -m alarmhub [run|init|create-admin|reset-password]"""
from __future__ import annotations

import argparse
import getpass
import logging
import sys


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="alarmhub")
    sub = parser.add_subparsers(dest="cmd")
    sub.add_parser("run", help="Arranca la web y los receptores (por defecto)")
    sub.add_parser("init", help="Crea/actualiza las tablas de la base de datos")
    ca = sub.add_parser("create-admin", help="Crea o actualiza un usuario administrador")
    ca.add_argument("username", nargs="?", default="admin")
    ca.add_argument("--password", help="Si se omite se pide por teclado")
    rp = sub.add_parser("reset-password", help="Cambia la contraseña de un usuario")
    rp.add_argument("username")
    rp.add_argument("--password")
    args = parser.parse_args(argv)

    from .config import config
    logging.basicConfig(level=config.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    if args.cmd in (None, "run"):
        import uvicorn
        uvicorn.run("alarmhub.main:app", host=config.web_host, port=config.web_port, log_level=config.log_level.lower(),
                    proxy_headers=True)
        return 0

    from .bootstrap import init_db
    from .db import session_scope
    from .models import Role, User
    from .security import hash_password

    if args.cmd == "init":
        init_db()
        print("Base de datos inicializada")
        return 0
    pwd = args.password or getpass.getpass("Contraseña: ")
    if len(pwd) < 8:
        print("La contraseña debe tener al menos 8 caracteres", file=sys.stderr)
        return 1
    init_db(create_admin_password=pwd if args.cmd == "create-admin" else None)
    with session_scope() as db:
        u = db.query(User).filter_by(username=args.username).first()
        if args.cmd == "reset-password" and not u:
            print("Usuario no encontrado", file=sys.stderr)
            return 1
        if not u:
            u = User(username=args.username, full_name=args.username)
            db.add(u)
        if args.cmd == "create-admin":
            u.role_id = db.query(Role).filter_by(name="Administrador").one().id
            u.active = True
        u.password_hash = hash_password(pwd)
        u.must_change_password = False
    print(f"Usuario '{args.username}' actualizado")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
