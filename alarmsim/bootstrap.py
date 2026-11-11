"""Creación de tablas y datos de ejemplo."""
from __future__ import annotations

import logging

from .config import config
from .core.security import hash_password
from .db import Base, engine, session_scope
from .models import Panel, PanelUser, Partition, Scenario, Target, User, Zone

log = logging.getLogger("alarmsim")


def init_db(admin_password: str | None = None) -> None:
    Base.metadata.create_all(engine)
    with session_scope() as db:
        if db.query(User).count() == 0:
            db.add(User(username="admin", password_hash=hash_password(admin_password or "admin"),
                        must_change_password=admin_password is None))
            if admin_password is None and config.auth_enabled:
                log.warning("Usuario 'admin' creado con contraseña 'admin' (se pedirá cambiarla)")
        if db.query(Target).count() == 0:
            port = config.test_receiver_port or 7000
            t = Target(name="Receptor de pruebas local", host="127.0.0.1", port=port, transport="tcp",
                       receiver="1", line="1", notes="Apunta al receptor de pruebas integrado")
            db.add(t)
            db.flush()
            p = Panel(name="Panel demo", account="1234", target_id=t.id, protocol="SIA-DCS",
                      xdata_defaults={"S": "Instalación demo", "X": "W003.7038", "Y": "N40.4168"})
            db.add(p)
            db.flush()
            db.add(Partition(panel_id=p.id, number=1, name="Planta baja"))
            db.add(Partition(panel_id=p.id, number=2, name="Planta alta"))
            for n, name, part, code, cid, room in [
                (1, "Puerta principal", "1", "BA", "134", "Recibidor"), (2, "Volumétrico salón", "1", "BA", "132", "Salón"),
                (3, "Ventana cocina", "1", "BA", "131", "Cocina"), (4, "Volumétrico dormitorio", "2", "BA", "132", "Dormitorio"),
                (5, "Detector de humo", "2", "FA", "111", "Pasillo"), (6, "Pulsador de pánico", "1", "PA", "120", "Recibidor"),
                (7, "Detector de inundación", "1", "WA", "154", "Cocina")]:
                db.add(Zone(panel_id=p.id, number=n, name=name, partition=part, alarm_code=code, cid_code=cid, room=room))
            db.add(PanelUser(panel_id=p.id, number=1, name="Titular"))
            db.add(PanelUser(panel_id=p.id, number=2, name="Empleado"))
            db.add(Scenario(name="Intrusión confirmada + desconexión", panel_id=p.id, repeat=1, steps=[
                {"type": "event", "spec": {"code": "BA", "zone": "1", "partition": "1"}},
                {"type": "wait", "seconds": 3},
                {"type": "event", "spec": {"code": "BA", "zone": "2", "partition": "1",
                                           "xdata": {"V": "https://video.example.com/clip/demo"}}},
                {"type": "wait", "seconds": 5},
                {"type": "event", "spec": {"code": "OP", "user": "1", "partition": "1"}},
            ]))
            db.add(Scenario(name="Fallo de red y restauración", panel_id=p.id, repeat=1, steps=[
                {"type": "event", "spec": {"code": "AT"}},
                {"type": "wait", "seconds": 10},
                {"type": "event", "spec": {"code": "AR"}},
            ]))
            db.add(Scenario(name="Supervisión (NULL cada 60 s, infinito)", panel_id=p.id, repeat=0, repeat_delay=60,
                            steps=[{"type": "null"}]))
