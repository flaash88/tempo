"""The optional Garmin connector.

Off by default, at most one attempt per day, and it disables itself rather
than retrying — repeated failed attempts lead to blocks at the account
level. ``garminconnect`` is not installed here, and nothing in these tests
needs it: the API is a fake behind the module's own Protocol.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from typing import Any

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from tempo.config import Settings, load_settings
from tempo.db.models import SyncLog, SyncState, SyncStatus, WellnessDay
from tempo.db.session import session_factory
from tempo.ingest.garmin_optional import (
    LOOKBACK_DAYS,
    GarminApi,
    body_battery_for_day,
    day_of,
    due_for_attempt,
    stand_down_reason,
    status_code_of,
    sync_garmin,
    training_readiness_for_day,
)
from tests.fixtures.synthetic import make_wellness_block

NOW = dt.datetime(2026, 9, 8, 6, 0, tzinfo=dt.UTC)


@pytest.fixture
def open_session(engine: Engine) -> Callable[[], Session]:
    return session_factory(engine)


@pytest.fixture
def garmin_settings(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Settings:
    monkeypatch.setenv("GARMIN_DIRECT_ENABLED", "true")
    monkeypatch.setenv("GARMIN_EMAIL", "athlete@example.com")
    monkeypatch.setenv("GARMIN_PASSWORD", "garmin-secret")
    return load_settings()


class FakeGarmin:
    """A stand-in for the slice of garminconnect the module uses."""

    def __init__(
        self,
        *,
        body_battery: Any = None,
        readiness: dict[str, Any] | None = None,
        raise_on_body_battery: BaseException | None = None,
    ) -> None:
        self.body_battery = body_battery if body_battery is not None else []
        self.readiness = readiness or {}
        self.raise_on_body_battery = raise_on_body_battery
        self.calls: list[str] = []

    def get_body_battery(self, startdate: str, enddate: str) -> Any:
        self.calls.append(f"body_battery:{startdate}:{enddate}")
        if self.raise_on_body_battery is not None:
            raise self.raise_on_body_battery
        return self.body_battery

    def get_training_readiness(self, cdate: str) -> Any:
        self.calls.append(f"readiness:{cdate}")
        return self.readiness.get(cdate, [])


def factory_for(api: GarminApi) -> Callable[[Settings], GarminApi]:
    def build(_settings: Settings) -> GarminApi:
        return api

    return build


# --- the switch --------------------------------------------------------


def test_the_connector_is_off_by_default(engine: Engine, settings: Settings) -> None:
    api = FakeGarmin()

    report = sync_garmin(engine, settings, now=NOW, api_factory=factory_for(api))

    assert report.status == SyncStatus.DISABLED
    assert api.calls == []


def test_disabled_leaves_no_trace_in_the_sync_log(
    engine: Engine, settings: Settings, open_session: Callable[[], Session]
) -> None:
    """An hourly "still off" row would be noise, not an audit trail."""
    sync_garmin(engine, settings, now=NOW, api_factory=factory_for(FakeGarmin()))

    with open_session() as session:
        assert session.scalar(select(func.count()).select_from(SyncLog)) == 0


# --- once a day --------------------------------------------------------


def test_a_source_never_attempted_is_due() -> None:
    assert due_for_attempt(None, NOW) is True


def test_an_attempt_within_the_day_is_not_due() -> None:
    assert due_for_attempt(NOW - dt.timedelta(hours=23), NOW) is False


def test_an_attempt_a_day_ago_is_due() -> None:
    assert due_for_attempt(NOW - dt.timedelta(days=1), NOW) is True


def test_a_second_run_on_the_same_day_makes_no_calls(
    engine: Engine, garmin_settings: Settings
) -> None:
    first = FakeGarmin(body_battery=[{"date": "2026-09-08", "bodyBatteryLevel": 71}])
    sync_garmin(engine, garmin_settings, now=NOW, api_factory=factory_for(first))

    second = FakeGarmin()
    report = sync_garmin(
        engine,
        garmin_settings,
        now=NOW + dt.timedelta(hours=6),
        api_factory=factory_for(second),
    )

    assert second.calls == []
    assert any("within the last day" in note for note in report.notes)


# --- reading -----------------------------------------------------------


def test_body_battery_and_readiness_land_on_the_wellness_day(
    engine: Engine,
    garmin_settings: Settings,
    open_session: Callable[[], Session],
) -> None:
    api = FakeGarmin(
        body_battery=[{"calendarDate": "2026-09-08", "bodyBatteryLevel": 71}],
        readiness={"2026-09-08": [{"score": 63}]},
    )

    report = sync_garmin(engine, garmin_settings, now=NOW, api_factory=factory_for(api))

    assert report.status == SyncStatus.OK
    assert report.days_updated == 1
    with open_session() as session:
        day = session.get(WellnessDay, dt.date(2026, 9, 8))
    assert day is not None
    assert day.body_battery == 71
    assert day.training_readiness == 63
    assert day.source == "garmin"


def test_the_window_is_the_lookback_and_readiness_is_asked_per_day(
    engine: Engine, garmin_settings: Settings
) -> None:
    api = FakeGarmin()

    sync_garmin(engine, garmin_settings, now=NOW, api_factory=factory_for(api))

    assert api.calls[0] == "body_battery:2026-09-02:2026-09-08"
    assert len([call for call in api.calls if call.startswith("readiness")]) == (
        LOOKBACK_DAYS
    )


def test_an_existing_wellness_day_keeps_its_source_and_its_values(
    engine: Engine,
    garmin_settings: Settings,
    open_session: Callable[[], Session],
) -> None:
    """Garmin contributing one field does not make the day Garmin's."""
    with open_session() as session:
        session.add_all(make_wellness_block(dt.date(2026, 9, 8), 1))
        session.commit()

    api = FakeGarmin(body_battery=[{"date": "2026-09-08", "bodyBatteryLevel": 71}])
    sync_garmin(engine, garmin_settings, now=NOW, api_factory=factory_for(api))

    with open_session() as session:
        day = session.get(WellnessDay, dt.date(2026, 9, 8))

    assert day is not None
    assert day.body_battery == 71
    assert day.source == "intervals"
    assert day.hrv is not None


