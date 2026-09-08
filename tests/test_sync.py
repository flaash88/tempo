"""Sync orchestration: watermark discipline, partial failures, idempotency.

The intervals.icu client is driven through a mocked transport, so nothing
here reaches the network.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from tempo.config import Settings
from tempo.db.models import (
    Activity,
    ActivityStream,
    DailyLoad,
    Lap,
    SyncLog,
    SyncState,
    SyncStatus,
    WellnessDay,
)
from tempo.db.session import session_factory
from tempo.ingest.intervals_client import IntervalsClient, RetryPolicy
from tempo.ingest.sync import (
    FULL_SYNC_OLDEST,
    INCREMENTAL_OVERLAP_DAYS,
    MIN_SYNC_INTERVAL,
    due_for_sync,
    import_fit_directory,
    sync_intervals,
)
from tempo.recompute import recompute_all
from tests.fixtures.fit_writer import Sample, write_activity_file

NOW = dt.datetime(2026, 1, 20, 6, 0, tzinfo=dt.UTC)
API_KEY = "test-key"


def activity_payload(
    activity_id: str, start_local: str, **overrides: Any
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": activity_id,
        "start_date_local": start_local,
        "type": "Run",
        "distance": 3500.0,
        "moving_time": 1260,
        "elapsed_time": 1300,
        "total_elevation_gain": 12.0,
        "average_heartrate": 142,
        "max_heartrate": 168,
    }
    payload.update(overrides)
    return payload


def wellness_payload(day: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "id": day,
        "restingHR": 52,
        "hrv": 44.5,
        "sleepSecs": 25200,
        "sleepScore": 72,
    }
    payload.update(overrides)
    return payload


def fit_bytes(tmp_path: Path, seconds: int = 6) -> bytes:
    path = write_activity_file(
        tmp_path / "source.fit",
        start_utc=dt.datetime(2026, 1, 15, 6, 30, tzinfo=dt.UTC),
        samples=[
            Sample(offset_s=offset, hr=130 + offset, distance_m=offset * 2.8)
            for offset in range(seconds)
        ],
        laps=[(0, seconds, 16.8, 133)],
    )
    return path.read_bytes()


class Api:
    """A stand-in intervals.icu, assembled per test."""

    def __init__(
        self,
        *,
        activities: list[dict[str, Any]] | None = None,
        wellness: list[dict[str, Any]] | None = None,
        fit: dict[str, bytes | int] | None = None,
    ) -> None:
        self.activities = activities or []
        self.wellness = wellness or []
        self.fit = fit or {}
        self.requests: list[httpx.Request] = []
        self.waits: list[float] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path.endswith("/activities"):
            return httpx.Response(200, json=self.activities)
        if path.endswith("/wellness"):
            return httpx.Response(200, json=self.wellness)
        if path.endswith("/fit-file"):
            activity_id = path.split("/")[-2]
            payload = self.fit.get(activity_id)
            if payload is None:
                return httpx.Response(404, json={"error": "not found"})
            if isinstance(payload, int):
                return httpx.Response(payload, json={"error": "boom"})
            return httpx.Response(200, content=payload)
        return httpx.Response(404, json={})

    def factory(self) -> Callable[[Settings], IntervalsClient]:
        def build(_settings: Settings) -> IntervalsClient:
            return IntervalsClient(
                API_KEY,
                transport=httpx.MockTransport(self.handler),
                retry=RetryPolicy(max_attempts=2, initial_backoff_s=0.0),
                sleeper=self.waits.append,
            )

        return build

    def paths(self) -> list[str]:
        return [request.url.path for request in self.requests]


@pytest.fixture
def open_session(engine: Engine) -> Callable[[], Session]:
    factory = session_factory(engine)
    return factory


# --- the hourly guard ---------------------------------------------------


def test_a_source_never_synced_is_due() -> None:
    assert due_for_sync(None, NOW) is True


def test_a_source_synced_within_the_hour_is_not_due() -> None:
    assert due_for_sync(NOW - MIN_SYNC_INTERVAL + dt.timedelta(minutes=1), NOW) is (
        False
    )


def test_a_source_synced_over_an_hour_ago_is_due() -> None:
    assert due_for_sync(NOW - MIN_SYNC_INTERVAL, NOW) is True


def test_a_naive_watermark_is_read_as_utc() -> None:
    """SQLite hands datetimes back without a zone."""
    naive = (NOW - dt.timedelta(minutes=5)).replace(tzinfo=None)

    assert due_for_sync(naive, NOW) is False


def test_the_second_sync_within_the_hour_is_skipped_without_requests(
    engine: Engine, settings: Settings, tmp_path: Path
) -> None:
    api = Api(activities=[activity_payload("i1", "2026-01-15T07:30:00")])
    sync_intervals(engine, settings, now=NOW, client_factory=api.factory())
    before = len(api.requests)

    report = sync_intervals(
        engine,
        settings,
        now=NOW + dt.timedelta(minutes=10),
        client_factory=api.factory(),
    )

    assert len(api.requests) == before
    assert any("skipped" in note for note in report.notes)


def test_force_overrides_the_hourly_guard(engine: Engine, settings: Settings) -> None:
    api = Api(activities=[])
    sync_intervals(engine, settings, now=NOW, client_factory=api.factory())
    before = len(api.requests)

    sync_intervals(
        engine,
        settings,
        now=NOW + dt.timedelta(minutes=1),
        force=True,
        client_factory=api.factory(),
    )

    assert len(api.requests) > before


# --- windows ------------------------------------------------------------


def test_the_first_sync_asks_for_the_whole_history(
    engine: Engine, settings: Settings
) -> None:
    api = Api()

    sync_intervals(engine, settings, now=NOW, client_factory=api.factory())

    activities = next(
        request for request in api.requests if request.url.path.endswith("activities")
    )
    assert activities.url.params["oldest"] == FULL_SYNC_OLDEST.isoformat()
    assert activities.url.params["newest"] == "2026-01-20"


def test_a_later_sync_asks_from_the_watermark_with_an_overlap(
    engine: Engine, settings: Settings, tmp_path: Path
) -> None:
    payload = fit_bytes(tmp_path)
    api = Api(
        activities=[activity_payload("i1", "2026-01-15T07:30:00")],
        fit={"i1": payload},
    )
    sync_intervals(engine, settings, now=NOW, client_factory=api.factory())

    later = Api(activities=[], fit={})
    sync_intervals(
        engine,
        settings,
        now=NOW + dt.timedelta(days=1),
        client_factory=later.factory(),
    )

    activities = next(
        request for request in later.requests if request.url.path.endswith("activities")
    )
    expected = dt.date(2026, 1, 15) - dt.timedelta(days=INCREMENTAL_OVERLAP_DAYS)
    assert activities.url.params["oldest"] == expected.isoformat()


def test_full_ignores_the_watermark(
    engine: Engine, settings: Settings, tmp_path: Path
) -> None:
    api = Api(
        activities=[activity_payload("i1", "2026-01-15T07:30:00")],
        fit={"i1": fit_bytes(tmp_path)},
    )
    sync_intervals(engine, settings, now=NOW, client_factory=api.factory())

    again = Api(activities=[])
    sync_intervals(
        engine,
        settings,
        now=NOW + dt.timedelta(days=1),
        full=True,
        client_factory=again.factory(),
    )

    activities = next(
        request for request in again.requests if request.url.path.endswith("activities")
    )
    assert activities.url.params["oldest"] == FULL_SYNC_OLDEST.isoformat()


# --- activities and their recordings ------------------------------------


def test_a_sync_stores_the_activity_its_fit_file_and_its_streams(
    engine: Engine,
    settings: Settings,
    tmp_path: Path,
    open_session: Callable[[], Session],
) -> None:
    api = Api(
        activities=[activity_payload("i1", "2026-01-15T07:30:00")],
        wellness=[wellness_payload("2026-01-15")],
        fit={"i1": fit_bytes(tmp_path)},
    )

    report = sync_intervals(engine, settings, now=NOW, client_factory=api.factory())

    assert report.status == SyncStatus.OK
    assert report.activities_stored == 1
    assert report.fit_downloaded == 1
    assert report.stream_samples == 6
    assert report.wellness_days == 1

    assert (settings.fit_dir / "i1.fit").is_file()
    with open_session() as session:
        activity = session.get(Activity, "i1")
        assert activity is not None
        assert activity.fit_path == "fit/i1.fit"
        assert activity.sport == "Run"
        assert session.scalar(select(func.count()).select_from(ActivityStream)) == 6
        assert session.scalar(select(func.count()).select_from(Lap)) == 1
        assert session.get(WellnessDay, dt.date(2026, 1, 15)) is not None


def test_an_already_downloaded_fit_file_is_not_fetched_again(
    engine: Engine, settings: Settings, tmp_path: Path
) -> None:
    api = Api(
        activities=[activity_payload("i1", "2026-01-15T07:30:00")],
        fit={"i1": fit_bytes(tmp_path)},
    )
    sync_intervals(engine, settings, now=NOW, client_factory=api.factory())

    second = Api(
        activities=[activity_payload("i1", "2026-01-15T07:30:00")],
        fit={"i1": fit_bytes(tmp_path)},
    )
    report = sync_intervals(
        engine,
        settings,
        now=NOW + dt.timedelta(hours=2),
        client_factory=second.factory(),
    )

    assert report.fit_downloaded == 0
    assert not any(path.endswith("fit-file") for path in second.paths())


def test_syncing_twice_does_not_duplicate_anything(
    engine: Engine,
    settings: Settings,
    tmp_path: Path,
    open_session: Callable[[], Session],
) -> None:
    payload = fit_bytes(tmp_path)
    for offset in (0, 2):
        api = Api(
            activities=[activity_payload("i1", "2026-01-15T07:30:00")],
            wellness=[wellness_payload("2026-01-15")],
            fit={"i1": payload},
        )
        sync_intervals(
            engine,
            settings,
            now=NOW + dt.timedelta(hours=offset),
            client_factory=api.factory(),
        )

    with open_session() as session:
        assert session.scalar(select(func.count()).select_from(Activity)) == 1
        assert session.scalar(select(func.count()).select_from(ActivityStream)) == 6
        assert session.scalar(select(func.count()).select_from(Lap)) == 1
        assert session.scalar(select(func.count()).select_from(WellnessDay)) == 1


def test_an_activity_without_a_fit_file_on_the_server_is_still_stored(
    engine: Engine, settings: Settings, open_session: Callable[[], Session]
) -> None:
    """A manually entered session has no recording. That is not an error."""
    api = Api(activities=[activity_payload("i1", "2026-01-15T07:30:00")], fit={})

    report = sync_intervals(engine, settings, now=NOW, client_factory=api.factory())

    assert report.activities_without_fit == 1
    assert report.status == SyncStatus.PARTIAL
    with open_session() as session:
        activity = session.get(Activity, "i1")
        assert activity is not None
        assert activity.fit_path is None
        assert session.scalar(select(func.count()).select_from(ActivityStream)) == 0


def test_the_watermark_moves_past_an_activity_without_a_recording(
    engine: Engine, settings: Settings, open_session: Callable[[], Session]
) -> None:
    """Otherwise one such activity would stall every future sync."""
    api = Api(activities=[activity_payload("i1", "2026-01-15T07:30:00")], fit={})

    sync_intervals(engine, settings, now=NOW, client_factory=api.factory())

    with open_session() as session:
        state = session.get(SyncState, "intervals")
        assert state is not None
        assert state.last_activity_start == dt.datetime(2026, 1, 15, 7, 30)


def test_a_corrupt_fit_file_is_set_aside_so_the_next_run_refetches(
    engine: Engine, settings: Settings
) -> None:
    api = Api(
        activities=[activity_payload("i1", "2026-01-15T07:30:00")],
        fit={"i1": b"not a FIT file"},
    )

    report = sync_intervals(engine, settings, now=NOW, client_factory=api.factory())

    assert report.status == SyncStatus.PARTIAL
    assert any("set aside" in note for note in report.notes)
    assert not (settings.fit_dir / "i1.fit").exists()
    assert (settings.fit_dir / "i1.fit.invalid").is_file()


# --- watermark discipline ----------------------------------------------


def test_the_watermark_holds_at_the_last_activity_that_came_through_whole(
    engine: Engine,
    settings: Settings,
    tmp_path: Path,
    open_session: Callable[[], Session],
) -> None:
    """The older one is complete; the newer one fails on a server error."""
    api = Api(
        activities=[
            activity_payload("i1", "2026-01-14T07:30:00"),
            activity_payload("i2", "2026-01-16T07:30:00"),
        ],
        fit={"i1": fit_bytes(tmp_path), "i2": 500},
    )

    report = sync_intervals(engine, settings, now=NOW, client_factory=api.factory())

    assert report.status == SyncStatus.PARTIAL
    with open_session() as session:
        state = session.get(SyncState, "intervals")
        assert state is not None
        assert state.last_activity_start == dt.datetime(2026, 1, 14, 7, 30)


def test_the_watermark_never_moves_backwards(
    engine: Engine,
    settings: Settings,
    tmp_path: Path,
    open_session: Callable[[], Session],
) -> None:
    payload = fit_bytes(tmp_path)
    first = Api(
        activities=[activity_payload("i2", "2026-01-16T07:30:00")],
        fit={"i2": payload},
    )
    sync_intervals(engine, settings, now=NOW, client_factory=first.factory())

    # A later run that only sees an older activity must not rewind.
    second = Api(
        activities=[activity_payload("i1", "2026-01-10T07:30:00")],
        fit={"i1": payload},
    )
    sync_intervals(
        engine,
        settings,
        now=NOW + dt.timedelta(hours=2),
        client_factory=second.factory(),
    )

    with open_session() as session:
        state = session.get(SyncState, "intervals")
        assert state is not None
        assert state.last_activity_start == dt.datetime(2026, 1, 16, 7, 30)


def test_wellness_progress_survives_an_activity_failure(
    engine: Engine, settings: Settings, open_session: Callable[[], Session]
) -> None:
    """Activities and wellness are separate parts with separate marks."""
    api = Api(
        activities=[activity_payload("i1", "2026-01-15T07:30:00")],
        wellness=[wellness_payload("2026-01-15")],
        fit={"i1": 500},
    )

    sync_intervals(engine, settings, now=NOW, client_factory=api.factory())

    with open_session() as session:
        state = session.get(SyncState, "intervals")
        assert state is not None
        assert state.last_activity_start is None
        assert state.last_wellness_date == dt.date(2026, 1, 20)
        assert session.get(WellnessDay, dt.date(2026, 1, 15)) is not None


def test_a_rejected_key_fails_the_run_and_leaves_the_watermark_alone(
    engine: Engine, settings: Settings, open_session: Callable[[], Session]
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={})

    def build(_settings: Settings) -> IntervalsClient:
        return IntervalsClient(
            API_KEY,
            transport=httpx.MockTransport(handler),
            retry=RetryPolicy(max_attempts=1),
            sleeper=lambda _seconds: None,
        )

    report = sync_intervals(engine, settings, now=NOW, client_factory=build)

    assert report.status == SyncStatus.FAILED
    with open_session() as session:
        state = session.get(SyncState, "intervals")
        assert state is not None
        assert state.last_success_at is None
        assert state.last_activity_start is None


# --- audit log ---------------------------------------------------------


def test_every_run_is_recorded_in_the_sync_log(
    engine: Engine,
    settings: Settings,
    tmp_path: Path,
    open_session: Callable[[], Session],
) -> None:
    api = Api(
        activities=[activity_payload("i1", "2026-01-15T07:30:00")],
        fit={"i1": fit_bytes(tmp_path)},
    )

    sync_intervals(engine, settings, now=NOW, client_factory=api.factory())

    with open_session() as session:
        entries = list(session.scalars(select(SyncLog)))

    assert len(entries) == 1
    assert entries[0].source == "intervals"
    assert entries[0].status == SyncStatus.OK
    assert entries[0].finished_at is not None
    assert "activities seen 1" in (entries[0].detail or "")


def test_the_sync_log_detail_never_carries_the_api_key(
    engine: Engine, settings: Settings, open_session: Callable[[], Session]
) -> None:
    api = Api(activities=[activity_payload("i1", "2026-01-15T07:30:00")], fit={})

    sync_intervals(engine, settings, now=NOW, client_factory=api.factory())

    with open_session() as session:
        details = [entry.detail or "" for entry in session.scalars(select(SyncLog))]

    assert details
    assert all(API_KEY not in detail for detail in details)


# --- wellness ----------------------------------------------------------


def test_empty_wellness_days_are_not_stored(
    engine: Engine, settings: Settings, open_session: Callable[[], Session]
) -> None:
    """The API answers a range with a row per day, mostly empty ones."""
    api = Api(
        wellness=[
            wellness_payload("2026-01-15"),
            {"id": "2026-01-16"},
            {"id": "2026-01-17", "restingHR": None, "hrv": None},
        ]
    )

    report = sync_intervals(engine, settings, now=NOW, client_factory=api.factory())

    assert report.wellness_days == 1
    with open_session() as session:
        assert session.scalar(select(func.count()).select_from(WellnessDay)) == 1


def test_an_unusable_wellness_row_is_skipped_and_noted(
    engine: Engine, settings: Settings
) -> None:
    api = Api(wellness=[wellness_payload("2026-01-15"), {"nope": True}])

    report = sync_intervals(engine, settings, now=NOW, client_factory=api.factory())

    assert report.wellness_days == 1
    assert report.status == SyncStatus.PARTIAL
    assert any("no date" in note for note in report.notes)


# --- file import -------------------------------------------------------


def test_import_dir_reads_every_fit_file_below_the_directory(
    engine: Engine,
    settings: Settings,
    tmp_path: Path,
    open_session: Callable[[], Session],
) -> None:
    export = tmp_path / "export"
    write_activity_file(
        export / "2026" / "one.fit",
        start_utc=dt.datetime(2026, 1, 10, 6, 0, tzinfo=dt.UTC),
        samples=[Sample(offset_s=offset) for offset in range(4)],
    )
    write_activity_file(
        export / "two.FIT",
        start_utc=dt.datetime(2026, 1, 11, 6, 0, tzinfo=dt.UTC),
        samples=[Sample(offset_s=offset) for offset in range(3)],
    )
    (export / "readme.txt").write_text("not a fit file", encoding="utf-8")

    report = import_fit_directory(engine, settings, export)

    assert report.activities_seen == 2
    assert report.activities_stored == 2
    assert report.stream_samples == 7
    with open_session() as session:
        activities = list(session.scalars(select(Activity)))
    assert len(activities) == 2
    assert all(activity.source == "fit" for activity in activities)
    assert all(
        (settings.data_dir / (activity.fit_path or "")).is_file()
        for activity in activities
    )


def test_import_dir_is_idempotent(
    engine: Engine,
    settings: Settings,
    tmp_path: Path,
    open_session: Callable[[], Session],
) -> None:
    export = tmp_path / "export"
    write_activity_file(
        export / "one.fit",
        start_utc=dt.datetime(2026, 1, 10, 6, 0, tzinfo=dt.UTC),
        samples=[Sample(offset_s=offset) for offset in range(4)],
    )

    import_fit_directory(engine, settings, export)
    import_fit_directory(engine, settings, export)

    with open_session() as session:
        assert session.scalar(select(func.count()).select_from(Activity)) == 1
        assert session.scalar(select(func.count()).select_from(ActivityStream)) == 4


def test_import_dir_leaves_an_activity_intervals_already_delivered_alone(
    engine: Engine,
    settings: Settings,
    tmp_path: Path,
    open_session: Callable[[], Session],
) -> None:
    api = Api(
        activities=[activity_payload("i1", "2026-01-15T07:30:00")],
        fit={"i1": fit_bytes(tmp_path)},
    )
    sync_intervals(engine, settings, now=NOW, client_factory=api.factory())

    export = tmp_path / "export"
    write_activity_file(
        export / "same.fit",
        start_utc=dt.datetime(2026, 1, 15, 6, 30, tzinfo=dt.UTC),
        samples=[Sample(offset_s=offset) for offset in range(6)],
        utc_offset_s=3600,
    )

    report = import_fit_directory(engine, settings, export)

    assert report.activities_stored == 0
    assert any("already stored as i1" in note for note in report.notes)
    with open_session() as session:
        assert session.scalar(select(func.count()).select_from(Activity)) == 1


def test_import_dir_notes_a_file_it_cannot_read(
    engine: Engine, settings: Settings, tmp_path: Path
) -> None:
    export = tmp_path / "export"
    export.mkdir()
    (export / "broken.fit").write_bytes(b"nope")

    report = import_fit_directory(engine, settings, export)

    assert report.status == SyncStatus.PARTIAL
    assert report.activities_stored == 0
    assert any("broken.fit" in note for note in report.notes)


# --- the recompute the phase has to reach ------------------------------


def test_recompute_runs_through_after_a_sync(
    engine: Engine,
    settings: Settings,
    tmp_path: Path,
    open_session: Callable[[], Session],
) -> None:
    api = Api(
        activities=[activity_payload("i1", "2026-01-15T07:30:00")],
        wellness=[wellness_payload("2026-01-15")],
        fit={"i1": fit_bytes(tmp_path)},
    )
    sync_intervals(engine, settings, now=NOW, client_factory=api.factory())

    report = recompute_all(engine, today=dt.date(2026, 1, 20))

    assert report.first_day == dt.date(2026, 1, 15)
    assert report.last_day == dt.date(2026, 1, 20)
    assert report.days == 6
    assert report.days_with_session == 1

    with open_session() as session:
        rows = {row.date: row for row in session.scalars(select(DailyLoad))}

    assert len(rows) == 6
    session_day = rows[dt.date(2026, 1, 15)]
    assert session_day.duration_s == 1260
    assert session_day.distance_m == pytest.approx(3500.0)
    # The formulas are phase 3; "not computed" is not "zero".
    assert session_day.trimp is None

    empty_day = rows[dt.date(2026, 1, 18)]
    assert empty_day.duration_s == 0
    assert empty_day.trimp == 0.0
