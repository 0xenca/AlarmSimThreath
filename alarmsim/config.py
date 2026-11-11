"""Configuración de arranque (fichero alarmsim.env + variables de entorno)."""
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
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env_file(Path(os.environ.get("ALARMSIM_ENV_FILE", "alarmsim.env")))


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _bool(name: str, default: bool) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in ("1", "true", "yes", "si", "on")


@dataclass
class Config:
    database_url: str = field(default_factory=lambda: os.environ.get("DATABASE_URL", "sqlite:///./data/alarmsim.db"))
    secret_key: str = field(default_factory=lambda: os.environ.get("SECRET_KEY", ""))
    web_host: str = field(default_factory=lambda: os.environ.get("WEB_HOST", "0.0.0.0"))
    web_port: int = field(default_factory=lambda: _int("WEB_PORT", 8080))
    auth_enabled: bool = field(default_factory=lambda: _bool("AUTH_ENABLED", True))
    # Receptor de pruebas integrado (0 = desactivado)
    test_receiver_host: str = field(default_factory=lambda: os.environ.get("TEST_RECEIVER_HOST", "0.0.0.0"))
    test_receiver_port: int = field(default_factory=lambda: _int("TEST_RECEIVER_PORT", 7100))
    log_level: str = field(default_factory=lambda: os.environ.get("LOG_LEVEL", "INFO"))

    def __post_init__(self) -> None:
        if not self.secret_key:
            self.secret_key = secrets.token_urlsafe(48)
        if self.database_url.startswith("sqlite:///./"):
            Path(self.database_url.replace("sqlite:///", "")).parent.mkdir(parents=True, exist_ok=True)


config = Config()
