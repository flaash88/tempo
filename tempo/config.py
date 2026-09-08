"""Runtime configuration, read from the environment.

Every value comes from an environment variable. Nothing is read from disk,
nothing is hard coded, and no secret is ever logged or serialised into an
API response. Deployment loads the variables from a file (``docker compose
env_file`` or ``uv run --env-file .env``); the application itself only ever
sees ``os.environ``.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Final

from pydantic import BaseModel, Field, SecretStr

TRUE_VALUES: Final = frozenset({"1", "true", "yes", "on"})


def _env_str(name: str, default: str = "") -> str:
    return os.environ.get(name, default).strip()


def _env_bool(name: str, default: bool = False) -> bool:
    raw = _env_str(name)
    if not raw:
        return default
    return raw.lower() in TRUE_VALUES


def _env_float(name: str, default: float) -> float:
    raw = _env_str(name)
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a number, got {raw!r}") from exc


class Settings(BaseModel):
    """Validated configuration snapshot.

    Secrets are held as ``SecretStr`` so that an accidental ``repr`` in a log
    line or a traceback cannot leak them.
    """

    model_config = {"frozen": True}

    # Storage. The database and the raw FIT files live in the data volume,
    # which is git-ignored and never read by tooling.
    data_dir: Path = Field(default=Path("data"))

    # intervals.icu
    intervals_api_key: SecretStr = Field(default=SecretStr(""))
    intervals_athlete_id: str = Field(default="0")

    # Anthropic
    anthropic_api_key: SecretStr = Field(default=SecretStr(""))
    anthropic_model_daily: str = Field(default="claude-sonnet-5")
    anthropic_model_planning: str = Field(default="claude-opus-5")
    monthly_budget_eur: float = Field(default=10.0, ge=0.0)

    # Single-user authentication
    password_hash: SecretStr = Field(default=SecretStr(""))

    # Optional Garmin direct connector, off unless explicitly enabled.
    garmin_direct_enabled: bool = Field(default=False)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "tempo.db"

    @property
    def fit_dir(self) -> Path:
        return self.data_dir / "fit"

    @property
    def database_url(self) -> str:
        return f"sqlite+pysqlite:///{self.db_path}"

    @property
    def has_intervals_credentials(self) -> bool:
        return bool(self.intervals_api_key.get_secret_value())

    @property
    def has_anthropic_credentials(self) -> bool:
        return bool(self.anthropic_api_key.get_secret_value())

    @property
    def has_password(self) -> bool:
        return bool(self.password_hash.get_secret_value())


def load_settings() -> Settings:
    """Build a settings object from the current environment."""
    return Settings(
        data_dir=Path(_env_str("TEMPO_DATA_DIR", "data")),
        intervals_api_key=SecretStr(_env_str("INTERVALS_API_KEY")),
        intervals_athlete_id=_env_str("INTERVALS_ATHLETE_ID", "0") or "0",
        anthropic_api_key=SecretStr(_env_str("ANTHROPIC_API_KEY")),
        anthropic_model_daily=(_env_str("ANTHROPIC_MODEL_DAILY") or "claude-sonnet-5"),
        anthropic_model_planning=(
            _env_str("ANTHROPIC_MODEL_PLANNING") or "claude-opus-5"
        ),
        monthly_budget_eur=_env_float("TEMPO_MONTHLY_BUDGET_EUR", 10.0),
        password_hash=SecretStr(_env_str("TEMPO_PASSWORD_HASH")),
        garmin_direct_enabled=_env_bool("GARMIN_DIRECT_ENABLED", False),
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Process-wide settings, read once."""
    return load_settings()