def test_a_day_with_no_reading_is_not_written(
    engine: Engine,
    garmin_settings: Settings,
    open_session: Callable[[], Session],
) -> None:
    api = FakeGarmin(body_battery=[{"date": "2026-09-08"}])

    report = sync_garmin(engine, garmin_settings, now=NOW, api_factory=factory_for(api))

    assert report.days_updated == 0
    with open_session() as session:
        assert session.scalar(select(func.count()).select_from(WellnessDay)) == 0


# --- standing down -----------------------------------------------------


class Rejected(Exception):
    def __init__(self, status_code: int) -> None:
        super().__init__(f"rejected with {status_code}")
        self.status_code = status_code


class GarminConnectAuthenticationError(Exception):
    """Named like the library's own, which carries no status code."""


class GarminConnectTooManyRequestsError(Exception):
    """Likewise."""


@pytest.mark.parametrize("status", [401, 403, 429])
def test_a_refusal_disables_the_module_for_this_run(
    engine: Engine,
    garmin_settings: Settings,
    open_session: Callable[[], Session],
    status: int,
) -> None:
    api = FakeGarmin(raise_on_body_battery=Rejected(status))

    report = sync_garmin(engine, garmin_settings, now=NOW, api_factory=factory_for(api))

    assert report.status == SyncStatus.DISABLED
    assert str(status) in report.notes[0]
    # It stopped: no readiness calls followed the refusal.
    assert not any(call.startswith("readiness") for call in api.calls)

    with open_session() as session:
        entries = list(session.scalars(select(SyncLog)))
        state = session.get(SyncState, "garmin")

    assert [entry.status for entry in entries] == [SyncStatus.DISABLED]
    assert entries[0].source == "garmin"
    # A refusal is not a success, so the daily mark does not move — but the
    # log row records the attempt.
    assert state is not None
    assert state.last_success_at is None


def test_an_authentication_error_without_a_status_still_stands_down(
    engine: Engine, garmin_settings: Settings
) -> None:
    api = FakeGarmin(raise_on_body_battery=GarminConnectAuthenticationError("no token"))

    report = sync_garmin(engine, garmin_settings, now=NOW, api_factory=factory_for(api))

    assert report.status == SyncStatus.DISABLED
    assert "credentials" in report.notes[0]


