"""Shared test fixtures.

Every test runs against a throwaway database in a temporary directory. The
production data directory is never opened: ``TEMPO_DATA_DIR`` is redirected
before any settings object is built, and the settings cache is cleared so
no earlier value survives into a test.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from tempo.config import Settings, get_settings, load_settings
from tempo.db.migrate import upgrade_to_head
from tempo.db.session import create_db_engine, ensure_data_dirs, session_factory

# Environment variables that must not leak from the developer's shell into
# a test run. Cleared for every test.
_CLEARED_ENV = (
    "INTERVALS_API_KEY",
    "INTERVALS_ATHLETE_ID",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL_DAILY",
    "ANTHROPIC_MODEL_PLANNING",
    "TEMPO_MONTHLY_BUDGET_EUR",
    "TEMPO_PASSWORD_HASH",
    "GARMIN_DIRECT_ENABLED",
    "GARMIN_EMAIL",
    "GARMIN_PASSWORD",
    "TEMPO_READINESS_WEIGHT_HRV",
    "TEMPO_READINESS_WEIGHT_RESTING_HR",
    "TEMPO_READINESS_WEIGHT_SLEEP",
    "TEMPO_READINESS_WEIGHT_TSB",
)


@pytest.fixture
def data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """An isolated data directory, active for the duration of one test."""
    for name in _CLEARED_ENV:
        monkeypatch.delenv(name, raising=False)
    target = tmp_path / "data"
    monkeypatch.setenv("TEMPO_DATA_DIR", str(target))
    get_settings.cache_clear()
    yield target
    get_settings.cache_clear()


@pytest.fixture
def settings(data_dir: Path) -> Settings:
    built = load_settings()
    ensure_data_dirs(built)
    return built


@pytest.fixture
def unmigrated_engine(settings: Settings) -> Iterator[Engine]:
    """An engine on an empty database — the state right after install."""
    engine = create_db_engine(settings.database_url)
    try:
        yield engine
    finally:
        engine.dispose()


@pytest.fixture
def engine(settings: Settings) -> Iterator[Engine]:
    """An engine on a database migrated to head."""
    upgrade_to_head()
    created = create_db_engine(settings.database_url)
    try:
        yield created
    finally:
        created.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    factory = session_factory(engine)
    with factory() as open_session:
        yield open_session
