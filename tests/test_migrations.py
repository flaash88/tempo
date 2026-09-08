"""The schema in the migrations and the schema in the models agree."""

from __future__ import annotations

from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect

from tempo.db.base import Base
from tempo.db.migrate import current_revision

EXPECTED_TABLES = {
    "activity",
    "activity_stream",
    "lap",
    "wellness_day",
    "daily_load",
    "fitness_day",
    "athlete_settings",
    "ai_call",
    "sync_log",
}


def test_upgrade_creates_every_table(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())

    assert tables >= EXPECTED_TABLES


def test_upgrade_records_a_revision(engine: Engine) -> None:
    assert current_revision(engine) == "0001"


def test_unmigrated_database_reports_no_revision(unmigrated_engine: Engine) -> None:
    assert current_revision(unmigrated_engine) is None


def test_models_and_migrations_do_not_drift(engine: Engine) -> None:
    """Autogenerate must find nothing left to do after an upgrade."""
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        diff = compare_metadata(context, Base.metadata)

    assert diff == []


def test_sqlite_runs_in_wal_mode(engine: Engine) -> None:
    with engine.connect() as connection:
        journal_mode = connection.exec_driver_sql("PRAGMA journal_mode").scalar()
        foreign_keys = connection.exec_driver_sql("PRAGMA foreign_keys").scalar()

    assert journal_mode == "wal"
    assert foreign_keys == 1
