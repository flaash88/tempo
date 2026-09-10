"""The way back to the watch: confirmation, transfer, and idempotency.

The calendar is mocked through an ``httpx.MockTransport``; the socket ban
in ``conftest`` makes sure that stays true. What is checked here is mostly
what does *not* happen: no transfer without a confirmation, no second
event for the same session, no overwriting of anything the athlete already
agreed to or wrote somewhere else.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from tempo.api.app import create_app
from tempo.api.auth import hash_password
from tempo.config import Settings, load_settings
from tempo.db.models import (
    DataSource,
    PlannedWorkout,
    WorkoutSyncStatus,
)
from tempo.db.session import session_factory
from tempo.ingest.intervals_client import (
    IntervalsClient,
    PlannedWorkoutValues,
    RetryPolicy,
)
from tempo.ingest.store import upsert_planned_workouts
from tempo.ingest.writeback import (
    AlreadyConfirmed,
    NotConfirmed,
    NotOurWorkout,
    TransferInFlight,
    WorkoutProposal,
    confirm_workout,
    event_payload,
    propose_workout,
    push_workout,
    update_workout,
)

PASSWORD = "ein sehr langes Passwort"
PASSWORD_HASH = hash_password(PASSWORD)
API_KEY = "intervals-secret-key-7f2c"


def tomorrow() -> dt.date:
    return dt.datetime.now(tz=dt.UTC).date() + dt.timedelta(days=1)


class Calendar:
    """A stand-in intervals.icu calendar that records what it was sent."""

    def __init__(self, *, status_code: int = 200, event_id: str = "ev-1") -> None:
        self.status_code = status_code
        self.event_id = event_id
        self.posts: list[dict[str, Any]] = []
        self.puts: list[tuple[str, dict[str, Any]]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else {}
        if self.status_code != 200:
            return httpx.Response(self.status_code, json={"error": "boom"})
        if request.method == "POST":
            self.posts.append(body)
            return httpx.Response(200, json={"id": self.event_id, **body})
        if request.method == "PUT":
            self.puts.append((request.url.path.rsplit("/", 1)[-1], body))
            return httpx.Response(200, json={"id": self.event_id, **body})
        return httpx.Response(404, json={})

    def factory(self) -> Callable[[Settings], IntervalsClient]:
        def build(_settings: Settings) -> IntervalsClient:
            return IntervalsClient(
                API_KEY,
                transport=httpx.MockTransport(self.handler),
                retry=RetryPolicy(max_attempts=2, initial_backoff_s=0.0),
                sleeper=lambda _seconds: None,
            )

        return build

    @property
    def calls(self) -> int:
        return len(self.posts) + len(self.puts)


@pytest.fixture
def calendar() -> Calendar:
    return Calendar()


@pytest.fixture
def open_session(engine: Engine) -> Callable[[], Session]:
    return session_factory(engine)


@pytest.fixture
def configured(
    settings: Settings, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Settings:
    monkeypatch.setenv("TEMPO_PASSWORD_HASH", PASSWORD_HASH)
    monkeypatch.setenv("INTERVALS_API_KEY", API_KEY)
    return load_settings()


@pytest.fixture
def client(configured: Settings, calendar: Calendar) -> Iterator[TestClient]:
    with TestClient(
        create_app(configured, client_factory=calendar.factory()),
        base_url="https://testserver",
    ) as test_client:
        test_client.post("/api/auth/login", json={"password": PASSWORD})
        yield test_client


def a_proposal(**overrides: Any) -> WorkoutProposal:
    values: dict[str, Any] = {
        "date": tomorrow(),
        "sport": "Run",
        "name": "Ruhiger Dauerlauf",
        "description": "45 min in Zone 2",
        "target_time_s": 2_700,
        "target_dist_m": 8_000.0,
        "workout_doc": {"steps": [{"duration": 2_700, "zone": 2}]},
    }
    values.update(overrides)
    return WorkoutProposal(**values)


def a_request(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "date": tomorrow().isoformat(),
        "sport": "Run",
        "name": "Ruhiger Dauerlauf",
        "description": "45 min in Zone 2",
        "target_time_s": 2_700,
        "workout_doc": {"steps": [{"duration": 2_700, "zone": 2}]},
    }
    payload.update(overrides)
    return payload


def seed_source_event(session: Session, day: dt.date, entry_id: str = "src-1") -> None:
    """A session the athlete created in intervals.icu itself."""
    session.add(
        PlannedWorkout(
            id=entry_id,
            date=day,
            category="WORKOUT",
            sport="Run",
            name="Vom Trainer",
            source=DataSource.INTERVALS,
        )
    )


# --- proposing and confirming ------------------------------------------


def test_a_proposal_starts_unconfirmed_and_unsent(engine: Engine) -> None:
    entry = propose_workout(engine, a_proposal())

    assert entry.source == DataSource.TEMPO
    assert entry.sync_status == WorkoutSyncStatus.NOT_SENT
    assert entry.confirmed_at is None
    assert entry.synced_at is None
    # Its own idempotency key from the start, so the calendar can recognise
    # it even if the answer to the first attempt is lost.
    assert entry.external_id == entry.id


def test_nothing_is_sent_without_a_confirmation(
    engine: Engine, configured: Settings, calendar: Calendar
) -> None:
    entry = propose_workout(engine, a_proposal())

    with pytest.raises(NotConfirmed):
        push_workout(engine, configured, entry.id, client_factory=calendar.factory())

    assert calendar.calls == 0


def test_a_confirmed_session_reaches_the_calendar(
    engine: Engine,
    configured: Settings,
    calendar: Calendar,
    open_session: Callable[[], Session],
) -> None:
    entry = propose_workout(engine, a_proposal())
    confirm_workout(engine, entry.id)

    report = push_workout(
        engine, configured, entry.id, client_factory=calendar.factory()
    )

    assert report is not None and report.sent is True
    assert len(calendar.posts) == 1
    sent = calendar.posts[0]
    assert sent["external_id"] == entry.id
    assert sent["category"] == "WORKOUT"
    assert sent["workout_doc"] == {"steps": [{"duration": 2_700, "zone": 2}]}
    assert sent["moving_time"] == 2_700
    # Absent rather than null: no distance target is not a target of none.
    assert "icu_training_load" not in sent

    with open_session() as session:
        stored = session.get(PlannedWorkout, entry.id)
        assert stored is not None
        assert stored.sync_status == WorkoutSyncStatus.ON_WATCH
        assert stored.synced_at is not None
        assert stored.remote_event_id == "ev-1"
        assert stored.sync_error is None


def test_sending_the_same_session_twice_sends_it_once(
    engine: Engine, configured: Settings, calendar: Calendar
) -> None:
    entry = propose_workout(engine, a_proposal())
    confirm_workout(engine, entry.id)
    push_workout(engine, configured, entry.id, client_factory=calendar.factory())

    again = push_workout(
        engine, configured, entry.id, client_factory=calendar.factory()
    )

    assert again is not None and again.sent is False
    assert calendar.calls == 1


def test_a_change_updates_the_event_instead_of_adding_one(
    engine: Engine,
    configured: Settings,
    calendar: Calendar,
    open_session: Callable[[], Session],
) -> None:
    """Idempotent over external_id: one session, one event, whatever happens."""
    entry = propose_workout(engine, a_proposal())
    confirm_workout(engine, entry.id)
    push_workout(engine, configured, entry.id, client_factory=calendar.factory())

    changed = update_workout(engine, entry.id, a_proposal(target_time_s=3_600))
    assert changed is not None
    # On the watch, but no longer what is planned here.
    assert changed.sync_status == WorkoutSyncStatus.OUTDATED
    assert changed.confirmed_at is None

    confirm_workout(engine, entry.id)
    push_workout(engine, configured, entry.id, client_factory=calendar.factory())

    assert len(calendar.posts) == 1
    assert len(calendar.puts) == 1
    event_id, body = calendar.puts[0]
    assert event_id == "ev-1"
    assert body["moving_time"] == 3_600
    assert body["external_id"] == entry.id

    with open_session() as session:
        stored = session.get(PlannedWorkout, entry.id)
        assert stored is not None
        assert stored.sync_status == WorkoutSyncStatus.ON_WATCH


def test_a_failed_transfer_is_recorded_without_the_key(
    engine: Engine, configured: Settings, open_session: Callable[[], Session]
) -> None:
    broken = Calendar(status_code=500)
    entry = propose_workout(engine, a_proposal())
    confirm_workout(engine, entry.id)

    report = push_workout(engine, configured, entry.id, client_factory=broken.factory())

    assert report is not None and report.sent is False
    assert report.status == WorkoutSyncStatus.FAILED
    with open_session() as session:
        stored = session.get(PlannedWorkout, entry.id)
        assert stored is not None
        assert stored.sync_status == WorkoutSyncStatus.FAILED
        assert stored.sync_error
        assert API_KEY not in stored.sync_error
        assert stored.synced_at is None


def test_a_failed_transfer_can_be_repeated(
    engine: Engine, configured: Settings, calendar: Calendar
) -> None:
    broken = Calendar(status_code=500)
    entry = propose_workout(engine, a_proposal())
    confirm_workout(engine, entry.id)
    push_workout(engine, configured, entry.id, client_factory=broken.factory())

    report = push_workout(
        engine, configured, entry.id, client_factory=calendar.factory()
    )

    assert report is not None and report.sent is True
    assert len(calendar.posts) == 1


# --- what is never overwritten -----------------------------------------


def test_a_confirmed_session_is_not_replaced_silently(engine: Engine) -> None:
    first = propose_workout(engine, a_proposal())
    confirm_workout(engine, first.id)

    with pytest.raises(AlreadyConfirmed):
        propose_workout(engine, a_proposal(name="Etwas ganz anderes"))


def test_replacing_a_confirmed_session_withdraws_the_confirmation(
    engine: Engine,
) -> None:
    first = propose_workout(engine, a_proposal())
    confirm_workout(engine, first.id)

    replaced = propose_workout(
        engine, a_proposal(name="Etwas ganz anderes"), replace=True
    )

    assert replaced.id == first.id
    assert replaced.name == "Etwas ganz anderes"
    assert replaced.confirmed_at is None
    assert replaced.sync_status == WorkoutSyncStatus.NOT_SENT


def test_an_unconfirmed_proposal_is_replaced_without_asking(engine: Engine) -> None:
    """Only a confirmation is worth protecting. A suggestion is not."""
    first = propose_workout(engine, a_proposal())

    second = propose_workout(engine, a_proposal(name="Doch lieber ruhig"))

    assert second.id != first.id


def test_the_sources_own_entry_is_never_touched(
    engine: Engine,
    configured: Settings,
    calendar: Calendar,
    open_session: Callable[[], Session],
) -> None:
    day = tomorrow()
    with open_session() as session:
        seed_source_event(session, day)
        session.commit()

    with pytest.raises(AlreadyConfirmed):
        propose_workout(engine, a_proposal(date=day))
    # Not even when asked to replace: it was not written here.
    with pytest.raises(AlreadyConfirmed):
        propose_workout(engine, a_proposal(date=day), replace=True)
    with pytest.raises(NotOurWorkout):
        confirm_workout(engine, "src-1")
    with pytest.raises(NotOurWorkout):
        push_workout(engine, configured, "src-1", client_factory=calendar.factory())

    assert calendar.calls == 0


def test_reading_the_calendar_back_keeps_the_bookkeeping(
    engine: Engine,
    configured: Settings,
    calendar: Calendar,
    open_session: Callable[[], Session],
) -> None:
    """The session comes back as one of the source's events. It stays ours."""
    entry = propose_workout(engine, a_proposal())
    confirm_workout(engine, entry.id)
    push_workout(engine, configured, entry.id, client_factory=calendar.factory())

    with open_session() as session:
        upsert_planned_workouts(
            session,
            [
                PlannedWorkoutValues(
                    id="ev-1",
                    date=tomorrow(),
                    category="WORKOUT",
                    sport="Run",
                    name="Ruhiger Dauerlauf",
                    external_id=entry.id,
                )
            ],
        )
        session.commit()

    with open_session() as session:
        stored = session.get(PlannedWorkout, entry.id)
        assert stored is not None, "the row kept its own id"
        assert stored.source == DataSource.TEMPO
        assert stored.sync_status == WorkoutSyncStatus.ON_WATCH
        assert stored.confirmed_at is not None
        assert stored.remote_event_id == "ev-1"


