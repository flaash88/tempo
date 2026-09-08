"""Engine and session factory.

SQLite needs three things told to it explicitly on every connection: WAL so
that the nightly recompute does not block the API, foreign key enforcement
so that cascades actually cascade, and a busy timeout so that a concurrent
writer waits instead of raising.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from tempo.config import Settings, get_settings

BUSY_TIMEOUT_MS = 5_000


def _configure_connection(dbapi_connection: Any, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    finally:
        cursor.close()


def create_db_engine(url: str, *, echo: bool = False) -> Engine:
    """Create an engine with the SQLite pragmas Tempo relies on."""
    engine = create_engine(url, echo=echo, future=True)
    event.listen(engine, "connect", _configure_connection)
    return engine


def engine_for(settings: Settings | None = None) -> Engine:
    """Create the engine for the configured data directory."""
    settings = settings or get_settings()
    ensure_data_dirs(settings)
    return create_db_engine(settings.database_url)


def ensure_data_dirs(settings: Settings) -> Path:
    """Create the data volume layout if it is not there yet."""
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.fit_dir.mkdir(parents=True, exist_ok=True)
    return settings.data_dir


def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(engine: Engine) -> Iterator[Session]:
    """Transactional scope around a series of operations."""
    factory = session_factory(engine)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
