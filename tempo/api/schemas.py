"""Response schemas.

The metric envelope is the contract for the whole API: no endpoint returns
a bare number. Every value arrives with the evidence behind it, so the
interface can tell "63" apart from "63, but from a three month old block"
and from "not yet, 34 of 42 days".
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import BaseModel, Field


class MetricEnvelope[T](BaseModel):
    """A single metric together with how much it can be trusted."""

    value: T | None = Field(
        default=None,
        description="None while the minimum history is not reached.",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Window fill ratio multiplied by recency.",
    )
    days_of_history: int = Field(
        default=0,
        ge=0,
        description="Days in the current unbroken window.",
    )
    have: int = Field(default=0, ge=0)
    required: int = Field(default=0, ge=0)
    available_from: dt.date | None = Field(
        default=None,
        description="Earliest date the value can exist, if data continues.",
    )
    last_data_point: dt.date | None = None
    stale: bool = Field(
        default=False,
        description="Last data point older than the staleness threshold.",
    )


class HealthResponse(BaseModel):
    """Liveness answer. Deliberately free of any athlete data."""

    status: Literal["ok"] = "ok"
    version: str
    database: Literal["ok", "missing", "error"]
    schema_revision: str | None = Field(
        default=None,
        description="Applied Alembic revision, None if not migrated yet.",
    )