# --- the payload -------------------------------------------------------


def test_the_event_payload_omits_what_is_not_set(engine: Engine) -> None:
    entry = propose_workout(
        engine,
        WorkoutProposal(date=tomorrow(), sport="Run", name="Nur ein Name"),
    )

    payload = event_payload(entry)

    assert payload["name"] == "Nur ein Name"
    assert payload["start_date_local"].startswith(tomorrow().isoformat())
    for absent in ("moving_time", "distance", "icu_training_load", "workout_doc"):
        assert absent not in payload


# --- the endpoints -----------------------------------------------------


def test_the_endpoints_walk_the_whole_way(
    client: TestClient, calendar: Calendar
) -> None:
    created = client.post("/api/plan/workouts", json=a_request())
    assert created.status_code == 201
    workout = created.json()
    assert workout["sync"]["status"] == "not_sent"
    assert workout["sync"]["confirmed_at"] is None
    assert workout["sync"]["sendable"] is True

    refused = client.post(f"/api/plan/workouts/{workout['id']}/push")
    assert refused.status_code == 409
    assert calendar.calls == 0

    confirmed = client.post(f"/api/plan/workouts/{workout['id']}/confirm")
    assert confirmed.status_code == 200
    assert confirmed.json()["sync"]["confirmed_at"] is not None

    pushed = client.post(f"/api/plan/workouts/{workout['id']}/push")
    assert pushed.status_code == 200
    body = pushed.json()
    assert body["sent"] is True
    assert body["workout"]["sync"]["status"] == "on_watch"
    assert body["workout"]["sync"]["synced_at"] is not None
    assert calendar.calls == 1

    # And the plan screen shows the same state.
    plan = client.get("/api/plan", params={"from_date": tomorrow().isoformat()})
    assert plan.status_code == 200
    assert plan.json()["workouts"][0]["sync"]["status"] == "on_watch"


