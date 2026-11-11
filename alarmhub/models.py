from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (JSON, Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ------------------------------------------------------------------ usuarios
PERMISSIONS: dict[str, str] = {
    "alarms.view": "Ver cola de alarmas",
    "alarms.handle": "Atender / cerrar alarmas",
    "events.view": "Ver histórico de señales",
    "events.export": "Exportar señales (CSV)",
    "clients.view": "Ver clientes y paneles",
    "clients.edit": "Crear / editar clientes, contactos y paneles",
    "clients.secrets": "Ver claves de cifrado y palabras clave",
    "workflows.view": "Ver workflows",
    "workflows.edit": "Crear / editar workflows",
    "users.manage": "Gestionar usuarios y perfiles",
    "settings.manage": "Configuración del sistema",
    "audit.view": "Ver registro de auditoría",
    "api.ingest": "Enviar eventos por API HTTP",
}


class Role(Base):
    __tablename__ = "roles"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    description: Mapped[str] = mapped_column(String(255), default="")
    permissions: Mapped[list[str]] = mapped_column(JSON, default=list)
    users: Mapped[list["User"]] = relationship(back_populates="role")


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    full_name: Mapped[str] = mapped_column(String(128), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    phone: Mapped[str] = mapped_column(String(64), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role_id: Mapped[int | None] = mapped_column(ForeignKey("roles.id"))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    api_key: Mapped[str | None] = mapped_column(String(128), unique=True)
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    # Restricción opcional: lista de grupos de clientes visibles (vacío = todos)
    client_groups: Mapped[list[str]] = mapped_column(JSON, default=list)
    last_login: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    role: Mapped[Role | None] = relationship(back_populates="users")

    def has(self, perm: str) -> bool:
        return bool(self.role and (perm in self.role.permissions or "*" in self.role.permissions))


# ------------------------------------------------------------------ clientes
class Client(Base):
    __tablename__ = "clients"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    group: Mapped[str] = mapped_column(String(64), default="", index=True)
    tax_id: Mapped[str] = mapped_column(String(32), default="")
    client_type: Mapped[str] = mapped_column(String(32), default="residencial")
    address: Mapped[str] = mapped_column(String(255), default="")
    city: Mapped[str] = mapped_column(String(128), default="")
    postal_code: Mapped[str] = mapped_column(String(16), default="")
    province: Mapped[str] = mapped_column(String(128), default="")
    country: Mapped[str] = mapped_column(String(64), default="España")
    phone: Mapped[str] = mapped_column(String(64), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    keyword: Mapped[str] = mapped_column(String(64), default="")       # palabra clave de verificación
    duress_keyword: Mapped[str] = mapped_column(String(64), default="")  # palabra de coacción
    instructions: Mapped[str] = mapped_column(Text, default="")        # instrucciones al operador
    notes: Mapped[str] = mapped_column(Text, default="")
    contract_number: Mapped[str] = mapped_column(String(64), default="")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    contacts: Mapped[list["Contact"]] = relationship(back_populates="client", cascade="all, delete-orphan",
                                                     order_by="Contact.priority")
    panels: Mapped[list["Panel"]] = relationship(back_populates="client")


class Contact(Base):
    __tablename__ = "contacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(128))
    relation: Mapped[str] = mapped_column(String(64), default="")
    phone: Mapped[str] = mapped_column(String(64), default="")
    phone2: Mapped[str] = mapped_column(String(64), default="")
    email: Mapped[str] = mapped_column(String(255), default="")
    priority: Mapped[int] = mapped_column(Integer, default=1)
    has_keys: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_email: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    client: Mapped[Client] = relationship(back_populates="contacts")


class Panel(Base):
    __tablename__ = "panels"
    __table_args__ = (UniqueConstraint("account", "line_prefix", name="uq_panel_account_prefix"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"), index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    account: Mapped[str] = mapped_column(String(16), index=True)
    line_prefix: Mapped[str] = mapped_column(String(8), default="")   # vacío = cualquier prefijo
    protocol: Mapped[str] = mapped_column(String(32), default="SIA-DCS")
    manufacturer: Mapped[str] = mapped_column(String(64), default="")
    model: Mapped[str] = mapped_column(String(64), default="")
    serial: Mapped[str] = mapped_column(String(64), default="")
    installer: Mapped[str] = mapped_column(String(128), default="")
    location: Mapped[str] = mapped_column(String(255), default="")
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    enc_key: Mapped[str] = mapped_column(String(80), default="")
    key_format: Mapped[str] = mapped_column(String(8), default="hex")
    require_encryption: Mapped[bool] = mapped_column(Boolean, default=False)
    supervision_minutes: Mapped[int] = mapped_column(Integer, default=0)
    supervision_failed: Mapped[bool] = mapped_column(Boolean, default=False)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime)
    last_event_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_remote: Mapped[str] = mapped_column(String(64), default="")
    armed_state: Mapped[str] = mapped_column(String(16), default="desconocido")
    test_mode_until: Mapped[datetime | None] = mapped_column(DateTime)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)

    client: Mapped[Client | None] = relationship(back_populates="panels")
    zones: Mapped[list["Zone"]] = relationship(back_populates="panel", cascade="all, delete-orphan",
                                               order_by="Zone.number")
    users: Mapped[list["PanelUser"]] = relationship(back_populates="panel", cascade="all, delete-orphan",
                                                    order_by="PanelUser.number")

    @property
    def in_test(self) -> bool:
        return bool(self.test_mode_until and self.test_mode_until > utcnow())

    @property
    def label(self) -> str:
        return f"{self.account}" + (f" ({self.name})" if self.name else "")


class Zone(Base):
    __tablename__ = "zones"
    __table_args__ = (UniqueConstraint("panel_id", "number", name="uq_zone_panel_number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    panel_id: Mapped[int] = mapped_column(ForeignKey("panels.id", ondelete="CASCADE"))
    number: Mapped[str] = mapped_column(String(8))
    name: Mapped[str] = mapped_column(String(128), default="")
    zone_type: Mapped[str] = mapped_column(String(32), default="")
    partition: Mapped[str] = mapped_column(String(8), default="")
    notes: Mapped[str] = mapped_column(String(255), default="")
    panel: Mapped[Panel] = relationship(back_populates="zones")


class PanelUser(Base):
    __tablename__ = "panel_users"
    __table_args__ = (UniqueConstraint("panel_id", "number", name="uq_paneluser_panel_number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    panel_id: Mapped[int] = mapped_column(ForeignKey("panels.id", ondelete="CASCADE"))
    number: Mapped[str] = mapped_column(String(8))
    name: Mapped[str] = mapped_column(String(128), default="")
    panel: Mapped[Panel] = relationship(back_populates="users")


# ------------------------------------------------------------------ señales
class Event(Base):
    __tablename__ = "events"
    __table_args__ = (Index("ix_events_panel_time", "panel_id", "received_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    panel_id: Mapped[int | None] = mapped_column(ForeignKey("panels.id", ondelete="SET NULL"))
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"), index=True)
    alarm_id: Mapped[int | None] = mapped_column(ForeignKey("alarms.id", ondelete="SET NULL"), index=True)
    account: Mapped[str] = mapped_column(String(16), index=True)
    line_prefix: Mapped[str] = mapped_column(String(8), default="")
    receiver: Mapped[str] = mapped_column(String(8), default="")
    protocol: Mapped[str] = mapped_column(String(16))
    transport: Mapped[str] = mapped_column(String(16), default="")
    remote: Mapped[str] = mapped_column(String(64), default="")
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    sequence: Mapped[str] = mapped_column(String(8), default="")
    code: Mapped[str] = mapped_column(String(16), index=True)
    qualifier: Mapped[str | None] = mapped_column(String(2))
    description: Mapped[str] = mapped_column(String(255))
    category: Mapped[str] = mapped_column(String(16), index=True)
    priority: Mapped[int] = mapped_column(Integer, default=5)
    zone: Mapped[str | None] = mapped_column(String(8))
    partition: Mapped[str | None] = mapped_column(String(8))
    user: Mapped[str | None] = mapped_column(String(8))
    text: Mapped[str | None] = mapped_column(String(255))
    test_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    panel_timestamp: Mapped[str | None] = mapped_column(String(32))
    raw: Mapped[str] = mapped_column(Text, default="")
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    panel: Mapped[Panel | None] = relationship()
    client: Mapped[Client | None] = relationship()
    alarm: Mapped["Alarm | None"] = relationship(back_populates="events", foreign_keys=[alarm_id])


ALARM_STATUS = {
    "pending": "En buffer (retenida por workflow)",
    "new": "Nueva",
    "in_progress": "En gestión",
    "closed": "Cerrada",
}


class Alarm(Base):
    __tablename__ = "alarms"
    id: Mapped[int] = mapped_column(primary_key=True)
    panel_id: Mapped[int | None] = mapped_column(ForeignKey("panels.id", ondelete="SET NULL"), index=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id", ondelete="SET NULL"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="new", index=True)
    priority: Mapped[int] = mapped_column(Integer, default=3)
    category: Mapped[str] = mapped_column(String(16), default="alarm")
    title: Mapped[str] = mapped_column(String(255), default="")
    account: Mapped[str] = mapped_column(String(16), default="")
    opened_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    last_event_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)
    assigned_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    closed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    resolution: Mapped[str] = mapped_column(String(64), default="")
    restored: Mapped[bool] = mapped_column(Boolean, default=False)
    event_count: Mapped[int] = mapped_column(Integer, default=0)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)

    panel: Mapped[Panel | None] = relationship()
    client: Mapped[Client | None] = relationship()
    assigned_user: Mapped[User | None] = relationship(foreign_keys=[assigned_user_id])
    closed_by: Mapped[User | None] = relationship(foreign_keys=[closed_by_id])
    events: Mapped[list[Event]] = relationship(back_populates="alarm", foreign_keys=[Event.alarm_id],
                                               order_by="Event.received_at")
    comments: Mapped[list["Comment"]] = relationship(back_populates="alarm", order_by="Comment.created_at")


RESOLUTIONS = {
    "real": "Alarma real – aviso a policía/bomberos/sanitarios",
    "false_user": "Falsa alarma – error de usuario",
    "false_tech": "Falsa alarma – causa técnica",
    "verified_ok": "Verificada con cliente – sin novedad",
    "test": "Prueba / mantenimiento",
    "auto": "Cerrada automáticamente (workflow)",
    "duplicate": "Duplicada",
    "other": "Otra",
}


class Comment(Base):
    __tablename__ = "comments"
    id: Mapped[int] = mapped_column(primary_key=True)
    alarm_id: Mapped[int | None] = mapped_column(ForeignKey("alarms.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id", ondelete="CASCADE"), index=True)
    client_id: Mapped[int | None] = mapped_column(ForeignKey("clients.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(16), default="note")  # note, call, system, workflow
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    alarm: Mapped[Alarm | None] = relationship(back_populates="comments")
    user: Mapped[User | None] = relationship()


# ------------------------------------------------------------------ workflows
class Workflow(Base):
    __tablename__ = "workflows"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    order: Mapped[int] = mapped_column(Integer, default=100)
    stop_processing: Mapped[bool] = mapped_column(Boolean, default=False)
    definition: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    workflow_id: Mapped[int | None] = mapped_column(ForeignKey("workflows.id", ondelete="CASCADE"), index=True)
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"))
    alarm_id: Mapped[int | None] = mapped_column(ForeignKey("alarms.id", ondelete="SET NULL"))
    status: Mapped[str] = mapped_column(String(16), default="running", index=True)
    log: Mapped[list[str]] = mapped_column(JSON, default=list)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    workflow: Mapped[Workflow | None] = relationship()


# ------------------------------------------------------------------ sistema
class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    username: Mapped[str] = mapped_column(String(64), default="")
    action: Mapped[str] = mapped_column(String(64))
    object_type: Mapped[str] = mapped_column(String(32), default="")
    object_id: Mapped[str] = mapped_column(String(32), default="")
    details: Mapped[str] = mapped_column(Text, default="")
    ip: Mapped[str] = mapped_column(String(64), default="")


class NotificationLog(Base):
    __tablename__ = "notification_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    channel: Mapped[str] = mapped_column(String(16))       # email, webhook, telegram
    target: Mapped[str] = mapped_column(String(255))
    subject: Mapped[str] = mapped_column(String(255), default="")
    status: Mapped[str] = mapped_column(String(16))        # ok, error
    error: Mapped[str] = mapped_column(Text, default="")
    alarm_id: Mapped[int | None] = mapped_column(ForeignKey("alarms.id", ondelete="SET NULL"))
    event_id: Mapped[int | None] = mapped_column(ForeignKey("events.id", ondelete="SET NULL"))
