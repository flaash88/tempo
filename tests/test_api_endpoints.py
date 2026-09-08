"""The endpoints, and the contract design/README.md sets for them.

Two things are checked throughout: every metric arrives in the full
confidence envelope, and no response carries a credential — not the key,
not a masked form of it.
"""

from __future__ import annotations

import datetime as dt
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
    Activity,
    ActivityStream,
    AiCall,
    AthleteSettings,
    PlannedWorkout,
    SyncLog,
    SyncStatus,
    WellnessDay,
)
from tempo.db.session import session_factory
from tempo.ingest.intervals_client import IntervalsClient, RetryPolicy
from tempo.metrics.thresholds import (
    MIN_DAYS_FORM,
    MIN_NIGHTS_READINESS,
    PREDICTION_STALE_AFTER_DAYS,
)
from tempo.recompute import recompute_all

PASSWORD = "ein sehr langes Passwort"
# Hashed once for the module: scrypt is deliberately expensive, which is the
# point in production and pure waste once per test.
PASSWORD_HASH = hash_password(PASSWORD)
INTERVALS_KEY = "intervals-secret-key-7f2c"
ANTHROPIC_KEY = "sk-ant-api03-secret-abcd"

ENVELOPE_KEYS = {
    "value",
    "confidence",
    "days_of_history",
    "have",
    "required",
    "available_from",
    "last_data_point",
    "stale",
}


@pytest.fixture
def open_session(engine: Engine) -> Callable[[], Session]:
    return session_factory(engine)