def test_the_endpoint_refuses_a_second_confirmed_session_that_day(
    client: TestClient,
) -> None:
    created = client.post("/api/plan/workouts", json=a_request()).json()
    client.post(f"/api/plan/workouts/{created['id']}/confirm")

    clash = client.post("/api/plan/workouts", json=a_request(name="Anders"))

    assert clash.status_code == 409
    assert "replace=true" in clash.json()["detail"]

    replaced = client.post(
        "/api/plan/workouts", json=a_request(name="Anders"), params={"replace": "true"}
    )
    assert replaced.status_code == 201
    assert replaced.json()["sync"]["confirmed_at"] is None


def test_editing_after_the_transfer_reports_the_outdated_state(
    client: TestClient,
) -> None:
    created = client.post("/api/plan/workouts", json=a_request()).json()
    client.post(f"/api/plan/workouts/{created['id']}/confirm")
    client.post(f"/api/plan/workouts/{created['id']}/push")

    changed = client.put(
        f"/api/plan/workouts/{created['id']}", json=a_request(target_time_s=3_600)
    )

    assert changed.status_code == 200
    assert changed.json()["sync"]["status"] == "outdated"
    assert changed.json()["sync"]["confirmed_at"] is None


def test_the_endpoint_refuses_a_session_in_the_past(client: TestClient) -> None:
    yesterday = dt.datetime.now(tz=dt.UTC).date() - dt.timedelta(days=30)

    response = client.post("/api/plan/workouts", json=a_request(date=str(yesterday)))

    assert response.status_code == 400


