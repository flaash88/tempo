"""Recompute pipeline.

Phase 2 builds the calendar this pipeline runs on: a ``daily_load`` row for
every day from the first piece of evidence up to today, with no day
skipped. Days without a session carry load zero so that CTL and ATL decay
correctly instead of freezing over a gap.

Only the aggregation lives here — summing the duration and distance of the
sessions on a day. The formulas that turn a session into TRIMP, hrTSS and
rTSS belong to the metric engine in phase 3, and until they exist those
columns stay ``None`` on any day that has a session: "not computed yet" is
not the same statement as "zero load".
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Final

from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from tempo.db.models import Activity, DailyLoad, WellnessDay
from tempo.db.session import session_scope

log = logging.getLogger(__name__)

# Written in batches so a long history does not build one giant statement.
_BATCH_DAYS: Final = 365


@dataclass
class RecomputeReport:
    days: int = 0
    first_day: dt.date | None = None
    last_day: dt.date | None = None
    days_with_session: int = 0

    def summary(self) -> str:
        if self.days == 0:
            return "no data to recompute"
        return (
            f"{self.days} days from {self.first_day} to {self.last_day}, "
            f"{self.days_with_session} with a session"
        )


def _earliest_evidence(session: Session) -> dt.date | None:
    """The first day anything is known about, activity or wellness."""
    first_activity = session.scalar(select(func.min(Activity.start_local)))
    first_wellness = session.scalar(select(func.min(WellnessDay.date)))

    candidates: list[dt.date] = []
    if isinstance(first_activity, dt.datetime):
        candidates.append(first_activity.date())
    elif isinstance(first_activity, str):
        candidates.append(dt.datetime.fromisoformat(first_activity).date())
    if isinstance(first_wellness, dt.date):
        candidates.append(first_wellness)
    elif isinstance(first_wellness, str):
        candidates.append(dt.date.fromisoformat(first_wellness))
    return min(candidates) if candidates else None


def _session_totals(
    session: Session, first_day: dt.date, last_day: dt.date
) -> dict[dt.date, tuple[int, float]]:
    """Duration and distance summed per calendar day.

    A session with an unknown duration or distance contributes nothing
    rather than being guessed at, but the day still counts as having one.
    """
    totals: dict[dt.date, tuple[int, float]] = {}
    rows = session.execute(
        select(Activity.start_local, Activity.moving_s, Activity.distance_m).where(
            Activity.start_local >= dt.datetime.combine(first_day, dt.time.min),
            Activity.start_local <= dt.datetime.combine(last_day, dt.time.max),
        )
    ).all()
    for start_local, moving_s, distance_m in rows:
        day = start_local.date()
        duration, distance = totals.get(day, (0, 0.0))
        totals[day] = (
            duration + (moving_s or 0),
            distance + (distance_m or 0.0),
        )
    return totals


def recompute_all(engine: Engine, *, today: dt.date | None = None) -> RecomputeReport:
    """Materialise the daily load calendar over the whole known history."""
    today = today or dt.datetime.now(tz=dt.UTC).date()
    report = RecomputeReport()

    with session_scope(engine) as session:
        first_day = _earliest_evidence(session)
        if first_day is None:
            log.info("recompute found no data")
            return report
        last_day = max(first_day, today)
        totals = _session_totals(session, first_day, last_day)

    report.first_day = first_day
    report.last_day = last_day
    report.days = (last_day - first_day).days + 1
    report.days_with_session = len(totals)

    day = first_day
    while day <= last_day:
        chunk_end = min(day + dt.timedelta(days=_BATCH_DAYS - 1), last_day)
        with session_scope(engine) as session:
            _write_chunk(session, day, chunk_end, totals)
        day = chunk_end + dt.timedelta(days=1)

    log.info("recompute finished", extra={"days": report.days})
    return report


def _write_chunk(
    session: Session,
    first_day: dt.date,
    last_day: dt.date,
    totals: dict[dt.date, tuple[int, float]],
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

        if day in totals:
            duration_s, distance_m = totals[day]
            row.duration_s = duration_s
            row.distance_m = distance_m
            # A session happened; what it cost is a phase 3 question.
            row.trimp = None
            row.hr_tss = None
            row.r_tss = None
        else:
            # No session: the load really is zero, and saying so is what
            # makes the fitness curve decay instead of stalling.
            row.duration_s = 0
            row.distance_m = 0.0
            row.trimp = 0.0
            row.hr_tss = 0.0
            row.r_tss = 0.0
        day += dt.timedelta(days=1)
