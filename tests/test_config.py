"""Configuration is read from the environment and keeps secrets opaque."""

from __future__ import annotations

from pathlib import Path

import pytest

from tempo.config import Settings, load_settings
from tests import REPO_ROOT


def test_defaults_are_usable_without_any_environment(data_dir: Path) -> None:
    settings = load_settings()

    assert settings.intervals_athlete_id == "0"
    assert settings.anthropic_model_daily == "claude-sonnet-5"
    assert settings.anthropic_model_planning == "claude-opus-5"
    assert settings.garmin_direct_enabled is False
    assert settings.has_intervals_credentials is False
    assert settings.has_password is False


def test_paths_derive_from_the_data_directory(data_dir: Path) -> None:
    settings = load_settings()

    assert settings.db_path == data_dir / "tempo.db"
    assert settings.fit_dir == data_dir / "fit"
    assert settings.database_url.endswith(str(data_dir / "tempo.db"))


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("true", True),
        ("True", True),
        ("1", True),
        ("yes", True),
        ("false", False),
        ("0", False),
        ("", False),
        ("nonsense", False),
    ],
)
def test_garmin_flag_is_off_unless_clearly_enabled(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool
) -> None:
    monkeypatch.setenv("GARMIN_DIRECT_ENABLED", raw)

    assert load_settings().garmin_direct_enabled is expected


def test_secrets_do_not_appear_in_repr_or_serialisation(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("INTERVALS_API_KEY", "super-secret-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    monkeypatch.setenv("TEMPO_PASSWORD_HASH", "scrypt$1$2$3$c2FsdA==$aGFzaA==")

    settings = load_settings()

    rendered = f"{settings!r} {settings.model_dump_json()} {settings.model_dump()}"
    assert "super-secret-key" not in rendered
    assert "sk-ant-secret" not in rendered
    assert "c2FsdA==" not in rendered
    # The values are still reachable where they are actually needed.
    assert settings.intervals_api_key.get_secret_value() == "super-secret-key"


def test_budget_must_be_a_number(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEMPO_MONTHLY_BUDGET_EUR", "zehn")

    with pytest.raises(ValueError, match="TEMPO_MONTHLY_BUDGET_EUR"):
        load_settings()


def test_settings_are_immutable(data_dir: Path) -> None:
    settings = load_settings()

    with pytest.raises(ValueError):
        settings.intervals_athlete_id = "42"


def test_env_example_documents_every_configured_variable() -> None:
    """A variable the code reads but .env.example omits is a trap."""
    example = (REPO_ROOT / ".env.example").read_text(encoding="utf-8")
    documented = {
        line.split("=", 1)[0].strip()
        for line in example.splitlines()
        if line and not line.startswith("#") and "=" in line
    }

    assert documented == {
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
    }


def test_settings_model_rejects_a_negative_budget() -> None:
    with pytest.raises(ValueError):
        Settings(monthly_budget_eur=-1.0)