def test_a_missing_session_answers_404(client: TestClient) -> None:
    assert client.post("/api/plan/workouts/nope/confirm").status_code == 404
    assert client.post("/api/plan/workouts/nope/push").status_code == 404
    assert client.put("/api/plan/workouts/nope", json=a_request()).status_code == 404


def test_the_endpoints_need_a_session(configured: Settings, calendar: Calendar) -> None:
    with TestClient(
        create_app(configured, client_factory=calendar.factory()),
        base_url="https://testserver",
    ) as anonymous:
        assert anonymous.post("/api/plan/workouts", json=a_request()).status_code == 401
        assert anonymous.post("/api/plan/workouts/x/push").status_code == 401
    assert calendar.calls == 0


def test_no_answer_carries_the_key(client: TestClient) -> None:
    created = client.post("/api/plan/workouts", json=a_request())

    assert API_KEY not in created.text


def test_a_transfer_left_in_flight_is_not_started_again(
    engine: Engine,
    configured: Settings,
    calendar: Calendar,
    open_session: Callable[[], Session],
) -> None:
    """A crash mid-transfer leaves `sending`, and that is the honest answer.

    Nobody knows whether the event arrived, so the next attempt refuses
    rather than risking a second one; the athlete sees the state and the
    next calendar read settles it.
    """
    entry = propose_workout(engine, a_proposal())
    confirm_workout(engine, entry.id)
    with open_session() as session:
        stuck = session.get(PlannedWorkout, entry.id)
        assert stuck is not None
        stuck.sync_status = WorkoutSyncStatus.SENDING
        session.commit()

    with pytest.raises(TransferInFlight):
        push_workout(engine, configured, entry.id, client_factory=calendar.factory())

    assert calendar.calls == 0


