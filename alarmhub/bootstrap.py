"""Inicialización de la base de datos y datos por defecto."""
from __future__ import annotations

import logging

from . import settings_store
from .db import Base, engine, session_scope
from .engine import alarms as alarm_ops
from .models import PERMISSIONS, Alarm, Role, User, Workflow, WorkflowRun, utcnow
from .security import hash_password

log = logging.getLogger("alarmhub")

DEFAULT_ROLES = {
    "Administrador": ("Acceso total", ["*"]),
    "Supervisor": ("Gestión operativa, clientes y workflows",
                   [p for p in PERMISSIONS if p not in ("users.manage", "settings.manage")]),
    "Operador": ("Atención de alarmas y consulta",
                 ["alarms.view", "alarms.handle", "events.view", "clients.view"]),
    "Técnico": ("Alta y mantenimiento de paneles", ["events.view", "clients.view", "clients.edit", "clients.secrets"]),
    "Solo lectura": ("Consulta", ["alarms.view", "events.view", "clients.view"]),
    "Integración API": ("Cuenta de servicio para envío de eventos por HTTP", ["api.ingest"]),
}

EXAMPLE_WORKFLOWS = [
    {
        "name": "Verificación por doble zona (buffer)",
        "description": "Una alarma de intrusión queda retenida en buffer. Si en 2 minutos salta otra zona "
                       "distinta se libera como confirmada con prioridad 1; si no llega, se cierra sola.",
        "order": 10,
        "definition": {
            "trigger": {"categories": ["alarm"], "client_groups": []},
            "hold_alarm": True,
            "steps": [
                {"type": "wait_for", "categories": ["alarm"], "different_zone": True, "timeout": 120,
                 "consume": True,
                 "on_match": [
                     {"type": "set_priority", "value": 1},
                     {"type": "tag", "value": "confirmada"},
                     {"type": "release_alarm", "comment": "Confirmada: zona {zone} + zona {matched_zone}"},
                 ],
                 "on_timeout": [
                     {"type": "close_alarm", "resolution": "auto",
                      "comment": "Señal única no confirmada en 120 s (zona {zone})"},
                 ]},
            ],
        },
    },
    {
        "name": "Corte de red: avisar solo si no se restaura en 15 min",
        "description": "Retiene la avería de 230V; si llega la restauración en 15 minutos se cierra, "
                       "si no se libera al operador y se envía email al cliente.",
        "order": 20,
        "definition": {
            "trigger": {"codes": ["AT", "E301"]},
            "hold_alarm": True,
            "steps": [
                {"type": "wait_for", "codes": ["AR", "R301"], "timeout": 900, "consume": False,
                 "on_match": [{"type": "close_alarm", "resolution": "auto",
                               "comment": "Red restaurada antes de 15 min"}],
                 "on_timeout": [
                     {"type": "release_alarm", "comment": "Sin restauración de red tras 15 min"},
                     {"type": "email", "to": "@contacts",
                      "subject": "Fallo de alimentación en {client_name}",
                      "body": "Su sistema de alarma ({account}) lleva 15 minutos sin red eléctrica.\n"
                              "Hora del fallo: {received_at}"},
                 ]},
            ],
        },
    },
    {
        "name": "Incendio / pánico: notificación inmediata",
        "description": "Envía email y aviso emergente para cualquier alarma de prioridad 1.",
        "order": 5,
        "definition": {
            "trigger": {"categories": ["fire", "panic", "medical"]},
            "steps": [
                {"type": "notify", "level": "danger", "message": "⚠ {category_name}: {client_name} ({account})"},
                {"type": "email", "to": "guardia@example.com",
                 "subject": "[P{priority}] {description} - {client_name}",
                 "body": "{received_at}\nCliente: {client_name} ({client_code})\nDirección: {client_address}\n"
                         "Abonado: {account} Zona: {zone} {zone_name}\nEvento: {code} {description}"},
            ],
        },
    },
    {
        "name": "Ráfaga de alarmas en la misma zona",
        "description": "Si una misma zona dispara 3 o más veces en 10 minutos, se etiqueta como posible avería.",
        "order": 30,
        "definition": {
            "trigger": {"categories": ["alarm"]},
            "steps": [
                {"type": "count", "categories": ["alarm"], "same_zone": True, "window": 600, "threshold": 3,
                 "then": [{"type": "tag", "value": "posible-averia-zona-{zone}"},
                          {"type": "comment", "text": "Zona {zone} ha disparado {count} veces en 10 min"}]},
            ],
        },
    },
    {
        "name": "Apertura fuera de horario",
        "description": "Desconexión entre las 22:00 y las 06:00: se genera alarma para verificar con el cliente.",
        "order": 40,
        "definition": {
            "trigger": {"categories": ["open"], "schedule": {"from": "22:00", "to": "06:00"}},
            "steps": [
                {"type": "create_alarm", "priority": 3, "title": "Desconexión fuera de horario (usuario {user})"},
                {"type": "comment", "text": "Verificar con el cliente usando la palabra clave"},
            ],
        },
    },
]


def init_db(create_admin_password: str | None = None) -> None:
    Base.metadata.create_all(engine)
    with session_scope() as db:
        settings_store.load(db)
        for name, (desc, perms) in DEFAULT_ROLES.items():
            if not db.query(Role).filter_by(name=name).first():
                db.add(Role(name=name, description=desc, permissions=perms))
        db.flush()
        if db.query(User).count() == 0:
            admin_role = db.query(Role).filter_by(name="Administrador").one()
            pwd = create_admin_password or "admin"
            db.add(User(username="admin", full_name="Administrador", password_hash=hash_password(pwd),
                        role_id=admin_role.id, must_change_password=create_admin_password is None))
            log.warning("Usuario 'admin' creado%s", "" if create_admin_password else " con contraseña 'admin' "
                        "(se pedirá cambiarla en el primer acceso)")
        if db.query(Workflow).count() == 0:
            for wf in EXAMPLE_WORKFLOWS:
                db.add(Workflow(name=wf["name"], description=wf["description"], order=wf["order"],
                                enabled=False, definition=wf["definition"]))


def recover_after_restart() -> None:
    """Fail-safe tras un reinicio: las esperas en memoria se han perdido."""
    with session_scope() as db:
        n = 0
        for r in db.query(WorkflowRun).filter(WorkflowRun.status.in_(("running", "waiting"))):
            r.status = "interrupted"
            r.finished_at = utcnow()
            r.log = [*(r.log or []), f"{utcnow().isoformat()} Interrumpido por reinicio del servicio"]
            n += 1
        for a in db.query(Alarm).filter_by(status="pending"):
            alarm_ops.set_status(db, a, "new", comment="Liberada del buffer tras reinicio del servicio")
        if n:
            log.warning("%s ejecuciones de workflow interrumpidas por reinicio", n)