@pytest.fixture
def configured(
    settings: Settings, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Settings:
    monkeypatch.setenv("TEMPO_PASSWORD_HASH", PASSWORD_HASH)
    monkeypatch.setenv("INTERVALS_API_KEY", INTERVALS_KEY)
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    return load_settings()


@pytest.fixture
def client(configured: Settings) -> Iterator[TestClient]:
    with TestClient(
        create_app(configured), base_url="https://testserver"
    ) as test_client:
        test_client.post("/api/auth/login", json={"password": PASSWORD})
        yield test_client


def today() -> dt.date:
    return dt.datetime.now(tz=dt.UTC).date()


def seed_athlete(session: Session) -> None:
    session.add(
        AthleteSettings(
            id=1,
            hr_max=188,
            hr_rest=48,
            lthr=168,
            threshold_pace_s_per_km=330.0,
        )
    )


def seed_run(
    session: Session,
    activity_id: str = "i1",
    *,
    day: dt.date | None = None,
    seconds: int = 1800,
    speed: float = 3.0,
    with_stream: bool = True,
) -> None:
    day = day or today()
    session.add(
        Activity(
            id=activity_id,
            start_local=dt.datetime.combine(day, dt.time(7, 30)),
            sport="Run",
            distance_m=speed * seconds,
            moving_s=seconds,
            elapsed_s=seconds,
            elevation_gain_m=12.0,
            avg_hr=142,
            max_hr=168,
            avg_pace_s_per_km=1000.0 / speed,
            source="intervals",
        )
    )
    if not with_stream:
        return
    for offset in range(seconds):
        session.add(
            ActivityStream(
                activity_id=activity_id,
                offset_s=offset,
                hr=None if offset % 500 == 3 else 140 + offset % 10,
                speed_m_s=speed,
                altitude_m=200.0,
                cadence=82,
                lat=47.07,
                lon=15.44,
            )
        )


def seed_wellness(session: Session, days: int, *, end: dt.date | None = None) -> None:
    end = end or today()
    for offset in range(days):
        session.add(
            WellnessDay(
                date=end - dt.timedelta(days=days - 1 - offset),
                resting_hr=50 + (offset % 4),
                hrv=42.0 + (offset % 6),
                hrv_source_field="hrv",
                sleep_secs=25_200,
                sleep_score=72,
                source="intervals",
            )
        )


def assert_envelope(payload: Any, name: str) -> None:
    assert isinstance(payload, dict), name
    assert set(payload) == ENVELOPE_KEYS, name
    assert 0.0 <= payload["confidence"] <= 1.0, name
    if payload["value"] is None:
        assert payload["confidence"] == 0.0, name


def assert_no_credentials(text: str) -> None:
    assert INTERVALS_KEY not in text
    assert ANTHROPIC_KEY not in text
    assert PASSWORD not in text
    # Not even a prefix long enough to be worth anything.
    assert "sk-ant-api03" not in text


# --- thresholds --------------------------------------------------------


def test_thresholds_carry_every_minimum_history(client: TestClient) -> None:
    """So the frontend hard codes no number, not even as a fallback."""
    payload = client.get("/api/thresholds").json()

    minimums = payload["minimum_history"]
    assert set(minimums) == {"readiness", "hrv_baseline", "form", "critical_speed"}
    assert minimums["readiness"]["required"] == MIN_NIGHTS_READINESS
    assert minimums["form"]["required"] == MIN_DAYS_FORM
    for entry in minimums.values():
        assert entry["unit"] in {"days", "nights", "performances"}
        assert entry["label_de"]

    assert payload["stale_after_days"] == 7
    assert payload["baseline_reset_gap_days"] == 14
    assert (
        payload["performance"]["prediction_stale_after_days"]
        == PREDICTION_STALE_AFTER_DAYS
    )
    assert payload["zones"]["count"] == 5
    assert payload["readiness"]["weights"]["hrv"] == pytest.approx(0.40)


def test_thresholds_report_the_configured_values_and_the_defaults(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        session.add(AthleteSettings(id=1, sleep_target_s=7 * 3600))
        session.commit()

    payload = client.get("/api/thresholds").json()["readiness"]

    assert payload["sleep_target_s"] == 7 * 3600
    assert payload["sleep_target_default_s"] == 8 * 3600
    assert payload["weights_default"]["hrv"] == pytest.approx(0.40)


# --- today -------------------------------------------------------------


def test_today_answers_on_an_empty_database(client: TestClient) -> None:
    """The empty state is a state, not an error."""
    response = client.get("/api/today")

    assert response.status_code == 200
    payload = response.json()
    for name in ("readiness", "hrv", "resting_hr", "sleep", "form", "acwr"):
        assert_envelope(payload[name], name)
        assert payload[name]["value"] is None
    assert payload["planned"] is None
    assert payload["ai_model"]


def test_today_carries_every_field_the_screen_shows(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    """design/README.md: Heute reads GET /api/today."""
    with open_session() as session:
        seed_athlete(session)
        seed_wellness(session, MIN_NIGHTS_READINESS)
        seed_run(session)
        session.add(
            PlannedWorkout(
                id="e1",
                date=today(),
                category="WORKOUT",
                sport="Run",
                name="Lockerer Dauerlauf",
                description="Zone 2",
                target_time_s=2700,
                target_dist_m=8000.0,
                target_load=55.0,
                source="intervals",
            )
        )
        session.commit()
    recompute_all(engine_of(client), today=today())

    payload = client.get("/api/today").json()

    # Bereitschaft, HRV, Ruhepuls, Schlaf, Formzustand — one tile each.
    assert payload["readiness"]["value"] is not None
    assert payload["hrv_latest"] is not None
    assert payload["resting_hr_latest"] is not None
    assert payload["sleep"]["value"]["seconds"] == 25_200
    assert payload["hrv_source_field"] == "hrv"
    # Geplante Einheit, with its target and whether it is done.
    assert payload["planned"]["name"] == "Lockerer Dauerlauf"
    assert payload["planned"]["target_load"] == pytest.approx(55.0)
    assert payload["planned"]["done"] is True
    # The KI footer's model comes from the config, never hard coded.
    assert payload["ai_model"] == "claude-sonnet-5"
    # The sync tile.
    assert "sources" in payload["sync"]
    # What the readiness number is made of, and what it weighed.
    assert set(payload["readiness_components"]) == {
        "hrv",
        "resting_hr",
        "sleep",
        "tsb",
    }
    assert payload["readiness_weights"]["hrv"] == pytest.approx(0.40)


def engine_of(client: TestClient) -> Engine:
    engine: Engine = client.app.state.engine  # type: ignore[attr-defined]
    return engine


# --- activities --------------------------------------------------------


def test_the_activity_list_pages_and_reports_the_total(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        seed_athlete(session)
        for index in range(3):
            seed_run(
                session,
                f"i{index}",
                day=today() - dt.timedelta(days=index),
                seconds=60,
            )
        session.commit()

    payload = client.get("/api/activities", params={"limit": 2}).json()

    assert payload["total"] == 3
    assert len(payload["activities"]) == 2
    assert payload["activities"][0]["id"] == "i0"
    assert payload["activities"][0]["has_stream"] is True


def test_the_activity_detail_carries_what_the_screen_shows(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    """design/README.md: Aktivitätsdetail reads the activity and its streams."""
    with open_session() as session:
        seed_athlete(session)
        seed_run(session, "i1", seconds=2400)
        session.commit()

    payload = client.get("/api/activities/i1").json()

    # Ø Pace, Ø HF, Zeit, Distanz.
    assert payload["avg_pace_s_per_km"] is not None
    assert payload["avg_hr"] == 142
    assert payload["moving_s"] == 2400
    # GAP, TRIMP, hrTSS, Efficiency Factor, Decoupling — each an envelope.
    for name in (
        "trimp",
        "hr_tss",
        "r_tss",
        "gap_pace_s_per_km",
        "efficiency_factor",
        "decoupling",
    ):
        assert_envelope(payload[name], name)
    assert payload["trimp"]["value"] is not None
    assert payload["gap_pace_s_per_km"]["value"] is not None
    # Kadenz, Zonenverteilung.
    assert payload["avg_cadence_spm"] == 82
    assert len(payload["zones"]) == 5
    assert payload["zone_bounds"]["model"] == "friel_run_lthr"
    assert payload["seconds_unknown"] > 0


def test_a_missing_activity_is_a_404(client: TestClient) -> None:
    assert client.get("/api/activities/nope").status_code == 404
    assert client.get("/api/activities/nope/streams").status_code == 404


def test_streams_keep_gaps_as_nulls(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        seed_athlete(session)
        seed_run(session, "i1", seconds=600)
        session.commit()

    payload = client.get(
        "/api/activities/i1/streams", params={"fields": "hr,speed_m_s"}
    ).json()

    assert payload["fields"] == ["hr", "speed_m_s"]
    assert payload["samples"] == 600
    assert payload["resolution_s"] == 1
    assert None in payload["series"]["hr"]
    assert len(payload["offset_s"]) == 600


def test_a_coarser_resolution_decimates_rather_than_smooths(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    """Averaging a bucket would invent a reading and close a real gap."""
    with open_session() as session:
        seed_athlete(session)
        seed_run(session, "i1", seconds=600)
        session.commit()

    payload = client.get(
        "/api/activities/i1/streams",
        params={"fields": "speed_m_s", "resolution": 10},
    ).json()

    assert payload["resolution_s"] == 10
    assert payload["samples"] == 60
    assert payload["offset_s"][:3] == [0, 10, 20]


def test_an_unknown_stream_field_is_refused(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        seed_run(session, "i1", seconds=10)
        session.commit()

    response = client.get("/api/activities/i1/streams", params={"fields": "heartrate"})

    assert response.status_code == 400
    assert "heartrate" in response.json()["detail"]


# --- trends ------------------------------------------------------------


def test_trends_answer_for_each_window(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        seed_athlete(session)
        seed_wellness(session, 30)
        seed_run(session, "i1", seconds=1200)
        session.commit()
    recompute_all(engine_of(client), today=today())

    for window in ("6w", "12w", "52w"):
        payload = client.get("/api/trends", params={"window": window}).json()
        assert payload["window"] == window
        for name in ("hrv", "resting_hr", "vo2max", "vdot", "monotony", "strain"):
            assert_envelope(payload[name], f"{window}:{name}")


def test_trends_carry_what_the_screen_shows(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    """design/README.md: Trends reads /api/trends and /api/performance."""
    with open_session() as session:
        seed_athlete(session)
        seed_wellness(session, 40)
        seed_run(session, "i1", seconds=1800)
        session.commit()
    recompute_all(engine_of(client), today=today())

    payload = client.get("/api/trends", params={"window": "12w"}).json()

    # CTL / ATL / TSB series.
    assert payload["fitness"]
    assert {"date", "ctl", "atl", "tsb", "confidence"} <= set(payload["fitness"][0])
    # HRV-Baseline with its band.
    assert payload["hrv"]["value"] is not None
    assert payload["hrv_series"]
    assert payload["hrv_series"][-1]["upper"] > payload["hrv_series"][-1]["mean"]
    assert payload["hrv_source_field"] == "hrv"
    # Wochenvolumen und Zonenanteile.
    assert payload["weeks"]
    assert len(payload["weeks"][0]["zones"]) == 5
    assert payload["average_week_distance_m"] is not None


def test_an_unknown_window_is_refused(client: TestClient) -> None:
    assert client.get("/api/trends", params={"window": "3w"}).status_code == 422


# --- performance -------------------------------------------------------


def test_performance_reports_both_prediction_methods(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        seed_athlete(session)
        seed_run(session, "i1", seconds=1800, speed=3.0)
        session.commit()

    payload = client.get("/api/performance").json()

    assert payload["best_efforts"]
    assert_envelope(payload["critical_speed"], "critical_speed")
    assert payload["critical_speed"]["value"]["cs_pace_s_per_km"] > 0
    assert_envelope(payload["vdot"], "vdot")
    for name, race in payload["predictions"].items():
        methods = {entry["method"] for entry in race["predictions"]}
        assert methods == {"riegel", "vdot"}, name
        assert race["distance_m"] > 0


def test_a_fresh_best_effort_makes_fresh_predictions(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        seed_athlete(session)
        seed_run(session, "i1", seconds=1800, day=today() - dt.timedelta(days=10))
        session.commit()

    payload = client.get("/api/performance").json()

    assert payload["predictions_stale"] is False
    assert all(race["stale"] is False for race in payload["predictions"].values())


def test_an_old_best_effort_makes_the_predictions_stale(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    """A spring result is not this autumn's expectation."""
    old_day = today() - dt.timedelta(days=PREDICTION_STALE_AFTER_DAYS + 1)
    with open_session() as session:
        seed_athlete(session)
        seed_run(session, "i1", seconds=1800, day=old_day)
        session.commit()

    payload = client.get("/api/performance").json()

    assert payload["predictions_stale"] is True
    assert payload["reference_date"] == old_day.isoformat()
    assert payload["prediction_stale_after_days"] == PREDICTION_STALE_AFTER_DAYS
    for race in payload["predictions"].values():
        assert race["stale"] is True
        assert race["based_on_date"] == old_day.isoformat()
    # Still delivered: a personal best does not stop being a fact.
    assert payload["best_efforts"]


def test_the_staleness_boundary_is_the_configured_horizon(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    on_the_edge = today() - dt.timedelta(days=PREDICTION_STALE_AFTER_DAYS)
    with open_session() as session:
        seed_athlete(session)
        seed_run(session, "i1", seconds=1800, day=on_the_edge)
        session.commit()

    assert client.get("/api/performance").json()["predictions_stale"] is False


def test_performance_on_an_empty_database_is_empty_not_broken(
    client: TestClient,
) -> None:
    payload = client.get("/api/performance").json()

    assert payload["best_efforts"] == []
    assert payload["predictions"] == {}
    assert payload["critical_speed"]["value"] is None


# --- plan --------------------------------------------------------------


def test_the_plan_defaults_to_the_current_week(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    monday = today() - dt.timedelta(days=today().weekday())
    with open_session() as session:
        session.add(
            PlannedWorkout(
                id="e1",
                date=monday,
                category="WORKOUT",
                sport="Run",
                name="Intervalle",
                target_time_s=3600,
                target_dist_m=12_000.0,
                target_load=80.0,
                source="intervals",
            )
        )
        session.add(
            PlannedWorkout(
                id="r1",
                date=monday + dt.timedelta(days=60),
                category="RACE_A",
                sport="Run",
                name="Halbmarathon München",
                source="intervals",
            )
        )
        session.commit()

    payload = client.get("/api/plan").json()

    assert payload["from_date"] == monday.isoformat()
    assert len(payload["workouts"]) == 1
    assert payload["planned_distance_m"] == pytest.approx(12_000.0)
    assert payload["planned_duration_s"] == 3600
    assert payload["planned_load"] == pytest.approx(80.0)
    # The race is outside the default week.
    assert payload["races"] == []


def test_a_target_race_is_kept_apart_from_the_sessions(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        session.add(
            PlannedWorkout(
                id="r1",
                date=today(),
                category="RACE_A",
                sport="Run",
                name="Halbmarathon München",
                source="intervals",
            )
        )
        session.commit()

    payload = client.get("/api/plan").json()

    assert payload["workouts"] == []
    assert payload["races"][0]["name"] == "Halbmarathon München"


def test_a_reversed_range_is_refused(client: TestClient) -> None:
    response = client.get(
        "/api/plan",
        params={"from_date": "2026-09-10", "to_date": "2026-09-01"},
    )

    assert response.status_code == 400


# --- settings ----------------------------------------------------------


def test_settings_never_return_a_key_not_even_masked(
    client: TestClient,
) -> None:
    """The mock-up draws the dots; the API sends valid and last4."""
    response = client.get("/api/settings")

    assert_no_credentials(response.text)
    payload = response.json()
    assert payload["intervals"] == {"valid": True, "last4": "7f2c"}
    assert payload["anthropic"] == {"valid": True, "last4": "abcd"}


def test_settings_carry_what_the_screen_shows(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    """design/README.md: Einstellungen reads /api/settings and /api/sync/status."""
    with open_session() as session:
        seed_athlete(session)
        session.add(
            AiCall(
                ts=dt.datetime.now(tz=dt.UTC),
                endpoint="daily",
                model="claude-sonnet-5",
                input_tokens=1200,
                output_tokens=300,
                cost_eur=0.02,
            )
        )
        session.commit()

    payload = client.get("/api/settings").json()

    # HFmax, LTHR, Schwellenpace, Zonenmodell und die Zonengrenzen.
    assert payload["hr_max"] == 188
    assert payload["lthr"] == 168
    assert payload["threshold_pace_s_per_km"] == pytest.approx(330.0)
    assert payload["zone_model"] == "friel_run_lthr"
    assert len(payload["hr_zones"]["lower_bounds"]) == 5
    assert len(payload["pace_zones"]["lower_bounds"]) == 5
    # Modell und Verbrauch, gegen das Budget.
    assert payload["ai_model_daily"] == "claude-sonnet-5"
    assert payload["ai_usage"]["input_tokens"] == 1200
    assert payload["ai_usage"]["cost_eur"] == pytest.approx(0.02)
    assert payload["ai_usage"]["budget_eur"] > 0
    # Datenquellen.
    assert payload["garmin_direct_enabled"] is False
    assert "sources" in payload["sync"]


def test_settings_can_be_changed(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    response = client.put(
        "/api/settings",
        json={"hr_max": 190, "lthr": 170, "sleep_target_s": 27_000},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["hr_max"] == 190
    assert payload["lthr"] == 170
    assert payload["sleep_target_s"] == 27_000
    assert payload["updated_at"] is not None

    with open_session() as session:
        stored = session.get(AthleteSettings, 1)
    assert stored is not None
    assert stored.hr_max == 190


def test_a_partial_update_leaves_the_rest_alone(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        seed_athlete(session)
        session.commit()

    payload = client.put("/api/settings", json={"hr_rest": 46}).json()

    assert payload["hr_rest"] == 46
    assert payload["hr_max"] == 188
    assert payload["lthr"] == 168


@pytest.mark.parametrize(
    "body",
    [
        {"hr_max": 20},
        {"hr_rest": 500},
        {"threshold_pace_s_per_km": 10},
        {"zone_model": "erfunden"},
        {"intervals_api_key": "abc"},
        {"anthropic": {"valid": True}},
    ],
)
def test_an_unusable_settings_update_is_refused(
    client: TestClient, body: dict[str, Any]
) -> None:
    """Credentials among them: keys live in the environment, not in a PUT."""
    assert client.put("/api/settings", json=body).status_code == 422


# --- sync --------------------------------------------------------------


def test_the_sync_status_is_readable_before_any_run(client: TestClient) -> None:
    payload = client.get("/api/sync/status").json()

    assert payload["sources"] == []
    assert payload["next_allowed_at"] is None


def test_starting_a_sync_is_accepted(configured: Settings) -> None:
    """The transport is injected, so the run cannot reach the network."""
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json=[])

    def factory(_settings: Settings) -> IntervalsClient:
        return IntervalsClient(
            INTERVALS_KEY,
            transport=httpx.MockTransport(handler),
            retry=RetryPolicy(max_attempts=1),
            sleeper=lambda _seconds: None,
        )

    with TestClient(
        create_app(configured, client_factory=factory),
        base_url="https://testserver",
    ) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})

        response = client.post("/api/sync")

    assert response.status_code == 202
    assert response.json()["started"] is True
    # The background task ran, against the mock and nothing else.
    assert any(path.endswith("/activities") for path in calls)


def test_a_second_sync_while_one_runs_is_refused(
    configured: Settings, engine: Engine, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        session.add(
            SyncLog(
                source="intervals",
                started_at=dt.datetime.now(tz=dt.UTC),
                status=SyncStatus.RUNNING,
            )
        )
        session.commit()

    with TestClient(create_app(configured), base_url="https://testserver") as client:
        client.post("/api/auth/login", json={"password": PASSWORD})

        assert client.post("/api/sync").status_code == 409


def test_a_sync_without_credentials_is_refused(
    settings: Settings, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("TEMPO_PASSWORD_HASH", PASSWORD_HASH)
    monkeypatch.delenv("INTERVALS_API_KEY", raising=False)
    with TestClient(
        create_app(load_settings()), base_url="https://testserver"
    ) as client:
        client.post("/api/auth/login", json={"password": PASSWORD})

        response = client.post("/api/sync")

    assert response.status_code == 400
    assert "INTERVALS_API_KEY" in response.json()["detail"]


# --- the envelope everywhere -------------------------------------------


def test_every_metric_in_every_answer_carries_its_evidence(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    """The contract for the whole API, checked across it rather than per route."""
    with open_session() as session:
        seed_athlete(session)
        seed_wellness(session, 30)
        seed_run(session, "i1", seconds=1800)
        session.commit()
    recompute_all(engine_of(client), today=today())

    for path in (
        "/api/today",
        "/api/trends",
        "/api/performance",
        "/api/activities/i1",
    ):
        payload = client.get(path).json()
        envelopes = [
            (name, value)
            for name, value in payload.items()
            if isinstance(value, dict) and "confidence" in value
        ]
        assert envelopes, path
        for name, value in envelopes:
            assert_envelope(value, f"{path}:{name}")


def test_no_answer_anywhere_carries_a_credential(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        seed_athlete(session)
        seed_wellness(session, 10)
        seed_run(session, "i1", seconds=120)
        session.commit()

    for path in (
        "/api/today",
        "/api/trends",
        "/api/performance",
        "/api/plan",
        "/api/settings",
        "/api/thresholds",
        "/api/sync/status",
        "/api/activities",
        "/api/activities/i1",
        "/api/activities/i1/streams",
        "/health",
    ):
        assert_no_credentials(client.get(path).text)


# --- subjective daily form ---------------------------------------------


def test_today_carries_the_subjective_values_raw(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    """Uninterpreted, with the source that recorded them."""
    with open_session() as session:
        seed_wellness(session, 3)
        day = session.get(WellnessDay, today())
        assert day is not None
        day.fatigue = 2
        day.soreness = 4
        day.mood = 1
        day.subjective_source = "intervals"
        session.commit()

    payload = client.get("/api/today").json()["subjective"]

    assert payload["fatigue"] == 2
    assert payload["soreness"] == 4
    assert payload["mood"] == 1
    assert payload["source"] == "intervals"
    assert payload["date"] == today().isoformat()


def test_today_says_nothing_entered_rather_than_nothing_at_all(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        seed_wellness(session, 1)
        session.commit()

    payload = client.get("/api/today").json()["subjective"]

    assert payload is not None
    assert payload["fatigue"] is None
    assert payload["source"] is None


def test_a_day_without_a_row_has_no_subjective_block(client: TestClient) -> None:
    assert client.get("/api/today").json()["subjective"] is None


def test_the_athlete_can_record_how_a_day_felt(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    response = client.put(
        f"/api/wellness/{today().isoformat()}",
        json={"fatigue": 3, "soreness": 2, "mood": 4},
    )

    assert response.status_code == 200
    payload = response.json()
    assert (payload["fatigue"], payload["soreness"], payload["mood"]) == (3, 2, 4)
    assert payload["source"] == "manual"

    with open_session() as session:
        row = session.get(WellnessDay, today())
    assert row is not None
    assert row.fatigue == 3
    assert row.subjective_source == "manual"


def test_an_entry_creates_the_day_when_the_watch_has_nothing_to_say(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    day = today() - dt.timedelta(days=3)

    client.put(f"/api/wellness/{day.isoformat()}", json={"mood": 5})

    with open_session() as session:
        row = session.get(WellnessDay, day)
    assert row is not None
    assert row.mood == 5
    assert row.hrv is None


def test_one_field_leaves_the_others_alone(client: TestClient) -> None:
    client.put(
        f"/api/wellness/{today().isoformat()}",
        json={"fatigue": 3, "soreness": 2, "mood": 4},
    )

    payload = client.put(
        f"/api/wellness/{today().isoformat()}", json={"mood": 1}
    ).json()

    assert payload["mood"] == 1
    assert payload["fatigue"] == 3
    assert payload["soreness"] == 2


def test_a_field_sent_as_null_is_cleared(client: TestClient) -> None:
    client.put(f"/api/wellness/{today().isoformat()}", json={"fatigue": 3})

    payload = client.put(
        f"/api/wellness/{today().isoformat()}", json={"fatigue": None}
    ).json()

    assert payload["fatigue"] is None


def test_an_entry_does_not_disturb_the_measured_values(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        seed_wellness(session, 1)
        session.commit()

    client.put(f"/api/wellness/{today().isoformat()}", json={"fatigue": 2})

    with open_session() as session:
        row = session.get(WellnessDay, today())
    assert row is not None
    assert row.hrv is not None
    assert row.resting_hr is not None
    assert row.source == "intervals"
    assert row.subjective_source == "manual"


def test_a_sync_without_subjective_values_keeps_the_athletes_entry(
    client: TestClient, open_session: Callable[[], Session]
) -> None:
    """A hand-written note is not erased by a sync that says nothing about it."""
    from tempo.ingest.intervals_client import WellnessValues
    from tempo.ingest.store import upsert_wellness

    client.put(f"/api/wellness/{today().isoformat()}", json={"fatigue": 2})

    with open_session() as session:
        upsert_wellness(
            session, [WellnessValues(date=today(), resting_hr=51, hrv=44.0)]
        )
        session.commit()
        row = session.get(WellnessDay, today())

    assert row is not None
    assert row.fatigue == 2
    assert row.subjective_source == "manual"
    assert row.resting_hr == 51


@pytest.mark.parametrize(
    "body",
    [
        {"fatigue": 0},
        {"fatigue": 11},
        {"mood": -1},
        {"motivation": 3},
        {"fatigue": "hoch"},
    ],
)
def test_an_implausible_entry_is_refused(
    client: TestClient, body: dict[str, Any]
) -> None:
    """A plausibility bound, not a claim about the scale."""
    response = client.put(f"/api/wellness/{today().isoformat()}", json=body)

    assert response.status_code == 422


def test_an_empty_body_says_so(client: TestClient) -> None:
    response = client.put(f"/api/wellness/{today().isoformat()}", json={})

    assert response.status_code == 400
    assert "Kein Feld" in response.json()["detail"]


def test_a_day_in_the_future_cannot_be_rated(client: TestClient) -> None:
    tomorrow = today() + dt.timedelta(days=1)

    response = client.put(f"/api/wellness/{tomorrow.isoformat()}", json={"mood": 3})

    assert response.status_code == 400


def test_recording_a_day_needs_a_session(configured: Settings) -> None:
    with TestClient(create_app(configured), base_url="https://testserver") as anonymous:
        response = anonymous.put(
            f"/api/wellness/{today().isoformat()}", json={"mood": 3}
        )

    assert response.status_code == 401
