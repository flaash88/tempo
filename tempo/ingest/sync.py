"""Sync orchestration.

The watermark rules that shape this module:

* ``sync_state`` is the only thing a run reads to decide where to resume.
  ``sync_log`` is a pure audit trail and is never consulted for that.
* Activities and wellness carry separate marks, because either can fail on
  its own and a failure in one must not lose the other's progress.
* A mark moves forward only once its part has been processed **and
  committed**. Activities are processed oldest first, so an interruption
  leaves the mark at the last activity that came through whole, and the
  next run picks up from there.
* No source is asked more often than hourly. A free API and a personal
  account are not something to hammer.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from sqlalchemy import Engine

from tempo.config import Settings
from tempo.db.models import DataSource, SyncStatus
from tempo.db.session import session_scope
from tempo.ingest import store
from tempo.ingest.errors import (
    FitFileError,
    IngestError,
    IntervalsApiError,
    IntervalsAuthError,
    RateLimited,
)
from tempo.ingest.fit_parser import fit_activity_id, parse_fit_file
from tempo.ingest.intervals_client import IntervalsClient

log = logging.getLogger(__name__)

# Not more often than hourly, per the ingestion rules.
MIN_SYNC_INTERVAL: Final = dt.timedelta(hours=1)

# Re-ask this far behind the watermark. Wellness and activity edits arrive
# late; re-fetching a week is one request and every write is idempotent.
INCREMENTAL_OVERLAP_DAYS: Final = 7

# A full sync asks from before any consumer GPS watch existed.
FULL_SYNC_OLDEST: Final = dt.date(2000, 1, 1)

# How far ahead to read the calendar. A quarter covers a full training
# block without dragging in years of an empty calendar. Operational, not a
# metric threshold, so it lives here rather than in thresholds.py.
EVENT_HORIZON_DAYS: Final = 90

ClientFactory = Callable[[Settings], IntervalsClient]


@dataclass
class SyncReport:
    """What one run did. Rendered into ``sync_log.detail`` and the CLI."""

    source: str
    status: str
    activities_seen: int = 0
    activities_stored: int = 0
    fit_downloaded: int = 0
    stream_samples: int = 0
    activities_without_fit: int = 0
    wellness_days: int = 0
    planned_workouts: int = 0
    planned_workouts_removed: int = 0
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.status in {SyncStatus.OK, SyncStatus.PARTIAL}

    def summary(self) -> str:
        parts = [
            f"activities seen {self.activities_seen}",
            f"stored {self.activities_stored}",
            f"fit downloaded {self.fit_downloaded}",
            f"stream samples {self.stream_samples}",
            f"without fit {self.activities_without_fit}",
            f"wellness days {self.wellness_days}",
            f"planned workouts {self.planned_workouts}",
            f"removed {self.planned_workouts_removed}",
        ]
        if self.notes:
            parts.append(f"notes: {'; '.join(self.notes[:10])}")
        return ", ".join(parts)


def default_client_factory(settings: Settings) -> IntervalsClient:
    return IntervalsClient(
        settings.intervals_api_key.get_secret_value(),
        settings.intervals_athlete_id,
    )


def due_for_sync(last_success_at: dt.datetime | None, now: dt.datetime) -> bool:
    if last_success_at is None:
        return True
    if last_success_at.tzinfo is None:
        last_success_at = last_success_at.replace(tzinfo=dt.UTC)
    return now - last_success_at >= MIN_SYNC_INTERVAL


def _activity_window(
    watermark: dt.datetime | None, today: dt.date, *, full: bool
) -> tuple[dt.date, dt.date]:
    if full or watermark is None:
        return FULL_SYNC_OLDEST, today
    oldest = watermark.date() - dt.timedelta(days=INCREMENTAL_OVERLAP_DAYS)
    return min(oldest, today), today


def _wellness_window(
    watermark: dt.date | None, today: dt.date, *, full: bool
) -> tuple[dt.date, dt.date]:
    if full or watermark is None:
        return FULL_SYNC_OLDEST, today
    oldest = watermark - dt.timedelta(days=INCREMENTAL_OVERLAP_DAYS)
    return min(oldest, today), today


def _is_missing(error: IntervalsApiError) -> bool:
    return error.status_code == 404


def sync_intervals(
    engine: Engine,
    settings: Settings,
    *,
    full: bool = False,
    force: bool = False,
    now: dt.datetime | None = None,
    client_factory: ClientFactory | None = None,
) -> SyncReport:
    """Fetch activities, their FIT files and wellness from intervals.icu."""
    pinned_clock = now is not None
    now = now or dt.datetime.now(tz=dt.UTC)
    today = now.date()
    source = DataSource.INTERVALS
    report = SyncReport(source=source, status=SyncStatus.OK)

    with session_scope(engine) as session:
        state = store.get_or_create_state(session, source)
        activity_mark = state.last_activity_start
        wellness_mark = state.last_wellness_date
        last_success = state.last_success_at

    if not (full or force) and not due_for_sync(last_success, now):
        report.status = SyncStatus.OK
        report.notes.append(
            "skipped: synced less than an hour ago, use --force to override"
        )
        log.info("sync skipped", extra={"source": source})
        return report

    with session_scope(engine) as session:
        log_entry = store.open_sync_log(session, source, now)
        log_id = log_entry.id

    factory = client_factory or default_client_factory
    try:
        with factory(settings) as client:
            _sync_activities(
                engine, settings, client, report, activity_mark, today, full=full
            )
            _sync_wellness(engine, client, report, wellness_mark, today, full=full)
            _sync_events(engine, client, report, activity_mark, today, full=full)
    except (IntervalsAuthError, RateLimited) as exc:
        report.status = SyncStatus.FAILED
        report.notes.append(str(exc))
    except IngestError as exc:
        report.status = SyncStatus.FAILED
        report.notes.append(str(exc))

    if report.status == SyncStatus.OK and report.notes:
        report.status = SyncStatus.PARTIAL

    # A pinned clock keeps test timestamps deterministic; a real run wants
    # the actual finishing time in the audit log.
    finished = now if pinned_clock else dt.datetime.now(tz=dt.UTC)
    with session_scope(engine) as session:
        if report.ok:
            state = store.get_or_create_state(session, source)
            state.last_success_at = finished
        store.close_sync_log(
            session,
            log_id,
            status=report.status,
            finished_at=finished,
            detail=report.summary(),
        )
    log.info("sync finished", extra={"source": source, "status": report.status})
    return report


def _sync_activities(
    engine: Engine,
    settings: Settings,
    client: IntervalsClient,
    report: SyncReport,
    watermark: dt.datetime | None,
    today: dt.date,
    *,
    full: bool,
) -> None:
    oldest, newest = _activity_window(watermark, today, full=full)
    summaries, skipped = client.activities_from(client.list_activities(oldest, newest))
    report.activities_seen = len(summaries) + len(skipped)
    report.notes.extend(skipped)

    # Oldest first, so an interruption leaves a watermark that is behind
    # rather than ahead of what was actually processed.
    summaries.sort(key=lambda item: item.start_local)

    processed_through: dt.datetime | None = None
    for summary in summaries:
        try:
            with session_scope(engine) as session:
                if store.upsert_activity_summary(session, summary):
                    report.activities_stored += 1
            _attach_recording(engine, settings, client, report, summary.id)
        except (IntervalsAuthError, RateLimited):
            # Nothing further will succeed this run, and the watermark must
            # not move past an activity that was not handled.
            raise
        except IngestError as exc:
            report.notes.append(f"activity {summary.id}: {exc}")
            break
        processed_through = summary.start_local

    if processed_through is not None:
        with session_scope(engine) as session:
            state = store.get_or_create_state(session, DataSource.INTERVALS)
            previous = state.last_activity_start
            if previous is None or processed_through > previous:
                state.last_activity_start = processed_through


def _attach_recording(
    engine: Engine,
    settings: Settings,
    client: IntervalsClient,
    report: SyncReport,
    activity_id: str,
) -> None:
    """Download the raw FIT file if needed and fill streams and laps from it.

    An activity with no FIT file on the server is a normal thing — a
    manually entered session has no recording. That is counted, not treated
    as a failure, so the watermark can still move past it.
    """
    fit_path = settings.fit_dir / f"{activity_id}.fit"
    if not fit_path.exists():
        try:
            client.download_fit(activity_id, fit_path)
        except IntervalsApiError as exc:
            if not _is_missing(exc):
                raise
            report.activities_without_fit += 1
            report.notes.append(f"activity {activity_id}: no FIT file on the server")
            return
        report.fit_downloaded += 1

    try:
        parsed = parse_fit_file(fit_path)
    except FitFileError as exc:
        # Re-downloading a file the server sent broken will not help, but a
        # future export might. Move it aside so the next run fetches again.
        quarantined = fit_path.with_suffix(fit_path.suffix + ".invalid")
        fit_path.replace(quarantined)
        report.notes.append(f"activity {activity_id}: {exc}, file set aside")
        return

    relative = fit_path.relative_to(settings.data_dir).as_posix()
    with session_scope(engine) as session:
        report.stream_samples += store.replace_streams_and_laps(
            session, activity_id, parsed
        )
        store.set_fit_path(session, activity_id, relative)


def _sync_wellness(
    engine: Engine,
    client: IntervalsClient,
    report: SyncReport,
    watermark: dt.date | None,
    today: dt.date,
    *,
    full: bool,
) -> None:
    oldest, newest = _wellness_window(watermark, today, full=full)
    values, skipped = client.wellness_from(client.list_wellness(oldest, newest))
    report.notes.extend(skipped)

    with session_scope(engine) as session:
        report.wellness_days = store.upsert_wellness(session, values)

    # The window was processed in full, so the mark goes to its end. The
    # overlap on the next run is what catches a day filled in late.
    with session_scope(engine) as session:
        state = store.get_or_create_state(session, DataSource.INTERVALS)
        previous = state.last_wellness_date
        if previous is None or newest > previous:
            state.last_wellness_date = newest


def _sync_events(
    engine: Engine,
    client: IntervalsClient,
    report: SyncReport,
    watermark: dt.datetime | None,
    today: dt.date,
    *,
    full: bool,
) -> None:
    """Mirror the source's calendar into ``planned_workout``.

    No watermark of its own: a plan lives in the future, so the window
    always reaches forward and is re-read whole every run. That is one
    request, every write is idempotent over ``external_id``, and it is the
    only way a session deleted at the source can disappear here too.
    """
    oldest, _ = _activity_window(watermark, today, full=full)
    newest = today + dt.timedelta(days=EVENT_HORIZON_DAYS)

    try:
        entries, skipped = client.events_from(client.list_events(oldest, newest))
    except IntervalsApiError as exc:
        # The calendar is not worth failing activities and wellness over.
        report.notes.append(f"events: {exc}")
        return

    report.notes.extend(skipped)
    with session_scope(engine) as session:
        report.planned_workouts = store.upsert_planned_workouts(session, entries)
        report.planned_workouts_removed = store.drop_planned_workouts_absent_from(
            session,
            oldest=oldest,
            newest=newest,
            keep_ids={entry.id for entry in entries},
        )


def import_fit_directory(
    engine: Engine, settings: Settings, directory: Path
) -> SyncReport:
    """Import every FIT file below ``directory``, e.g. a Garmin GDPR export.

    Files are copied into the data volume, because ``activity.fit_path`` has
    to keep working after the export folder is gone. An activity that
    intervals.icu already delivered for the same start and sport is left
    alone rather than stored a second time under a different id.
    """
    report = SyncReport(source=DataSource.FIT, status=SyncStatus.OK)
    settings.fit_dir.mkdir(parents=True, exist_ok=True)

    candidates = sorted(
        path
        for path in directory.rglob("*")
        if path.is_file() and path.suffix.lower() == ".fit"
    )
    report.activities_seen = len(candidates)

    for path in candidates:
        try:
            parsed = parse_fit_file(path)
        except (FitFileError, OSError) as exc:
            report.notes.append(f"{path.name}: {exc}")
            continue

        activity_id = fit_activity_id(path)
        with session_scope(engine) as session:
            duplicate = store.find_activity_at(
                session, parsed.start_local, parsed.sport
            )
            if duplicate is not None and duplicate.id != activity_id:
                report.notes.append(f"{path.name}: already stored as {duplicate.id}")
                continue

        destination = settings.fit_dir / f"{activity_id}.fit"
        if destination != path:
            destination.write_bytes(path.read_bytes())
        relative = destination.relative_to(settings.data_dir).as_posix()

        with session_scope(engine) as session:
            if store.upsert_activity_from_fit(
                session, activity_id, parsed, fit_path=relative
            ):
                report.activities_stored += 1
            report.stream_samples += store.replace_streams_and_laps(
                session, activity_id, parsed
            )

    if report.notes:
        report.status = SyncStatus.PARTIAL
    return report
