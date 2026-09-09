"""Recompute pipeline.

Reads the raw data the ingestion stored and writes the derived tables. This
is the only place in the backend that both touches the database and calls
the metric engine; the engine itself is pure, and everything it needs is
handed to it as sequences.

Two things are worth stating about what lands in ``daily_load`` and
``fitness_day``:

* **``None`` is not zero.** A day without a session carries load zero,
  because a rest day really is no load and that is what makes the fitness
  curve decay. A day *with* a session whose load could not be derived —
  a run recorded without heart rate, or one with no threshold pace to
  compare against — carries ``None``, and nothing further treats that as
  a rest day.
* **The curves are computed everywhere and delivered selectively.** CTL and
  ATL run over every calendar day, including the untracked ones, because
  that is what "no skipping empty days" means. Whether a day's values are
  written out is then decided by the window rules in ``confidence``: a
  curve carried across three months in which the watch was in a drawer is
  arithmetic, not information, and those rows stay empty with their
  progress recorded instead.

The whole thing is idempotent. Running it again on unchanged data produces
the same rows, and running it after the metric engine gains a formula fills
the columns in without any need to re-import anything.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from tempo.db.models import (
    Activity,
    ActivityStream,
    AthleteSettings,
    DailyLoad,
    FitnessDay,
    WellnessDay,
)
from tempo.db.session import session_scope
from tempo.metrics import load as load_metrics
from tempo.metrics.confidence import (
    confidence_of,
    window_lengths_by_day,
)
from tempo.metrics.fitness import acwr, fitness_series, monotony, strain
from tempo.metrics.thresholds import MIN_DAYS_FORM, MONOTONY_WINDOW_DAYS
from tempo.metrics.zones import zones_for_athlete

log = logging.getLogger(__name__)

# Written in batches so a long history does not build one giant statement.
_BATCH_DAYS: Final = 365

_SECONDS_PER_KM: Final = 1000.0


@dataclass(frozen=True, slots=True)
class ActivityLoad:
    """What one session cost, as far as the data allows."""

    activity_id: str
    date: dt.date
    duration_s: int
    distance_m: float
    trimp: float | None = None
    hr_tss: float | None = None
    r_tss: float | None = None

    @property
    def primary(self) -> float | None:
        """The load the fitness curve is built from.

        rTSS where a threshold pace makes it computable, hrTSS otherwise —
        both are normalised so an hour at threshold is a hundred, which is
        what makes them interchangeable in one series. Raw TRIMP is not: it
        is on its own scale, and mixing scales would put a step in the
        curve every time a chest strap was forgotten.
        """
        if self.r_tss is not None:
            return self.r_tss
        return self.hr_tss


@dataclass(frozen=True, slots=True)
class FitnessValues:
    """One row of the fitness series, ready to be written."""

    date: dt.date
    ctl: float | None
    atl: float | None
    tsb: float | None
    acwr: float | None
    monotony: float | None
    strain: float | None
    confidence: float
    days_of_history: int


@dataclass
class RecomputeReport:
    days: int = 0
    first_day: dt.date | None = None
    last_day: dt.date | None = None
    days_with_session: int = 0
    activities: int = 0
    activities_with_load: int = 0
    form_days: int = 0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        if self.days == 0:
            return "no data to recompute"
        parts = [
            f"{self.days} days from {self.first_day} to {self.last_day}",
            f"{self.days_with_session} with a session",
            f"{self.activities_with_load} of {self.activities} activities scored",
            f"{self.form_days} days with a form value",
        ]
        if self.notes:
            parts.append(f"notes: {'; '.join(self.notes[:5])}")
        return ", ".join(parts)


def _threshold_speed(settings: AthleteSettings | None) -> float | None:
    if settings is None or not settings.threshold_pace_s_per_km:
        return None
    if settings.threshold_pace_s_per_km <= 0:
        return None
    return _SECONDS_PER_KM / settings.threshold_pace_s_per_km


def _as_date(value: object) -> dt.date | None:
    """SQLite hands dates back as strings often enough to be worth this."""
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        try:
            return dt.date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _stream_of(
    session: Session, activity_id: str
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    """Speed, altitude and heart rate of one activity, in recorded order."""
    rows = session.execute(
        select(
            ActivityStream.speed_m_s,
            ActivityStream.altitude_m,
            ActivityStream.hr,
        )
        .where(ActivityStream.activity_id == activity_id)
        .order_by(ActivityStream.offset_s)
    ).all()
    speeds: list[float | None] = []
    altitudes: list[float | None] = []
    rates: list[float | None] = []
    for speed, altitude, rate in rows:
        speeds.append(speed)
        altitudes.append(altitude)
        rates.append(None if rate is None else float(rate))
    return speeds, altitudes, rates


def score_activity(
    session: Session,
    activity: Activity,
    settings: AthleteSettings | None,
) -> ActivityLoad:
    """Derive the load of one session from its summary and its stream.

    Every metric is optional on its own. A run without heart rate has no
    TRIMP and no hrTSS but may still have rTSS; a run without a threshold
    pace configured is the other way round; a run with neither has a
    duration and a distance and nothing else, and says so.
    """
    hr_rest = settings.hr_rest if settings else None
    hr_max = settings.hr_max if settings else None
    lthr = settings.lthr if settings else None

    trimp = load_metrics.trimp_banister(
        activity.moving_s,
        None if activity.avg_hr is None else float(activity.avg_hr),
        hr_rest,
        hr_max,
    )
    reference = load_metrics.trimp_at_threshold_hour(lthr, hr_rest, hr_max)
    hr_tss = load_metrics.hr_tss(trimp, reference)

    r_tss: float | None = None
    threshold_speed = _threshold_speed(settings)
    if threshold_speed is not None:
        speeds, altitudes, _rates = _stream_of(session, activity.id)
        if speeds:
            ngp = load_metrics.normalised_graded_speed(speeds, altitudes)
            r_tss = load_metrics.r_tss(
                activity.moving_s,
                load_metrics.intensity_factor(ngp, threshold_speed),
            )

    return ActivityLoad(
        activity_id=activity.id,
        date=activity.start_local.date(),
        duration_s=activity.moving_s or 0,
        distance_m=activity.distance_m or 0.0,
        trimp=trimp,
        hr_tss=hr_tss,
        r_tss=r_tss,
    )


def _sum_optional(values: Sequence[float | None]) -> float | None:
    """Sum the values that exist, or ``None`` if none of them do.

    A day with two sessions where only one could be scored reports the one
    it has rather than nothing — but a day where neither could be scored
    stays unknown rather than becoming zero.
    """
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present)


def _earliest_evidence(session: Session) -> dt.date | None:
    """The first day anything is known about, activity or wellness."""
    candidates: list[dt.date] = []
    first_activity = _as_date(session.scalar(select(func.min(Activity.start_local))))
    first_wellness = _as_date(session.scalar(select(func.min(WellnessDay.date))))
    if first_activity is not None:
        candidates.append(first_activity)
    if first_wellness is not None:
        candidates.append(first_wellness)
    return min(candidates) if candidates else None


def recompute_all(engine: Engine, *, today: dt.date | None = None) -> RecomputeReport:
    """Rebuild the daily load calendar and the fitness series."""
    today = today or dt.datetime.now(tz=dt.UTC).date()
    report = RecomputeReport()

    with session_scope(engine) as session:
        first_day = _earliest_evidence(session)
        if first_day is None:
            log.info("recompute found no data")
            return report
        last_day = max(first_day, today)

        settings = session.get(AthleteSettings, 1)
        if settings is None or not (settings.hr_max and settings.hr_rest):
            report.notes.append("no heart rate settings: TRIMP and hrTSS unavailable")
        if _threshold_speed(settings) is None:
            report.notes.append("no threshold pace: rTSS unavailable")
        if (
            zones_for_athlete(
                lthr=settings.lthr if settings else None,
                hr_max=settings.hr_max if settings else None,
            )
            is None
        ):
            report.notes.append("no zone model: time in zone unavailable")

        activities = list(
            session.scalars(select(Activity).order_by(Activity.start_local))
        )
        report.activities = len(activities)
        scored = [
            score_activity(session, activity, settings) for activity in activities
        ]

        wellness_days = [
            day
            for day in (
                _as_date(value) for value in session.scalars(select(WellnessDay.date))
            )
            if day is not None
        ]

    report.activities_with_load = sum(
        1 for entry in scored if entry.primary is not None
    )
    per_day: dict[dt.date, list[ActivityLoad]] = {}
    for entry in scored:
        per_day.setdefault(entry.date, []).append(entry)

    report.first_day = first_day
    report.last_day = last_day
    report.days = (last_day - first_day).days + 1
    report.days_with_session = len(per_day)

    calendar = [first_day + dt.timedelta(days=offset) for offset in range(report.days)]

    day = first_day
    while day <= last_day:
        chunk_end = min(day + dt.timedelta(days=_BATCH_DAYS - 1), last_day)
        with session_scope(engine) as session:
            _write_daily_load(session, day, chunk_end, per_day)
        day = chunk_end + dt.timedelta(days=1)

    report.form_days = _write_fitness(
        engine,
        calendar=calendar,
        per_day=per_day,
        wellness_days=wellness_days,
    )

    log.info(
        "recompute finished",
        extra={"days": report.days, "form_days": report.form_days},
    )
    return report


def _write_daily_load(
    session: Session,
    first_day: dt.date,
    last_day: dt.date,
    per_day: dict[dt.date, list[ActivityLoad]],
) -> None:
    existing = {
        row.date: row
        for row in session.scalars(
            select(DailyLoad).where(
                DailyLoad.date >= first_day, DailyLoad.date <= last_day
            )
        )
    }

    day = first_day
    while day <= last_day:
        row = existing.get(day)
        if row is None:
            row = DailyLoad(date=day)
            session.add(row)

        entries = per_day.get(day)
        if entries:
            row.duration_s = sum(entry.duration_s for entry in entries)
            row.distance_m = sum(entry.distance_m for entry in entries)
            row.trimp = _sum_optional([entry.trimp for entry in entries])
            row.hr_tss = _sum_optional([entry.hr_tss for entry in entries])
            row.r_tss = _sum_optional([entry.r_tss for entry in entries])
        else:
            # No session: the load really is zero, and saying so is what
            # makes the fitness curve decay instead of stalling.
            row.duration_s = 0
            row.distance_m = 0.0
            row.trimp = 0.0
            row.hr_tss = 0.0
            row.r_tss = 0.0
        day += dt.timedelta(days=1)


def _write_fitness(
    engine: Engine,
    *,
    calendar: Sequence[dt.date],
    per_day: dict[dt.date, list[ActivityLoad]],
    wellness_days: Sequence[dt.date],
) -> int:
    """Write the fitness series, with each day's own confidence.

    The window length is taken as of each day rather than as of today: a row
    from six months ago describes what was known then, and stamping today's
    confidence on it would be a claim about the past that was never true.
    """
    daily_primary: dict[dt.date, float | None] = {}
    for day, entries in per_day.items():
        daily_primary[day] = _sum_optional([entry.primary for entry in entries])

    series_input = [(day, daily_primary.get(day, 0.0)) for day in calendar]
    points = fitness_series(series_input)
    loads = [point.load for point in points]

    tracked = set(wellness_days) | set(per_day)
    states = window_lengths_by_day(tracked, calendar)

    rows: list[FitnessValues] = []
    for index, point in enumerate(points):
        state = states[index]
        enough = state.length >= MIN_DAYS_FORM
        trailing = loads[: index + 1]
        week = trailing[-MONOTONY_WINDOW_DAYS:]
        full_week = enough and len(trailing) >= MONOTONY_WINDOW_DAYS
        rows.append(
            FitnessValues(
                date=point.date,
                ctl=point.ctl if enough else None,
                atl=point.atl if enough else None,
                tsb=point.tsb if enough else None,
                acwr=acwr(trailing) if enough else None,
                monotony=monotony(week) if full_week else None,
                strain=strain(week) if full_week else None,
                confidence=confidence_of(
                    state.length, MIN_DAYS_FORM, state.last_data_point, state.date
                ),
                days_of_history=state.length,
            )
        )

    for start in range(0, len(rows), _BATCH_DAYS):
        with session_scope(engine) as session:
            _write_fitness_chunk(session, rows[start : start + _BATCH_DAYS])

    return sum(1 for row in rows if row.ctl is not None)


def _write_fitness_chunk(session: Session, chunk: Sequence[FitnessValues]) -> None:
    if not chunk:
        return
    existing = {
        row.date: row
        for row in session.scalars(
            select(FitnessDay).where(
                FitnessDay.date >= chunk[0].date,
                FitnessDay.date <= chunk[-1].date,
            )
        )
    }
    for values in chunk:
        row = existing.get(values.date)
        if row is None:
            row = FitnessDay(date=values.date)
            session.add(row)
        row.ctl = values.ctl
        row.atl = values.atl
        row.tsb = values.tsb
        row.acwr = values.acwr
        row.monotony = values.monotony
        row.strain = values.strain
        row.confidence = values.confidence
        row.days_of_history = values.days_of_history
