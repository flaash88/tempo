"""Read models for the activity, trends, performance and plan screens.

Same division as ``snapshot``: the database is read here, the arithmetic
happens in ``tempo.metrics``, and the route modules only serialise. Nothing
in here formats text or decides what a number means.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from sqlalchemy import Engine, Select, func, select
from sqlalchemy.orm import Session

from tempo.db.models import (
    Activity,
    ActivityStream,
    AiCall,
    AthleteSettings,
    DailyLoad,
    FitnessDay,
    Lap,
    PlannedWorkout,
    WellnessDay,
)
from tempo.db.session import session_scope
from tempo.ingest.sports import RUN
from tempo.metrics import load as load_metrics
from tempo.metrics.confidence import (
    MetricResult,
    current_window,
    evaluate,
    from_window,
    newest_of,
    tracked_window,
    unavailable,
)
from tempo.metrics.fitness import monotony, strain
from tempo.metrics.performance import (
    BestEffort,
    CriticalSpeed,
    best_efforts,
    critical_speed,
    race_predictions,
    vdot,
)
from tempo.metrics.thresholds import (
    CRITICAL_SPEED_MAX_DURATION_S,
    CRITICAL_SPEED_MIN_DURATION_S,
    MIN_DAYS_FORM,
    MIN_DAYS_HRV_BASELINE,
    MIN_PERFORMANCES_CRITICAL_SPEED,
    MONOTONY_WINDOW_DAYS,
    PREDICTION_STALE_AFTER_DAYS,
)
from tempo.metrics.wellness import (
    Baseline,
    Reading,
    hrv_baseline,
    resting_hr_baseline,
)
from tempo.metrics.zones import ZoneSet, time_in_zones, zones_for_athlete
from tempo.snapshot import pace_of

log = logging.getLogger(__name__)

WINDOW_WEEKS: Final[dict[str, int]] = {"6w": 6, "12w": 12, "52w": 52}

# Race distances the trends and performance screens predict.
RACE_LABELS: Final[dict[str, float]] = {
    "5k": 5_000.0,
    "10k": 10_000.0,
    "half_marathon": 21_097.5,
    "marathon": 42_195.0,
}


# --- activity detail ---------------------------------------------------


@dataclass(frozen=True, slots=True)
class LapReport:
    index: int
    distance_m: float | None
    duration_s: int | None
    avg_hr: int | None
    avg_pace_s_per_km: float | None
    pace_delta_s_per_km: float | None = None


@dataclass
class ActivityReport:
    """One session with everything the detail screen shows."""

    activity: Activity
    has_stream: bool = False
    trimp: MetricResult[float] = field(default_factory=MetricResult[float])
    hr_tss: MetricResult[float] = field(default_factory=MetricResult[float])
    r_tss: MetricResult[float] = field(default_factory=MetricResult[float])
    gap_pace: MetricResult[float] = field(default_factory=MetricResult[float])
    efficiency_factor: MetricResult[float] = field(default_factory=MetricResult[float])
    decoupling: MetricResult[float] = field(default_factory=MetricResult[float])
    avg_cadence_spm: int | None = None
    zone_seconds: tuple[int, ...] = ()
    zone_bounds: ZoneSet | None = None
    seconds_below_zone_1: int = 0
    seconds_unknown: int = 0
    laps: tuple[LapReport, ...] = ()
    planned: PlannedWorkout | None = None


def _stream_channels(
    session: Session, activity_id: str
) -> tuple[
    list[int],
    list[float | None],
    list[float | None],
    list[float | None],
    list[float | None],
]:
    rows = session.execute(
        select(
            ActivityStream.offset_s,
            ActivityStream.speed_m_s,
            ActivityStream.altitude_m,
            ActivityStream.hr,
            ActivityStream.cadence,
        )
        .where(ActivityStream.activity_id == activity_id)
        .order_by(ActivityStream.offset_s)
    ).all()
    offsets: list[int] = []
    speeds: list[float | None] = []
    altitudes: list[float | None] = []
    rates: list[float | None] = []
    cadences: list[float | None] = []
    for offset, speed, altitude, rate, cadence in rows:
        offsets.append(offset)
        speeds.append(speed)
        altitudes.append(altitude)
        rates.append(None if rate is None else float(rate))
        cadences.append(None if cadence is None else float(cadence))
    return offsets, speeds, altitudes, rates, cadences


def _threshold_speed(settings: AthleteSettings | None) -> float | None:
    if settings is None or not settings.threshold_pace_s_per_km:
        return None
    if settings.threshold_pace_s_per_km <= 0:
        return None
    return 1000.0 / settings.threshold_pace_s_per_km


def _one(value: float | None, *, day: dt.date, as_of: dt.date) -> MetricResult[float]:
    """A per-session metric needs one session, and that is its whole window.

    Staleness still applies, because a value from a run in January is not a
    statement about today — the tile shows the date, as it does everywhere.
    """
    return evaluate(value, have=1, required=1, as_of=as_of, last_data_point=day)


def _planned_for(
    session: Session, day: dt.date, sport: str | None
) -> PlannedWorkout | None:
    """The calendar entry a session can be compared against."""
    statement: Select[tuple[PlannedWorkout]] = select(PlannedWorkout).where(
        PlannedWorkout.date == day
    )
    if sport is not None:
        statement = statement.where(PlannedWorkout.sport == sport)
    return session.scalars(statement.limit(1)).first()


def build_activity_report(
    engine: Engine, activity_id: str, *, as_of: dt.date | None = None
) -> ActivityReport | None:
    """Everything the detail screen shows for one session."""
    as_of = as_of or dt.datetime.now(tz=dt.UTC).date()

    with session_scope(engine) as session:
        activity = session.get(Activity, activity_id)
        if activity is None:
            return None
        settings = session.get(AthleteSettings, 1)
        offsets, speeds, altitudes, rates, cadences = _stream_channels(
            session, activity_id
        )
        laps = list(
            session.scalars(
                select(Lap).where(Lap.activity_id == activity_id).order_by(Lap.index)
            )
        )
        planned = _planned_for(session, activity.start_local.date(), activity.sport)
        session.expunge(activity)
        for lap in laps:
            session.expunge(lap)
        if planned is not None:
            session.expunge(planned)

    day = activity.start_local.date()
    report = ActivityReport(activity=activity, has_stream=bool(offsets))

    hr_rest = settings.hr_rest if settings else None
    hr_max = settings.hr_max if settings else None
    lthr = settings.lthr if settings else None

    trimp = load_metrics.trimp_banister(
        activity.moving_s,
        None if activity.avg_hr is None else float(activity.avg_hr),
        hr_rest,
        hr_max,
    )
    report.trimp = _one(trimp, day=day, as_of=as_of)
    report.hr_tss = _one(
        load_metrics.hr_tss(
            trimp, load_metrics.trimp_at_threshold_hour(lthr, hr_rest, hr_max)
        ),
        day=day,
        as_of=as_of,
    )

    ngp: float | None = None
    if speeds:
        ngp = load_metrics.normalised_graded_speed(speeds, altitudes)
        report.gap_pace = _one(
            None if ngp is None else pace_of(ngp), day=day, as_of=as_of
        )
        report.efficiency_factor = _one(
            load_metrics.efficiency_factor(ngp, load_metrics.mean_of(rates)),
            day=day,
            as_of=as_of,
        )
        report.decoupling = _one(
            load_metrics.decoupling(speeds, altitudes, rates), day=day, as_of=as_of
        )
        mean_cadence = load_metrics.mean_of(cadences)
        report.avg_cadence_spm = None if mean_cadence is None else round(mean_cadence)

    threshold_speed = _threshold_speed(settings)
    report.r_tss = _one(
        load_metrics.r_tss(
            activity.moving_s,
            load_metrics.intensity_factor(ngp, threshold_speed),
        ),
        day=day,
        as_of=as_of,
    )

    zones = zones_for_athlete(
        lthr=lthr,
        hr_max=hr_max,
        zone_model=settings.zone_model if settings else None,
    )
    if zones is not None and rates:
        times = time_in_zones(rates, zones)
        report.zone_seconds = times.seconds
        report.zone_bounds = zones
        report.seconds_below_zone_1 = times.below
        report.seconds_unknown = times.unknown

    planned_pace: float | None = None
    if planned and planned.target_time_s and planned.target_dist_m:
        planned_pace = planned.target_time_s / (planned.target_dist_m / 1000.0)

    report.laps = tuple(
        LapReport(
            index=lap.index,
            distance_m=lap.distance_m,
            duration_s=lap.duration_s,
            avg_hr=lap.avg_hr,
            avg_pace_s_per_km=lap.avg_pace_s_per_km,
            pace_delta_s_per_km=(
                None
                if planned_pace is None or lap.avg_pace_s_per_km is None
                else lap.avg_pace_s_per_km - planned_pace
            ),
        )
        for lap in laps
    )
    report.planned = planned
    return report


# --- activity list -----------------------------------------------------


@dataclass(frozen=True, slots=True)
class ActivityListRow:
    activity: Activity
    has_stream: bool


def list_activities(
    engine: Engine,
    *,
    limit: int = 50,
    offset: int = 0,
    sport: str | None = None,
) -> tuple[list[ActivityListRow], int]:
    with session_scope(engine) as session:
        statement: Select[tuple[Activity]] = select(Activity)
        counter = select(func.count()).select_from(Activity)
        if sport is not None:
            statement = statement.where(Activity.sport == sport)
            counter = counter.where(Activity.sport == sport)
        total = session.scalar(counter) or 0
        activities = list(
            session.scalars(
                statement.order_by(Activity.start_local.desc())
                .limit(limit)
                .offset(offset)
            )
        )
        with_stream = set(
            session.scalars(
                select(ActivityStream.activity_id)
                .where(
                    ActivityStream.activity_id.in_(
                        [activity.id for activity in activities]
                    )
                )
                .distinct()
            )
        )
        for activity in activities:
            session.expunge(activity)
    return (
        [
            ActivityListRow(activity=activity, has_stream=activity.id in with_stream)
            for activity in activities
        ],
        total,
    )


# --- streams -----------------------------------------------------------

STREAM_FIELDS: Final[tuple[str, ...]] = (
    "hr",
    "speed_m_s",
    "altitude_m",
    "cadence",
    "power",
    "lat",
    "lon",
)


@dataclass(frozen=True, slots=True)
class StreamReport:
    activity_id: str
    resolution_s: int
    offset_s: tuple[int, ...]
    series: dict[str, list[float | None]]


def build_stream_report(
    engine: Engine,
    activity_id: str,
    *,
    fields: Sequence[str] | None = None,
    resolution_s: int = 1,
) -> StreamReport | None:
    """Stream channels, decimated rather than smoothed.

    Downsampling takes every nth recorded second and leaves the rest out.
    Averaging a bucket would invent a value for a second that was never
    measured, and would quietly fill a gap that the parser went out of its
    way to preserve — so a decimated sample is always a real reading or a
    real ``null``.
    """
    if resolution_s < 1:
        raise ValueError("resolution must be at least one second")
    wanted = tuple(fields) if fields else STREAM_FIELDS
    unknown = [name for name in wanted if name not in STREAM_FIELDS]
    if unknown:
        raise ValueError(f"unknown stream fields: {', '.join(sorted(unknown))}")

    columns = [getattr(ActivityStream, name) for name in wanted]
    with session_scope(engine) as session:
        if session.get(Activity, activity_id) is None:
            return None
        rows = session.execute(
            select(ActivityStream.offset_s, *columns)
            .where(ActivityStream.activity_id == activity_id)
            .order_by(ActivityStream.offset_s)
        ).all()

    offsets: list[int] = []
    series: dict[str, list[float | None]] = {name: [] for name in wanted}
    for row in rows:
        offset = int(row[0])
        if offset % resolution_s:
            continue
        offsets.append(offset)
        for index, name in enumerate(wanted, start=1):
            raw = row[index]
            series[name].append(None if raw is None else float(raw))

    return StreamReport(
        activity_id=activity_id,
        resolution_s=resolution_s,
        offset_s=tuple(offsets),
        series=series,
    )


# --- trends ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FitnessPointReport:
    date: dt.date
    load: float | None
    ctl: float | None
    atl: float | None
    tsb: float | None
    confidence: float
    days_of_history: int


@dataclass(frozen=True, slots=True)
class WeekReport:
    week_start: dt.date
    distance_m: float
    duration_s: int
    load: float | None
    zone_seconds: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class BaselinePointReport:
    date: dt.date
    value: float
    mean: float | None
    lower: float | None
    upper: float | None


@dataclass
class TrendsReport:
    window: str
    from_date: dt.date
    to_date: dt.date
    fitness: tuple[FitnessPointReport, ...] = ()
    weeks: tuple[WeekReport, ...] = ()
    hrv: MetricResult[Baseline] = field(default_factory=MetricResult[Baseline])
    hrv_series: tuple[BaselinePointReport, ...] = ()
    hrv_source_field: str | None = None
    resting_hr: MetricResult[Baseline] = field(default_factory=MetricResult[Baseline])
    resting_hr_series: tuple[BaselinePointReport, ...] = ()
    vo2max: MetricResult[float] = field(default_factory=MetricResult[float])
    vdot: MetricResult[float] = field(default_factory=MetricResult[float])
    monotony: MetricResult[float] = field(default_factory=MetricResult[float])
    strain: MetricResult[float] = field(default_factory=MetricResult[float])
    average_week_distance_m: float | None = None


def _monday(day: dt.date) -> dt.date:
    return day - dt.timedelta(days=day.weekday())


def _baseline_series(
    readings: Sequence[Reading],
    baseline: Baseline | None,
    from_date: dt.date,
) -> tuple[BaselinePointReport, ...]:
    """The readings in the window, with the band the baseline draws.

    The band is the current one repeated, not recomputed per day: the tile
    draws today's band across the series, and pretending each past day had
    its own would be a claim about what was known then.
    """
    points: list[BaselinePointReport] = []
    for reading in readings:
        if reading.date < from_date:
            continue
        points.append(
            BaselinePointReport(
                date=reading.date,
                value=reading.value,
                mean=None if baseline is None else baseline.mean,
                lower=None if baseline is None else baseline.lower,
                upper=None if baseline is None else baseline.upper,
            )
        )
    return tuple(points)


def build_trends_report(
    engine: Engine, *, window: str = "12w", as_of: dt.date | None = None
) -> TrendsReport:
    """The series behind the Trends screen."""
    if window not in WINDOW_WEEKS:
        raise ValueError(f"unknown window {window!r}")
    as_of = as_of or dt.datetime.now(tz=dt.UTC).date()
    from_date = _monday(as_of) - dt.timedelta(weeks=WINDOW_WEEKS[window] - 1)

    with session_scope(engine) as session:
        settings = session.get(AthleteSettings, 1)
        fitness_rows = list(
            session.scalars(
                select(FitnessDay)
                .where(FitnessDay.date >= from_date, FitnessDay.date <= as_of)
                .order_by(FitnessDay.date)
            )
        )
        load_rows = list(
            session.scalars(
                select(DailyLoad)
                .where(DailyLoad.date >= from_date, DailyLoad.date <= as_of)
                .order_by(DailyLoad.date)
            )
        )
        wellness = list(session.scalars(select(WellnessDay).order_by(WellnessDay.date)))
        activities = session.execute(
            select(Activity.id, Activity.start_local)
            .where(
                Activity.start_local >= dt.datetime.combine(from_date, dt.time.min),
                Activity.start_local <= dt.datetime.combine(as_of, dt.time.max),
            )
            .order_by(Activity.start_local)
        ).all()
        zones = zones_for_athlete(
            lthr=settings.lthr if settings else None,
            hr_max=settings.hr_max if settings else None,
            zone_model=settings.zone_model if settings else None,
        )
        zone_by_week: dict[dt.date, list[int]] = {}
        if zones is not None:
            for activity_id, start_local in activities:
                rates = [
                    None if value is None else float(value)
                    for value in session.scalars(
                        select(ActivityStream.hr)
                        .where(ActivityStream.activity_id == activity_id)
                        .order_by(ActivityStream.offset_s)
                    )
                ]
                if not rates:
                    continue
                times = time_in_zones(rates, zones)
                zone_totals = zone_by_week.setdefault(
                    _monday(start_local.date()), [0, 0, 0, 0, 0]
                )
                for index, seconds in enumerate(times.seconds):
                    zone_totals[index] += seconds
        newest_vo2max = session.scalars(
            select(WellnessDay)
            .where(WellnessDay.vo2max.is_not(None))
            .order_by(WellnessDay.date.desc())
            .limit(1)
        ).first()
        vo2max_value = newest_vo2max.vo2max if newest_vo2max else None
        vo2max_date = newest_vo2max.date if newest_vo2max else None

    report = TrendsReport(window=window, from_date=from_date, to_date=as_of)

    load_by_date = {entry.date: _primary_load(entry) for entry in load_rows}
    report.fitness = tuple(
        FitnessPointReport(
            date=row.date,
            load=load_by_date.get(row.date),
            ctl=row.ctl,
            atl=row.atl,
            tsb=row.tsb,
            confidence=row.confidence,
            days_of_history=row.days_of_history,
        )
        for row in fitness_rows
    )

    weekly: dict[dt.date, list[float]] = {}
    for entry in load_rows:
        week_totals = weekly.setdefault(_monday(entry.date), [0.0, 0.0, 0.0])
        week_totals[0] += entry.distance_m
        week_totals[1] += entry.duration_s
        primary = _primary_load(entry)
        if primary is not None:
            week_totals[2] += primary
    report.weeks = tuple(
        WeekReport(
            week_start=week_start,
            distance_m=totals[0],
            duration_s=int(totals[1]),
            load=totals[2] or None,
            zone_seconds=tuple(zone_by_week.get(week_start, [0, 0, 0, 0, 0])),
        )
        for week_start, totals in sorted(weekly.items())
    )
    distances = [week.distance_m for week in report.weeks if week.distance_m > 0]
    report.average_week_distance_m = (
        sum(distances) / len(distances) if distances else None
    )

    hrv_readings = [
        Reading(date=row.date, value=row.hrv, source_field=row.hrv_source_field)
        for row in wellness
        if row.hrv is not None
    ]
    resting_readings = [
        Reading(date=row.date, value=float(row.resting_hr))
        for row in wellness
        if row.resting_hr is not None
    ]
    report.hrv = hrv_baseline(hrv_readings, as_of=as_of)
    report.resting_hr = resting_hr_baseline(resting_readings, as_of=as_of)
    report.hrv_series = _baseline_series(hrv_readings, report.hrv.value, from_date)
    report.resting_hr_series = _baseline_series(
        resting_readings, report.resting_hr.value, from_date
    )
    report.hrv_source_field = (
        report.hrv.value.source_field
        if report.hrv.value is not None
        else (hrv_readings[-1].source_field if hrv_readings else None)
    )

    report.vo2max = evaluate(
        vo2max_value,
        have=1 if vo2max_value is not None else 0,
        required=1,
        as_of=as_of,
        last_data_point=vo2max_date,
    )

    tracked = tracked_window(
        [row.date for row in wellness],
        [start.date() for _id, start in activities],
        as_of=as_of,
    )
    trailing = [_primary_load(entry) or 0.0 for entry in load_rows]
    week = trailing[-MONOTONY_WINDOW_DAYS:]
    enough_week = len(week) == MONOTONY_WINDOW_DAYS and len(tracked) >= MIN_DAYS_FORM
    report.monotony = from_window(
        monotony(week) if enough_week else None,
        window=tracked,
        required=MIN_DAYS_FORM,
        as_of=as_of,
        newest_data_point=newest_of(
            [row.date for row in wellness] + [start.date() for _id, start in activities]
        ),
    )
    report.strain = from_window(
        strain(week) if enough_week else None,
        window=tracked,
        required=MIN_DAYS_FORM,
        as_of=as_of,
        newest_data_point=report.monotony.last_data_point,
    )

    performance = build_performance_report(engine, as_of=as_of)
    report.vdot = performance.vdot
    return report


def _primary_load(entry: DailyLoad) -> float | None:
    """The load the fitness curve uses, mirroring the recompute pipeline."""
    if entry.r_tss is not None:
        return entry.r_tss
    return entry.hr_tss


# --- performance -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class EffortReport:
    duration_s: int
    distance_m: float
    activity_id: str | None
    date: dt.date | None

    @property
    def speed_m_s(self) -> float:
        return self.distance_m / self.duration_s


@dataclass
class PerformanceReport:
    best_efforts: tuple[EffortReport, ...] = ()
    critical_speed: MetricResult[CriticalSpeed] = field(
        default_factory=MetricResult[CriticalSpeed]
    )
    vdot: MetricResult[float] = field(default_factory=MetricResult[float])
    predictions: dict[str, list[tuple[str, float]]] = field(default_factory=dict)
    reference: EffortReport | None = None
    predictions_stale: bool = False


def build_performance_report(
    engine: Engine, *, as_of: dt.date | None = None
) -> PerformanceReport:
    """Best efforts, critical speed, VDOT and both sets of predictions.

    Runs only, as in ``snapshot``: a critical running speed or a race
    prediction taken from a bike ride is a different quantity.
    """
    as_of = as_of or dt.datetime.now(tz=dt.UTC).date()
    report = PerformanceReport()

    best: dict[int, EffortReport] = {}
    with session_scope(engine) as session:
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
                    best[duration] = EffortReport(
                        duration_s=duration,
                        distance_m=effort.distance_m,
                        activity_id=activity_id,
                        date=start_local.date(),
                    )

    report.best_efforts = tuple(best[duration] for duration in sorted(best))

    qualifying = [
        effort
        for effort in report.best_efforts
        if CRITICAL_SPEED_MIN_DURATION_S
        <= effort.duration_s
        <= CRITICAL_SPEED_MAX_DURATION_S
    ]
    newest_effort = newest_of(
        effort.date for effort in report.best_efforts if effort.date is not None
    )
    fitted = critical_speed(
        [
            BestEffort(duration_s=effort.duration_s, distance_m=effort.distance_m)
            for effort in qualifying
        ]
    )
    report.critical_speed = evaluate(
        fitted,
        have=len(qualifying),
        required=MIN_PERFORMANCES_CRITICAL_SPEED,
        as_of=as_of,
        last_data_point=newest_effort,
    )

    # The longest best effort is the most informative single performance to
    # extrapolate from: the further out the anchor, the less the prediction
    # depends on the model's behaviour at short durations.
    reference = report.best_efforts[-1] if report.best_efforts else None
    report.reference = reference
    if reference is None:
        report.vdot = unavailable(
            float, have=0, required=1, as_of=as_of, last_data_point=None
        )
        return report

    report.vdot = evaluate(
        vdot(reference.distance_m, float(reference.duration_s)),
        have=1,
        required=1,
        as_of=as_of,
        last_data_point=reference.date,
    )
    predictions = race_predictions(
        reference.distance_m, float(reference.duration_s), RACE_LABELS
    )
    report.predictions = {
        name: [(entry.method, entry.seconds) for entry in entries]
        for name, entries in predictions.items()
    }
    report.predictions_stale = (
        reference.date is not None
        and (as_of - reference.date).days > PREDICTION_STALE_AFTER_DAYS
    )
    return report


# --- plan --------------------------------------------------------------


@dataclass
class PlanReport:
    from_date: dt.date
    to_date: dt.date
    workouts: tuple[PlannedWorkout, ...] = ()
    races: tuple[PlannedWorkout, ...] = ()
    done_ids: frozenset[str] = frozenset()
    planned_distance_m: float = 0.0
    planned_duration_s: int = 0
    planned_load: float | None = None


# Calendar categories that are a target race rather than a session.
RACE_CATEGORIES: Final[frozenset[str]] = frozenset({"RACE_A", "RACE_B", "RACE_C"})


def build_plan_report(
    engine: Engine, *, from_date: dt.date, to_date: dt.date
) -> PlanReport:
    """The calendar for a date range, split into sessions and target races."""
    if to_date < from_date:
        raise ValueError("to_date must not be before from_date")

    with session_scope(engine) as session:
        entries = list(
            session.scalars(
                select(PlannedWorkout)
                .where(
                    PlannedWorkout.date >= from_date,
                    PlannedWorkout.date <= to_date,
                )
                .order_by(PlannedWorkout.date)
            )
        )
        recorded = {
            (start.date(), sport)
            for start, sport in session.execute(
                select(Activity.start_local, Activity.sport).where(
                    Activity.start_local >= dt.datetime.combine(from_date, dt.time.min),
                    Activity.start_local <= dt.datetime.combine(to_date, dt.time.max),
                )
            ).all()
        }
        for entry in entries:
            session.expunge(entry)

    workouts = tuple(
        entry
        for entry in entries
        if (entry.category or "").upper() not in RACE_CATEGORIES
    )
    races = tuple(
        entry for entry in entries if (entry.category or "").upper() in RACE_CATEGORIES
    )
    done = frozenset(
        entry.id for entry in entries if (entry.date, entry.sport) in recorded
    )

    total_load = sum(
        entry.target_load for entry in workouts if entry.target_load is not None
    )
    return PlanReport(
        from_date=from_date,
        to_date=to_date,
        workouts=workouts,
        races=races,
        done_ids=done,
        planned_distance_m=sum(entry.target_dist_m or 0.0 for entry in workouts),
        planned_duration_s=sum(entry.target_time_s or 0 for entry in workouts),
        planned_load=total_load or None,
    )


# --- AI usage ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class UsageReport:
    month: str
    input_tokens: int
    output_tokens: int
    cost_eur: float
    calls: int


def build_usage_report(engine: Engine, *, as_of: dt.date | None = None) -> UsageReport:
    """Token spend for the calendar month, for the budget tile."""
    as_of = as_of or dt.datetime.now(tz=dt.UTC).date()
    first = as_of.replace(day=1)
    start = dt.datetime.combine(first, dt.time.min, tzinfo=dt.UTC)

    with session_scope(engine) as session:
        row = session.execute(
            select(
                func.coalesce(func.sum(AiCall.input_tokens), 0),
                func.coalesce(func.sum(AiCall.output_tokens), 0),
                func.coalesce(func.sum(AiCall.cost_eur), 0.0),
                func.count(),
            ).where(AiCall.ts >= start)
        ).one()

    return UsageReport(
        month=first.strftime("%Y-%m"),
        input_tokens=int(row[0]),
        output_tokens=int(row[1]),
        cost_eur=float(row[2]),
        calls=int(row[3]),
    )


# --- today's extras ----------------------------------------------------


@dataclass(frozen=True, slots=True)
class TodayExtras:
    """Parts of the Heute screen that are not metric results."""

    sleep: MetricResult[int]
    hrv_latest: float | None
    resting_hr_latest: int | None
    planned: PlannedWorkout | None
    planned_done: bool
    plan_context: str | None


def build_today_extras(engine: Engine, *, as_of: dt.date | None = None) -> TodayExtras:
    as_of = as_of or dt.datetime.now(tz=dt.UTC).date()

    with session_scope(engine) as session:
        wellness = list(session.scalars(select(WellnessDay).order_by(WellnessDay.date)))
        planned = _planned_for(session, as_of, None)
        recorded = session.scalars(
            select(Activity.id).where(
                Activity.start_local >= dt.datetime.combine(as_of, dt.time.min),
                Activity.start_local <= dt.datetime.combine(as_of, dt.time.max),
            )
        ).first()
        if planned is not None:
            session.expunge(planned)

    nights = current_window((row.date for row in wellness), as_of=as_of)
    latest = wellness[-1] if wellness else None
    inside = latest is not None and bool(nights) and latest.date in nights
    sleep_days = [row.date for row in wellness if row.sleep_secs is not None]
    sleep_window = current_window(sleep_days, as_of=as_of)

    return TodayExtras(
        sleep=from_window(
            latest.sleep_secs if inside and latest else None,
            window=sleep_window,
            required=1,
            as_of=as_of,
            newest_data_point=newest_of(sleep_days),
        ),
        hrv_latest=latest.hrv if inside and latest else None,
        resting_hr_latest=latest.resting_hr if inside and latest else None,
        planned=planned,
        planned_done=recorded is not None,
        plan_context=(planned.name if planned and planned.category == "NOTE" else None),
    )


HRV_BASELINE_REQUIRED: Final = MIN_DAYS_HRV_BASELINE