# --- taking a generated week into the calendar -------------------------


def an_adopted_day(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "date": tomorrow().isoformat(),
        "title": "Lockerer Dauerlauf",
        "duration_s": 2_400,
        "zone_label": "Z2",
        "purpose": "Grundlage aufbauen ohne Ermüdung.",
    }
    payload.update(overrides)
    return payload


def test_adopting_a_day_confirms_it_but_sends_nothing(
    client: TestClient, calendar: Calendar, open_session: Callable[[], Session]
) -> None:
    """The click is the confirmation. The watch is still a separate act."""
    response = client.post(
        "/api/plan/workouts/adopt", json={"days": [an_adopted_day()]}
    )

    assert response.status_code == 200
    body = response.json()
    assert body["created"] == 1 and body["skipped"] == 0
    workout = body["results"][0]["workout"]
    assert workout["sync"]["confirmed_at"] is not None
    assert workout["sync"]["status"] == "not_sent"
    assert calendar.calls == 0


def test_the_description_is_built_from_the_zone_and_the_purpose(
    client: TestClient,
) -> None:
    body = client.post(
        "/api/plan/workouts/adopt", json={"days": [an_adopted_day()]}
    ).json()

    workout = body["results"][0]["workout"]
    assert workout["name"] == "Lockerer Dauerlauf"
    assert workout["description"] == ("Zielzone Z2. Grundlage aufbauen ohne Ermüdung.")
    assert workout["target_time_s"] == 2_400


def test_the_whole_week_is_one_request(client: TestClient) -> None:
    days = [
        an_adopted_day(date=(tomorrow() + dt.timedelta(days=offset)).isoformat())
        for offset in range(5)
    ]

    body = client.post("/api/plan/workouts/adopt", json={"days": days}).json()

    assert body["created"] == 5
    assert all(result["created"] for result in body["results"])


def test_a_day_that_is_taken_does_not_take_the_week_down_with_it(
    client: TestClient, engine: Engine
) -> None:
    """One refused Wednesday must not cost the other six days."""
    clash = tomorrow() + dt.timedelta(days=1)
    taken = propose_workout(engine, a_proposal(date=clash))
    confirm_workout(engine, taken.id)
    days = [
        an_adopted_day(date=(tomorrow() + dt.timedelta(days=offset)).isoformat())
        for offset in range(3)
    ]

    body = client.post("/api/plan/workouts/adopt", json={"days": days}).json()

    assert body["created"] == 2
    assert body["skipped"] == 1
    refused = next(
        result for result in body["results"] if result["date"] == clash.isoformat()
    )
    assert refused["created"] is False
    assert "bestätigte Einheit" in refused["detail"]
    assert refused["workout"] is None


def test_nothing_the_source_owns_is_overwritten_even_with_replace(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        seed_source_event(session, tomorrow())
        session.commit()

    body = client.post(
        "/api/plan/workouts/adopt",
        json={"days": [an_adopted_day()], "replace": True},
    ).json()

    assert body["created"] == 0
    assert body["results"][0]["detail"] is not None


def test_a_day_in_the_past_is_reported_not_created(client: TestClient) -> None:
    long_ago = (dt.datetime.now(tz=dt.UTC).date() - dt.timedelta(days=30)).isoformat()

    body = client.post(
        "/api/plan/workouts/adopt", json={"days": [an_adopted_day(date=long_ago)]}
    ).json()

    assert body["created"] == 0
    assert "Vergangenheit" in body["results"][0]["detail"]


def test_adopting_needs_a_session(configured: Settings, calendar: Calendar) -> None:
    with TestClient(
        create_app(configured, client_factory=calendar.factory()),
        base_url="https://testserver",
    ) as anonymous:
        response = anonymous.post(
            "/api/plan/workouts/adopt", json={"days": [an_adopted_day()]}
        )

    assert response.status_code == 401
