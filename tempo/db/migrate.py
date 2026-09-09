"""Programmatic access to the Alembic migrations.

The migration directory ships inside the package, so the CLI can migrate
without an alembic.ini next to the working directory. The alembic.ini in
the repository root exists for interactive use during development.
"""

from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def alembic_config() -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    return config


def upgrade_to_head() -> None:
    """Apply every pending migration."""
    command.upgrade(alembic_config(), "head")


def head_revision() -> str:
    """The newest revision on disk.

    Read from the migration directory rather than written down anywhere, so
    a new revision does not have to be repeated in a second place.
    """
    head = ScriptDirectory.from_config(alembic_config()).get_current_head()
    if head is None:
        raise RuntimeError("no Alembic revisions found")
    return head


def current_revision(engine: Engine) -> str | None:
    """The revision the database is on, or None if it was never migrated."""
    with engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()
