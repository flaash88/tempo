"""Alembic environment.

The URL comes from the application settings, so migrations always run
against the same database the app uses. Nothing is read from alembic.ini.
"""

from __future__ import annotations

from alembic import context
from sqlalchemy import Engine

from tempo.config import get_settings
from tempo.db.base import Base
from tempo.db.session import create_db_engine, ensure_data_dirs

# Importing the models registers every table on the shared metadata.
from tempo.db import models  # noqa: F401  isort:skip

target_metadata = Base.metadata


def _database_url() -> str:
    settings = get_settings()
    ensure_data_dirs(settings)
    return settings.database_url


def run_migrations_offline() -> None:
    context.configure(
        url=_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = context.config.attributes.get("connection", None)

    if isinstance(connectable, Engine):
        engine = connectable
    elif connectable is not None:
        # A live connection handed in by the test suite or by tempo init.
        context.configure(
            connection=connectable,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        return
    else:
        engine = create_db_engine(_database_url())

    with engine.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