def test_rate_limiting_without_a_status_still_stands_down(
    engine: Engine, garmin_settings: Settings
) -> None:
    api = FakeGarmin(
        raise_on_body_battery=GarminConnectTooManyRequestsError("slow down")
    )

    report = sync_garmin(engine, garmin_settings, now=NOW, api_factory=factory_for(api))

    assert report.status == SyncStatus.DISABLED
    assert "rate limited" in report.notes[0]


def test_any_other_failure_is_reported_as_a_failure_not_a_stand_down(
    engine: Engine, garmin_settings: Settings
) -> None:
    api = FakeGarmin(raise_on_body_battery=TimeoutError("network down"))

    report = sync_garmin(engine, garmin_settings, now=NOW, api_factory=factory_for(api))

    assert report.status == SyncStatus.FAILED
    assert "network down" in report.notes[0]


def test_a_missing_optional_library_is_just_another_refusal(
    engine: Engine, garmin_settings: Settings
) -> None:
    """With the extra not installed, the module says so and stops."""

    def build(_settings: Settings) -> GarminApi:
        raise RuntimeError("the garmin extra is not installed")

    report = sync_garmin(engine, garmin_settings, now=NOW, api_factory=build)

    assert report.status == SyncStatus.FAILED
    assert "not installed" in report.notes[0]


def test_the_garmin_password_never_reaches_the_sync_log(
    engine: Engine,
    garmin_settings: Settings,
    open_session: Callable[[], Session],
) -> None:
    api = FakeGarmin(raise_on_body_battery=Rejected(403))

    sync_garmin(engine, garmin_settings, now=NOW, api_factory=factory_for(api))

    with open_session() as session:
        details = [entry.detail or "" for entry in session.scalars(select(SyncLog))]

    assert details
    assert all("garmin-secret" not in detail for detail in details)


# --- payload reading ---------------------------------------------------


def test_a_status_code_is_found_through_the_exception_chain() -> None:
    inner = Rejected(429)
    try:
        try:
            raise inner
        except Rejected as exc:
            raise RuntimeError("wrapped") from exc
    except RuntimeError as outer:
        assert status_code_of(outer) == 429


def test_an_exception_without_a_status_has_none() -> None:
    assert status_code_of(ValueError("nope")) is None


def test_an_ordinary_error_is_not_a_reason_to_stand_down() -> None:
    assert stand_down_reason(ValueError("nope")) is None


def test_the_body_battery_series_yields_the_days_peak() -> None:
    """Body Battery tops out after sleep; that peak is the recovery reading."""
    payload = {
        "date": "2026-09-08",
        "bodyBatteryValuesArray": [
            [1, "MEASURED", 40, 1],
            [2, "MEASURED", 78, 1],
            [3, "MEASURED", 55, 1],
        ],
    }

    assert body_battery_for_day(payload) == 78


def test_a_single_level_in_the_payload_is_used_as_it_stands() -> None:
    assert body_battery_for_day({"bodyBatteryLevel": 61}) == 61


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"bodyBatteryValuesArray": []},
        {"bodyBatteryValuesArray": [[1, "MEASURED"]]},
        {"bodyBatteryLevel": 140},
        {"bodyBatteryLevel": "hoch"},
        None,
        [],
    ],
)
def test_an_unusable_body_battery_payload_yields_none(payload: Any) -> None:
    assert body_battery_for_day(payload) is None


def test_the_readiness_score_is_read_from_a_list_or_an_object() -> None:
    assert training_readiness_for_day([{"score": 63}]) == 63
    assert training_readiness_for_day({"score": 63}) == 63


@pytest.mark.parametrize("payload", [[], {}, None, [{"score": None}], "nope"])
def test_an_unusable_readiness_payload_yields_none(payload: Any) -> None:
    assert training_readiness_for_day(payload) is None


def test_the_day_is_read_from_any_of_the_known_date_keys() -> None:
    assert day_of({"date": "2026-09-08"}) == dt.date(2026, 9, 8)
    assert day_of({"calendarDate": "2026-09-08T00:00:00"}) == dt.date(2026, 9, 8)
    assert day_of({"nope": "2026-09-08"}) is None
    assert day_of({"date": "achter September"}) is None
