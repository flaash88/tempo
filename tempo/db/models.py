"""ORM models.

Unit suffixes are part of every column name — a number without a unit is a
bug waiting to happen. Nullability carries meaning throughout: ``None`` is
"not known", never "zero". Gaps in the athlete's history are the normal
case, so almost every measured value is optional.
"""

from __future__ import annotations

import datetime as dt
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tempo.db.base import Base


class DataSource(StrEnum):
    """Where a record came from."""

    INTERVALS = "intervals"
    FIT = "fit"
    GARMIN = "garmin"
    MANUAL = "manual"


class SyncStatus(StrEnum):
    """Terminal state of a sync run."""

    RUNNING = "running"
    OK = "ok"
    PARTIAL = "partial"
    FAILED = "failed"
    DISABLED = "disabled"


class ZoneModel(StrEnum):
    """Which model derives the heart rate zones."""

    FRIEL_RUN_LTHR = "friel_run_lthr"
    PERCENT_HR_MAX = "percent_hr_max"


def utcnow() -> dt.datetime:
    return dt.datetime.now(tz=dt.UTC)


class Activity(Base):
    """One training session.

    The primary key is the intervals.icu activity id, so a repeated import
    updates the existing row instead of duplicating it.
    """

    __tablename__ = "activity"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    # Wall clock time at the athlete's location. Stored naive on purpose:
    # "the 6 a.m. run" stays the 6 a.m. run across time zone changes.
    start_local: Mapped[dt.datetime] = mapped_column(DateTime, nullable=False)
    sport: Mapped[str] = mapped_column(String(32), nullable=False)
    distance_m: Mapped[float | None] = mapped_column(Float)
    moving_s: Mapped[int | None] = mapped_column(Integer)
    elapsed_s: Mapped[int | None] = mapped_column(Integer)
    elevation_gain_m: Mapped[float | None] = mapped_column(Float)
    avg_hr: Mapped[int | None] = mapped_column(Integer)
    max_hr: Mapped[int | None] = mapped_column(Integer)
    avg_pace_s_per_km: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DataSource.INTERVALS
    )
    # Path of the raw FIT file inside the data volume, relative to it.
    fit_path: Mapped[str | None] = mapped_column(Text)
    imported_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )

    streams: Mapped[list[ActivityStream]] = relationship(
        back_populates="activity",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    laps: Mapped[list[Lap]] = relationship(
        back_populates="activity",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )

    __table_args__ = (Index("ix_activity_start_local", "start_local"),)


class ActivityStream(Base):
    """One second of a session, normalised to 1 Hz.

    Deliberately narrow and deliberately sparse: a gap in the recording is
    stored as ``None`` per channel and is never interpolated.
    """

    __tablename__ = "activity_stream"

    activity_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("activity.id", ondelete="CASCADE"),
        primary_key=True,
    )
    offset_s: Mapped[int] = mapped_column(Integer, primary_key=True)
    hr: Mapped[int | None] = mapped_column(Integer)
    speed_m_s: Mapped[float | None] = mapped_column(Float)
    altitude_m: Mapped[float | None] = mapped_column(Float)
    cadence: Mapped[int | None] = mapped_column(Integer)
    power: Mapped[int | None] = mapped_column(Integer)
    lat: Mapped[float | None] = mapped_column(Float)
    lon: Mapped[float | None] = mapped_column(Float)

    activity: Mapped[Activity] = relationship(back_populates="streams")


class Lap(Base):
    """A lap or interval within a session."""

    __tablename__ = "lap"

    activity_id: Mapped[str] = mapped_column(
        String(64),
        ForeignKey("activity.id", ondelete="CASCADE"),
        primary_key=True,
    )
    index: Mapped[int] = mapped_column("index", Integer, primary_key=True)
    distance_m: Mapped[float | None] = mapped_column(Float)
    duration_s: Mapped[int | None] = mapped_column(Integer)
    avg_hr: Mapped[int | None] = mapped_column(Integer)
    avg_pace_s_per_km: Mapped[float | None] = mapped_column(Float)

    activity: Mapped[Activity] = relationship(back_populates="laps")


class WellnessDay(Base):
    """Daily wellness readings. Missing days simply have no row."""

    __tablename__ = "wellness_day"

    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    resting_hr: Mapped[int | None] = mapped_column(Integer)
    # Heart rate variability, deliberately not named after a metric. Which
    # measure this is depends on the source, so the source's own field name
    # travels with the value in hrv_source_field and nothing downstream has
    # to assume it is rMSSD. A change of that field breaks the baseline:
    # comparing one measure against another would be meaningless.
    hrv: Mapped[float | None] = mapped_column(Float)
    hrv_source_field: Mapped[str | None] = mapped_column(String(32))
    sleep_secs: Mapped[int | None] = mapped_column(Integer)
    sleep_score: Mapped[int | None] = mapped_column(Integer)
    vo2max: Mapped[float | None] = mapped_column(Float)
    weight_kg: Mapped[float | None] = mapped_column(Float)
    # Only the optional Garmin connector supplies these two; intervals.icu
    # does not carry them. Both stay None when that connector is off.
    body_battery: Mapped[int | None] = mapped_column(Integer)
    training_readiness: Mapped[int | None] = mapped_column(Integer)
    # How the day actually felt, as the athlete entered it. Stored on the
    # source's own scale and, for now, read by nothing: these are the only
    # way the readiness weights can later be calibrated against real
    # perception rather than against each other. Any evaluation has to wait
    # until the scale has been confirmed against a live account.
    fatigue: Mapped[int | None] = mapped_column(Integer)
    soreness: Mapped[int | None] = mapped_column(Integer)
    mood: Mapped[int | None] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DataSource.INTERVALS
    )


