"""Writing ingested data into the database.

Kept apart from the clients so that the transaction boundaries are visible
in one place. Everything here is idempotent: importing the same activity
twice updates one row rather than creating a second.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Collection, Iterable, Sequence

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from tempo.db.models import (
    Activity,
    ActivityStream,
    DataSource,
    Lap,
    PlannedWorkout,
    SyncLog,
    SyncState,
    SyncStatus,
    WellnessDay,
    utcnow,
)
from tempo.ingest.fit_parser import ParsedActivity
from tempo.ingest.intervals_client import (
    ActivitySummary,
    PlannedWorkoutValues,
    WellnessValues,
)


def get_or_create_state(session: Session, source: str) -> SyncState:
    state = session.get(SyncState, source)
    if state is None:
        state = SyncState(source=source)
        session.add(state)
        session.flush()
    return state


def upsert_activity_summary(
    session: Session, summary: ActivitySummary, *, source: str = DataSource.INTERVALS
) -> bool:
    """Store or refresh one activity. Returns True when it was new.

    ``fit_path`` is deliberately left alone: it is owned by whatever put the
    file on disk, and a summary refresh must not forget where it lives.
    """
    activity = session.get(Activity, summary.id)
    created = activity is None
    if activity is None:
        activity = Activity(id=summary.id, imported_at=utcnow())
        session.add(activity)

    activity.start_local = summary.start_local
    activity.sport = summary.sport
    activity.distance_m = summary.distance_m
    activity.moving_s = summary.moving_s
    activity.elapsed_s = summary.elapsed_s
    activity.elevation_gain_m = summary.elevation_gain_m
    activity.avg_hr = summary.avg_hr
    activity.max_hr = summary.max_hr
    activity.avg_pace_s_per_km = summary.avg_pace_s_per_km
    activity.source = source
    return created


def upsert_activity_from_fit(
    session: Session,
    activity_id: str,
    parsed: ParsedActivity,
    *,
    fit_path: str | None,
    source: str = DataSource.FIT,
) -> bool:
    """Store an activity that exists only as a file."""
    activity = session.get(Activity, activity_id)
    created = activity is None
    if activity is None:
        activity = Activity(id=activity_id, imported_at=utcnow())
        session.add(activity)

    activity.start_local = parsed.start_local
    activity.sport = parsed.sport
    activity.distance_m = parsed.distance_m
    activity.moving_s = parsed.moving_s
    activity.elapsed_s = parsed.elapsed_s
    activity.elevation_gain_m = parsed.elevation_gain_m
    activity.avg_hr = parsed.avg_hr
    activity.max_hr = parsed.max_hr
    activity.avg_pace_s_per_km = parsed.avg_pace_s_per_km
    activity.source = source
    if fit_path is not None:
        activity.fit_path = fit_path
    return created


def set_fit_path(session: Session, activity_id: str, fit_path: str) -> None:
    activity = session.get(Activity, activity_id)
    if activity is not None:
        activity.fit_path = fit_path


def replace_streams_and_laps(
    session: Session, activity_id: str, parsed: ParsedActivity
) -> int:
    """Rewrite the stream and lap rows of one activity.

    Replacing rather than merging: the FIT file is the whole truth about a
    recording, so a re-parse must not leave rows from an earlier one behind.
    """
    session.execute(
        delete(ActivityStream).where(ActivityStream.activity_id == activity_id)
    )
    session.execute(delete(Lap).where(Lap.activity_id == activity_id))

    if parsed.samples:
        session.execute(
            insert(ActivityStream),
            [
                {
                    "activity_id": activity_id,
                    "offset_s": sample.offset_s,
                    "hr": sample.hr,
                    "speed_m_s": sample.speed_m_s,
                    "altitude_m": sample.altitude_m,
                    "cadence": sample.cadence,
                    "power": sample.power,
                    "lat": sample.lat,
                    "lon": sample.lon,
                }
                for sample in parsed.samples
            ],
        )
    if parsed.laps:
        session.execute(
            insert(Lap),
            [
                {
                    "activity_id": activity_id,
                    "index": lap.index,
                    "distance_m": lap.distance_m,
                    "duration_s": lap.duration_s,
                    "avg_hr": lap.avg_hr,
                    "avg_pace_s_per_km": lap.avg_pace_s_per_km,
                }
                for lap in parsed.laps
            ],
        )
    return len(parsed.samples)


def upsert_wellness(
    session: Session,
    values: Iterable[WellnessValues],
    *,
    source: str = DataSource.INTERVALS,
) -> int:
    """Store wellness days, skipping days that carry no reading.

    intervals.icu answers a date range with a row for every day, most of
    them empty. Writing those would turn "no measurement" into a record
    that looks like one.
    """
    stored = 0
    for entry in values:
        if entry.is_empty:
            continue
        day = session.get(WellnessDay, entry.date)
        if day is None:
            day = WellnessDay(date=entry.date)
            session.add(day)
        day.resting_hr = entry.resting_hr
        day.hrv = entry.hrv
        day.hrv_source_field = entry.hrv_source_field
        day.sleep_secs = entry.sleep_secs
        day.sleep_score = entry.sleep_score
        day.vo2max = entry.vo2max
        day.weight_kg = entry.weight_kg
        # Only overwrite the subjective fields when the source carries them;
        # an entry the athlete made by hand is not erased by a sync that has
        # nothing to say about how the day felt.
        if any(
            value is not None for value in (entry.fatigue, entry.soreness, entry.mood)
        ):
            day.fatigue = entry.fatigue
            day.soreness = entry.soreness
            day.mood = entry.mood
            day.subjective_source = source
        day.source = source
        stored += 1
    return stored


def upsert_planned_workouts(
    session: Session,
    entries: Sequence[PlannedWorkoutValues],
    *,
    source: str = DataSource.INTERVALS,
) -> int:
    """Store the source's calendar entries, idempotently.

    ``external_id`` is the idempotency key where there is one: the same
    session re-fetched, or one Tempo itself pushed and read back, updates
    the row it already has rather than adding a second. An entry created in
    the source's own interface carries no external id, and then the row's
    own id is the key.
    """
    stored = 0
    for entry in entries:
        existing: PlannedWorkout | None = None
        if entry.external_id:
            existing = session.scalars(
                select(PlannedWorkout)
                .where(PlannedWorkout.external_id == entry.external_id)
                .limit(1)
            ).first()
        if existing is None:
            existing = session.get(PlannedWorkout, entry.id)
        if existing is None:
            existing = PlannedWorkout(id=entry.id)
            session.add(existing)

        # A session Tempo pushed comes back through this same read as an
        # event of the source's. It keeps its own identity and its
        # write-back bookkeeping: the confirmation the athlete gave and
        # the transfer that already happened are facts about this row, not
        # about the calendar, and re-reading the calendar does not undo
        # them. What the read does add is the id the event got at the
        # source, which is what a later change is sent to.
        ours = existing.source == DataSource.TEMPO
        if ours:
            existing.remote_event_id = entry.id
        else:
            existing.id = entry.id
        existing.date = entry.date
        existing.category = entry.category
        existing.sport = entry.sport
        existing.name = entry.name
        existing.description = entry.description
        existing.target_time_s = entry.target_time_s
        existing.target_dist_m = entry.target_dist_m
        existing.target_load = entry.target_load
        existing.workout_doc = entry.workout_doc
        existing.external_id = entry.external_id
        if not ours:
            existing.source = source
        existing.updated_at = utcnow()
        stored += 1
    return stored


def drop_planned_workouts_absent_from(
    session: Session,
    *,
    oldest: dt.date,
    newest: dt.date,
    keep_ids: Collection[str],
    source: str = DataSource.INTERVALS,
) -> int:
    """Remove entries the source no longer has in the fetched window.

    A plan is a mirror of the source's calendar, so a session deleted there
    has to disappear here too — otherwise the plan screen would show a
    workout nobody intends to do. Scoped to the window that was actually
    fetched and to this source, so nothing outside it is touched.
    """
    doomed = session.scalars(
        select(PlannedWorkout)
        .where(PlannedWorkout.source == source)
        .where(PlannedWorkout.date >= oldest)
        .where(PlannedWorkout.date <= newest)
    ).all()
    removed = 0
    for entry in doomed:
        if entry.id not in keep_ids:
            session.delete(entry)
            removed += 1
    return removed


def upsert_garmin_wellness(
    session: Session,
    day: dt.date,
    *,
    body_battery: int | None,
    training_readiness: int | None,
) -> bool:
    """Add the two Garmin-only readings to a wellness day.

    Creates the row when the day has none yet, but only if there is
    something to put in it. ``source`` is set only on creation: a day
    intervals.icu already owns is not relabelled just because Garmin
    contributed one more field.
    """
    if body_battery is None and training_readiness is None:
        return False

    entry = session.get(WellnessDay, day)
    if entry is None:
        entry = WellnessDay(date=day, source=DataSource.GARMIN)
        session.add(entry)
    if body_battery is not None:
        entry.body_battery = body_battery
    if training_readiness is not None:
        entry.training_readiness = training_readiness
    return True


def find_activity_at(
    session: Session, start_local: dt.datetime, sport: str
) -> Activity | None:
    """An already stored activity with the same start and sport.

    Used to keep a file import from duplicating something intervals.icu has
    already delivered.
    """
    return session.scalars(
        select(Activity)
        .where(Activity.start_local == start_local)
        .where(Activity.sport == sport)
        .limit(1)
    ).first()


def open_sync_log(session: Session, source: str, started_at: dt.datetime) -> SyncLog:
    entry = SyncLog(source=source, started_at=started_at, status=SyncStatus.RUNNING)
    session.add(entry)
    session.flush()
    return entry


def close_sync_log(
    session: Session,
    log_id: int,
    *,
    status: str,
    finished_at: dt.datetime,
    detail: str | None,
) -> None:
    entry = session.get(SyncLog, log_id)
    if entry is None:
        return
    entry.status = status
    entry.finished_at = finished_at
    entry.detail = detail


def latest_activity_start(session: Session) -> dt.datetime | None:
    return session.scalars(
        select(Activity.start_local).order_by(Activity.start_local.desc()).limit(1)
    ).first()


def stored_activity_ids(session: Session, ids: Sequence[str]) -> set[str]:
    if not ids:
        return set()
    return set(session.scalars(select(Activity.id).where(Activity.id.in_(ids))).all())
