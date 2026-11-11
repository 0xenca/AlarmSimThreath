"""Configuración de arranque (fichero .env + variables de entorno).

Los parámetros que requieren reinicio (puertos, base de datos, clave secreta)
se leen aquí. Los parámetros de negocio (SMTP, categorías que generan alarma,
tolerancias, etc.) se guardan en la base de datos y se editan desde la web.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


ENV_FILE = Path(os.environ.get("ALARMHUB_ENV_FILE", "alarmhub.env"))
_load_env_file(ENV_FILE)


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "si", "on")


@dataclass
class Config:
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", "sqlite:///./data/alarmhub.db"))
    secret_key: str = field(default_factory=lambda: os.environ.get("SECRET_KEY", ""))
    web_host: str = field(default_factory=lambda: os.environ.get("WEB_HOST", "0.0.0.0"))
    web_port: int = field(default_factory=lambda: _int("WEB_PORT", 8080))
    dc09_host: str = field(default_factory=lambda: os.environ.get("DC09_HOST", "0.0.0.0"))
    dc09_tcp_port: int = field(default_factory=lambda: _int("DC09_TCP_PORT", 7000))
    dc09_udp_port: int = field(default_factory=lambda: _int("DC09_UDP_PORT", 7000))
    surgard_port: int = field(default_factory=lambda: _int("SURGARD_PORT", 0))
    receivers_enabled: bool = field(default_factory=lambda: _bool("RECEIVERS_ENABLED", True))
    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO"))
    secure_cookies: bool = field(default_factory=lambda: _bool("SECURE_COOKIES", False))

    def __post_init__(self) -> None:
        if not self.secret_key:
            # Sin clave fija las sesiones se invalidan en cada reinicio.
            self.secret_key = secrets.token_urlsafe(48)
        if self.database_url.startswith("sqlite:///./"):
            Path(self.database_url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)


config = Config()
