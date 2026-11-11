from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    api_key: Mapped[str | None] = mapped_column(String(128), unique=True)


class Target(Base):
    """Receptora / software CRA destino."""
    __tablename__ = "targets"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    host: Mapped[str] = mapped_column(String(255))
    port: Mapped[int] = mapped_column(Integer, default=7000)
    transport: Mapped[str] = mapped_column(String(8), default="tcp")       # tcp / udp
    receiver: Mapped[str] = mapped_column(String(8), default="0")          # R ('' = omitir)
    line: Mapped[str] = mapped_column(String(8), default="0")              # L
    enc_key: Mapped[str] = mapped_column(String(80), default="")
    key_format: Mapped[str] = mapped_column(String(8), default="hex")
    timeout: Mapped[float] = mapped_column(Float, default=10.0)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    include_timestamp: Mapped[bool] = mapped_column(Boolean, default=True)
    keep_alive: Mapped[bool] = mapped_column(Boolean, default=False)       # reutilizar conexión TCP
    notes: Mapped[str] = mapped_column(Text, default="")

    @property
    def label(self) -> str:
        return f"{self.name} ({self.transport.upper()} {self.host}:{self.port})"


class Panel(Base):
    """Panel / comunicador simulado."""
    __tablename__ = "panels"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    account: Mapped[str] = mapped_column(String(16))
    target_id: Mapped[int | None] = mapped_column(ForeignKey("targets.id", ondelete="SET NULL"))
    protocol: Mapped[str] = mapped_column(String(8), default="SIA-DCS")    # SIA-DCS / ADM-CID
    enc_key: Mapped[str] = mapped_column(String(80), default="")           # vacío = usa la del destino
    key_format: Mapped[str] = mapped_column(String(8), default="hex")
    encryption: Mapped[str] = mapped_column(String(8), default="auto")     # auto / on / off
    receiver: Mapped[str] = mapped_column(String(8), default="")           # vacío = el del destino
    line: Mapped[str] = mapped_column(String(8), default="")
    sequence: Mapped[int] = mapped_column(Integer, default=0)
    xdata_defaults: Mapped[dict[str, str]] = mapped_column(JSON, default=dict)  # S, O, M, X, Y...
    null_interval: Mapped[int] = mapped_column(Integer, default=0)         # s entre NULL (0 = no)
    test_interval: Mapped[int] = mapped_column(Integer, default=0)         # s entre RP / 602 (0 = no)
    schedules_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    target: Mapped[Target | None] = relationship()
    zones: Mapped[list["Zone"]] = relationship(back_populates="panel", cascade="all, delete-orphan",
                                               order_by="Zone.number")
    partitions: Mapped[list["Partition"]] = relationship(back_populates="panel", cascade="all, delete-orphan",
                                                         order_by="Partition.number")
    users: Mapped[list["PanelUser"]] = relationship(back_populates="panel", cascade="all, delete-orphan",
                                                    order_by="PanelUser.number")

    @property
    def label(self) -> str:
        return f"{self.account}" + (f" · {self.name}" if self.name else "")


class Zone(Base):
    __tablename__ = "zones"
    id: Mapped[int] = mapped_column(primary_key=True)
    panel_id: Mapped[int] = mapped_column(ForeignKey("panels.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(128), default="")
    partition: Mapped[str] = mapped_column(String(4), default="1")
    alarm_code: Mapped[str] = mapped_column(String(4), default="BA")       # código SIA por defecto
    cid_code: Mapped[str] = mapped_column(String(4), default="130")
    room: Mapped[str] = mapped_column(String(128), default="")             # xdata R
    panel: Mapped[Panel] = relationship(back_populates="zones")


class Partition(Base):
    __tablename__ = "partitions"
    id: Mapped[int] = mapped_column(primary_key=True)
    panel_id: Mapped[int] = mapped_column(ForeignKey("panels.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(128), default="")
    panel: Mapped[Panel] = relationship(back_populates="partitions")


class PanelUser(Base):
    __tablename__ = "panel_users"
    id: Mapped[int] = mapped_column(primary_key=True)
    panel_id: Mapped[int] = mapped_column(ForeignKey("panels.id", ondelete="CASCADE"))
    number: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(128), default="")
    panel: Mapped[Panel] = relationship(back_populates="users")


class Template(Base):
    """Plantilla de alarma reutilizable (EventSpec en JSON)."""
    __tablename__ = "templates"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    spec: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Scenario(Base):
    __tablename__ = "scenarios"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text, default="")
    panel_id: Mapped[int | None] = mapped_column(ForeignKey("panels.id", ondelete="SET NULL"))
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    repeat: Mapped[int] = mapped_column(Integer, default=1)                # 0 = infinito
    repeat_delay: Mapped[float] = mapped_column(Float, default=0)
    panel: Mapped[Panel | None] = relationship()


class SentMessage(Base):
    __tablename__ = "sent_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    source: Mapped[str] = mapped_column(String(16), default="manual")    # manual/escenario/null/test/carga/api
    panel_id: Mapped[int | None] = mapped_column(ForeignKey("panels.id", ondelete="SET NULL"), index=True)
    account: Mapped[str] = mapped_column(String(16), default="")
    target: Mapped[str] = mapped_column(String(255), default="")
    token: Mapped[str] = mapped_column(String(16), default="")
    sequence: Mapped[str] = mapped_column(String(4), default="")
    summary: Mapped[str] = mapped_column(String(255), default="")
    encrypted: Mapped[bool] = mapped_column(Boolean, default=False)
    plaintext: Mapped[str] = mapped_column(Text, default="")
    frame: Mapped[str] = mapped_column(Text, default="")
    result: Mapped[str] = mapped_column(String(16), default="")           # ACK/NAK/DUH/TIMEOUT/ERROR/SENT
    response: Mapped[str] = mapped_column(Text, default="")
    latency_ms: Mapped[float | None] = mapped_column(Float)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    error: Mapped[str] = mapped_column(Text, default="")


class ReceivedMessage(Base):
    """Mensajes recibidos por el receptor de pruebas integrado."""
    __tablename__ = "received_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    remote: Mapped[str] = mapped_column(String(64), default="")
    transport: Mapped[str] = mapped_column(String(8), default="")
    raw: Mapped[str] = mapped_column(Text, default="")
    decoded: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    response: Mapped[str] = mapped_column(String(8), default="")
    error: Mapped[str] = mapped_column(Text, default="")


class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
