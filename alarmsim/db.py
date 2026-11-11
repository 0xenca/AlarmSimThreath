from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import config


class Base(DeclarativeBase):
    pass


_sqlite = config.database_url.startswith("sqlite")
engine = create_engine(config.database_url, connect_args={"check_same_thread": False, "timeout": 30} if _sqlite else {})

if _sqlite:
    @event.listens_for(engine, "connect")
    def _pragmas(conn, _):  # pragma: no cover
        cur = conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


@contextmanager
def session_scope() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_db() -> Iterator[Session]:
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()