class DailyLoad(Base):
    """Aggregated training load for one calendar day.

    A day without a session is materialised with zeroes so that CTL and ATL
    decay correctly — empty days are never skipped. ``None`` in a load
    column means a session exists but the metric could not be derived from
    it, for example TRIMP for a run recorded without heart rate.
    """

    __tablename__ = "daily_load"

    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    trimp: Mapped[float | None] = mapped_column(Float)
    hr_tss: Mapped[float | None] = mapped_column(Float)
    r_tss: Mapped[float | None] = mapped_column(Float)
    duration_s: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    distance_m: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)


class FitnessDay(Base):
    """Fitness, fatigue and form for one calendar day.

    Every value is optional: below the minimum history the series has no
    defensible value yet, and ``confidence``/``days_of_history`` describe
    how far the build-up has come.
    """

    __tablename__ = "fitness_day"

    date: Mapped[dt.date] = mapped_column(Date, primary_key=True)
    ctl: Mapped[float | None] = mapped_column(Float)
    atl: Mapped[float | None] = mapped_column(Float)
    tsb: Mapped[float | None] = mapped_column(Float)
    acwr: Mapped[float | None] = mapped_column(Float)
    monotony: Mapped[float | None] = mapped_column(Float)
    strain: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    days_of_history: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    __table_args__ = (
        CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="confidence_range",
        ),
    )


class AthleteSettings(Base):
    """Single-user athlete profile. Exactly one row, id 1."""

    __tablename__ = "athlete_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    hr_max: Mapped[int | None] = mapped_column(Integer)
    hr_rest: Mapped[int | None] = mapped_column(Integer)
    lthr: Mapped[int | None] = mapped_column(Integer)
    threshold_pace_s_per_km: Mapped[float | None] = mapped_column(Float)
    # The athlete's own sleep target. None falls back to the documented
    # default in tempo.metrics.thresholds.
    sleep_target_s: Mapped[int | None] = mapped_column(Integer)
    zone_model: Mapped[str] = mapped_column(
        String(32), nullable=False, default=ZoneModel.FRIEL_RUN_LTHR
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)


class AiCall(Base):
    """One call to the language model, for budget accounting."""

    __tablename__ = "ai_call"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    endpoint: Mapped[str] = mapped_column(String(64), nullable=False)
    model: Mapped[str] = mapped_column(String(64), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_eur: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    __table_args__ = (Index("ix_ai_call_ts", "ts"),)


class PlannedWorkout(Base):
    """A planned session, as the source's calendar has it.

    Mirrors what ``GET /athlete/0/events`` returns, including entries whose
    ``category`` is not a workout at all — that is what the column is for.
    Carries no sync status: pushing sessions back to the watch is phase 6,
    and its bookkeeping belongs to that phase.
    """

    __tablename__ = "planned_workout"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    date: Mapped[dt.date] = mapped_column(Date, nullable=False)
    category: Mapped[str | None] = mapped_column(String(32))
    sport: Mapped[str | None] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    target_time_s: Mapped[int | None] = mapped_column(Integer)
    target_dist_m: Mapped[float | None] = mapped_column(Float)
    target_load: Mapped[float | None] = mapped_column(Float)
    # The structured workout as the source describes it, stored verbatim.
    workout_doc: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    # The idempotency key. Nullable, because an entry created in the
    # source's own interface has none; SQLite allows repeated NULLs in a
    # unique column, which is exactly what that case needs.
    external_id: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )
    source: Mapped[str] = mapped_column(
        String(16), nullable=False, default=DataSource.INTERVALS
    )

    __table_args__ = (
        UniqueConstraint("external_id", name="uq_planned_workout_external_id"),
        Index("ix_planned_workout_date", "date"),
    )


class SyncState(Base):
    """The incremental sync watermark, one row per source.

    Kept apart from ``sync_log``, which is a pure audit trail: this table is
    the only thing the next run reads to decide where to resume. Activities
    and wellness carry their own marks because either can fail on its own,
    and a mark is only moved forward once its part has been processed **and
    committed** — a half-imported window must be fetched again, not skipped.
    """

    __tablename__ = "sync_state"

    source: Mapped[str] = mapped_column(String(16), primary_key=True)
    # Naive local time, matching activity.start_local.
    last_activity_start: Mapped[dt.datetime | None] = mapped_column(DateTime)
    last_wellness_date: Mapped[dt.date | None] = mapped_column(Date)
    last_success_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    # Opaque continuation token for sources that hand one out.
    cursor: Mapped[str | None] = mapped_column(Text)


class SyncLog(Base):
    """One sync run against one source.

    ``detail`` holds a human readable summary or an error message. It must
    never contain a credential — see ``tempo.ingest`` for the redaction the
    clients apply before anything reaches this column.
    """

    __tablename__ = "sync_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    started_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow
    )
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=SyncStatus.RUNNING
    )
    detail: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (Index("ix_sync_log_source_started_at", "source", "started_at"),)
