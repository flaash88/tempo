"""The read model: assembled metrics with their evidence.

Where ``recompute`` writes derived tables, this reads them back and hands
over the answers in the shape CLAUDE.md prescribes — a value or ``None``,
with confidence, history, progress and the date of the last data point.
The phase 4 endpoints are thin wrappers over this; nothing here formats
text or decides what any number means.

The database access lives here rather than in ``tempo.metrics`` so the
engine stays pure and strictly typed.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass
from typing import Final

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tempo.db.models import (
    Activity,
    ActivityStream,
    AthleteSettings,
    FitnessDay,
    WellnessDay,
)
from tempo.db.session import session_scope
from tempo.ingest.sports import RUN
from tempo.metrics.confidence import (
    MetricResult,
    evaluate,
    newest_of,
    tracked_window,
    unavailable,
)
from tempo.metrics.performance import (
    BestEffort,
    CriticalSpeed,
    best_efforts,
    critical_speed,
)
from tempo.metrics.thresholds import (
    CRITICAL_SPEED_MAX_DURATION_S,
    CRITICAL_SPEED_MIN_DURATION_S,
    DEFAULT_READINESS_WEIGHTS,
    DEFAULT_SLEEP_TARGET_S,
    MIN_DAYS_FORM,
    MIN_PERFORMANCES_CRITICAL_SPEED,
    ReadinessWeights,
)
from tempo.metrics.wellness import (
    Baseline,
    ReadinessComponents,
    Reading,
    hrv_baseline,
    readiness,
    readiness_baselines,
    readiness_components,
    resting_hr_baseline,
)

log = logging.getLogger(__name__)

_SECONDS_PER_KM: Final = 1000.0


@dataclass(frozen=True, slots=True)
class Form:
    """Fitness, fatigue and the balance between them."""

    ctl: float
    atl: float
    tsb: float


@dataclass(frozen=True, slots=True)
class Snapshot:
    """Everything the "today" view needs, each part with its own evidence."""

    as_of: dt.date
    readiness: MetricResult[int]
    readiness_components: ReadinessComponents
    hrv: MetricResult[Baseline]
    resting_hr: MetricResult[Baseline]
    form: MetricResult[Form]
    acwr: MetricResult[float]
    critical_speed: MetricResult[CriticalSpeed]
    # Which source field the HRV readings came from, carried through rather
    # than interpreted. None when there are no readings.
    hrv_source_field: str | None = None
    # What was in force while this was assembled, so a caller can report the
    # answer and the settings behind it together.
    readiness_weights: ReadinessWeights = DEFAULT_READINESS_WEIGHTS
    sleep_target_s: int = DEFAULT_SLEEP_TARGET_S

    @property
    def any_value(self) -> bool:
        return any(
            result.is_available
            for result in (
                self.readiness,
                self.hrv,
                self.resting_hr,
                self.form,
                self.acwr,
                self.critical_speed,
            )
        )


def _wellness_rows(session: Session) -> list[WellnessDay]:
    return list(session.scalars(select(WellnessDay).order_by(WellnessDay.date)))


def _activity_days(session: Session) -> list[dt.date]:
    return [
        start.date()
        for start in session.scalars(
            select(Activity.start_local).order_by(Activity.start_local)
        )
    ]


def _hrv_readings(rows: list[WellnessDay]) -> list[Reading]:
    return [
        Reading(
            date=row.date,
            value=row.hrv,
            source_field=row.hrv_source_field,
        )
        for row in rows
        if row.hrv is not None
    ]


def _resting_hr_readings(rows: list[WellnessDay]) -> list[Reading]:
    return [
        Reading(date=row.date, value=float(row.resting_hr))
        for row in rows
        if row.resting_hr is not None
    ]


def _form_of(
    row: FitnessDay | None,
    *,
    as_of: dt.date,
    have: int,
    last_data_point: dt.date | None,
) -> MetricResult[Form]:
    value: Form | None = None
    if row is not None and row.ctl is not None and row.atl is not None:
        value = Form(ctl=row.ctl, atl=row.atl, tsb=row.tsb or 0.0)
    return evaluate(
        value,
        have=have,
        required=MIN_DAYS_FORM,
        as_of=as_of,
        last_data_point=last_data_point,
    )


def _acwr_of(
    row: FitnessDay | None,
    *,
    as_of: dt.date,
    have: int,
    last_data_point: dt.date | None,
) -> MetricResult[float]:
    return evaluate(
        row.acwr if row is not None else None,
        have=have,
        required=MIN_DAYS_FORM,
        as_of=as_of,
        last_data_point=last_data_point,
    )


def _best_efforts_across_activities(
    session: Session,
) -> tuple[list[BestEffort], dt.date | None]:
    """The best running effort per duration, over the recorded sessions.

    Runs only. A critical *running* speed fitted to a bike ride is not a
    slightly wrong number, it is a different quantity — and on this
    athlete's history the rides are the fastest thing on file, so without
    the filter they would supply every best effort.

    Reads one stream at a time; with a single athlete's history that is a
    handful of queries, and it keeps the whole series out of memory at once.
    """
    best: dict[int, BestEffort] = {}
    newest: dt.date | None = None
    activities = session.execute(
        select(Activity.id, Activity.start_local)
        .where(Activity.sport == RUN)
        .order_by(Activity.start_local)
    ).all()
    for activity_id, start_local in activities:
        speeds = list(
            session.scalars(
                select(ActivityStream.speed_m_s)
                .where(ActivityStream.activity_id == activity_id)
                .order_by(ActivityStream.offset_s)
            )
        )
        if not speeds:
            continue
        for duration, effort in best_efforts(speeds).items():
            current = best.get(duration)
            if current is None or effort.distance_m > current.distance_m:
                best[duration] = effort
                day = start_local.date()
                newest = day if newest is None else max(newest, day)
    return list(best.values()), newest


def build_snapshot(
    engine: Engine,
    *,
    as_of: dt.date | None = None,
    weights: ReadinessWeights = DEFAULT_READINESS_WEIGHTS,
) -> Snapshot:
    """Assemble the current state of every headline metric.

    ``weights`` comes from the configuration; the sleep target comes from
    the athlete's own settings, falling back to the documented default.
    """
    as_of = as_of or dt.datetime.now(tz=dt.UTC).date()

    with session_scope(engine) as session:
        wellness = _wellness_rows(session)
        activity_days = _activity_days(session)
        fitness_row = session.get(FitnessDay, as_of)
        efforts, efforts_newest = _best_efforts_across_activities(session)
        athlete = session.get(AthleteSettings, 1)
        sleep_target_s = (
            athlete.sleep_target_s
            if athlete is not None and athlete.sleep_target_s
            else DEFAULT_SLEEP_TARGET_S
        )

    wellness_days = [row.date for row in wellness]
    nights = tracked_window(wellness_days, [], as_of=as_of)
    tracked = tracked_window(wellness_days, activity_days, as_of=as_of)
    newest_tracked = newest_of([*wellness_days, *activity_days])

    hrv_readings = _hrv_readings(wellness)
    resting_readings = _resting_hr_readings(wellness)
    hrv_result = hrv_baseline(hrv_readings, as_of=as_of)
    resting_result = resting_hr_baseline(resting_readings, as_of=as_of)
    # Readiness compares today against a baseline over its own, shorter
    # window; the published baselines above keep their longer minimum.
    hrv_reference, resting_reference = readiness_baselines(
        hrv_readings, resting_readings, as_of=as_of
    )

    have_form = len(tracked)
    form = _form_of(
        fitness_row, as_of=as_of, have=have_form, last_data_point=newest_tracked
    )
    acwr_result = _acwr_of(
        fitness_row, as_of=as_of, have=have_form, last_data_point=newest_tracked
    )

    latest = wellness[-1] if wellness else None
    inside_window = latest is not None and bool(nights) and latest.date in nights
    components = readiness_components(
        hrv_deviation_sd=(
            hrv_reference.deviation_sd(latest.hrv)
            if hrv_reference is not None and inside_window and latest is not None
            else None
        ),
        resting_hr_deviation_sd=(
            resting_reference.deviation_sd(
                None if latest.resting_hr is None else float(latest.resting_hr)
            )
            if resting_reference is not None and inside_window and latest is not None
            else None
        ),
        sleep_secs=latest.sleep_secs if inside_window and latest else None,
        tsb=form.value.tsb if form.value is not None else None,
        sleep_target_s=sleep_target_s,
    )
    readiness_result = readiness(
        nights=nights,
        components=components,
        as_of=as_of,
        newest_data_point=newest_of(wellness_days),
        weights=weights,
    )

    cs_value = critical_speed(efforts)
    qualifying = _qualifying_effort_count(efforts)
    cs_result: MetricResult[CriticalSpeed]
    if qualifying:
        cs_result = evaluate(
            cs_value,
            have=qualifying,
            required=MIN_PERFORMANCES_CRITICAL_SPEED,
            as_of=as_of,
            last_data_point=efforts_newest,
        )
    else:
        cs_result = unavailable(
            CriticalSpeed,
            have=0,
            required=MIN_PERFORMANCES_CRITICAL_SPEED,
            as_of=as_of,
            last_data_point=efforts_newest,
        )

    snapshot = Snapshot(
        as_of=as_of,
        readiness=readiness_result,
        readiness_components=components,
        hrv=hrv_result,
        resting_hr=resting_result,
        form=form,
        acwr=acwr_result,
        critical_speed=cs_result,
        hrv_source_field=(
            hrv_result.value.source_field
            if hrv_result.value is not None
            else (hrv_readings[-1].source_field if hrv_readings else None)
        ),
        readiness_weights=weights,
        sleep_target_s=sleep_target_s,
    )
    log.debug(
        "snapshot built",
        extra={
            "as_of": as_of.isoformat(),
            "readiness": snapshot.readiness.value,
            "form_available": snapshot.form.is_available,
        },
    )
    return snapshot


def _qualifying_effort_count(efforts: list[BestEffort]) -> int:
    """How many efforts are eligible for the critical speed regression."""
    return len(
        {
            effort.duration_s
            for effort in efforts
            if CRITICAL_SPEED_MIN_DURATION_S
            <= effort.duration_s
            <= CRITICAL_SPEED_MAX_DURATION_S
        }
    )


def pace_of(speed_m_s: float) -> float | None:
    """Pace in seconds per kilometre, or None when standing still."""
    if speed_m_s <= 0:
        return None
    return _SECONDS_PER_KM / speed_m_s
