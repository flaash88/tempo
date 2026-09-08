"""The AI layer: what is sent, what it costs, and what is never sent.

Nothing here touches the network. The Anthropic client is built over an
``httpx.MockTransport``, which lets the real request-building and
response-parsing code run while the socket ban in ``conftest`` guarantees
that a mistake in the wiring fails loudly instead of dialling out.
"""

from __future__ import annotations

import datetime as dt
import json
from collections.abc import Callable, Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tempo.ai import prompts
from tempo.ai.budget import USD_PER_EUR, budget_state, record_call
from tempo.ai.client import AnthropicClient, RetryPolicy, Usage
from tempo.ai.errors import AiApiError, AiAuthError, AiRateLimited, BudgetExceeded
from tempo.ai.features import (
    MAX_FEATURE_BYTES,
    MAX_RECENT_ACTIVITIES,
    activity_block,
    build_features,
)
from tempo.ai.service import answer, model_for
from tempo.api.app import create_app
from tempo.api.auth import hash_password
from tempo.config import Settings, load_settings
from tempo.db.models import (
    Activity,
    ActivityStream,
    AiCall,
    AiResponse,
    AthleteSettings,
    WellnessDay,
)
from tempo.db.session import session_factory
from tempo.recompute import recompute_all

PASSWORD = "ein sehr langes Passwort"
PASSWORD_HASH = hash_password(PASSWORD)
ANTHROPIC_KEY = "sk-ant-api03-secret-abcd"

# What the mocked model says. Deliberately a sentence that names what is
# missing instead of describing a development.
CAUTIOUS_ANSWER = (
    "Für eine Aussage über eine Entwicklung fehlen die Daten: die "
    "Bereitschaft braucht 14 Nächte, vorliegen 5. Bis dahin bleibt es bei "
    "einer lockeren Einheit, 30 min in Zone 2, zum Wiedereinstieg."
)


def today() -> dt.date:
    return dt.datetime.now(tz=dt.UTC).date()


# --- a mocked Anthropic ------------------------------------------------


class Recorder:
    """Captures every request that would have gone out."""

    def __init__(
        self,
        *,
        text: str = CAUTIOUS_ANSWER,
        input_tokens: int = 1_200,
        output_tokens: int = 180,
        cache_read: int = 0,
        status_code: int = 200,
    ) -> None:
        self.requests: list[dict[str, Any]] = []
        self.text = text
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read = cache_read
        self.status_code = status_code

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(json.loads(request.content))
        if self.status_code != 200:
            return httpx.Response(self.status_code, json={"error": "nope"})
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": self.text}],
                "model": self.requests[-1]["model"],
                "stop_reason": "end_turn",
                "usage": {
                    "input_tokens": self.input_tokens,
                    "output_tokens": self.output_tokens,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": self.cache_read,
                },
            },
        )

    def factory(self, settings: Settings) -> AnthropicClient:
        return AnthropicClient(
            settings.anthropic_api_key.get_secret_value() or "sk-ant-test",
            transport=httpx.MockTransport(self.handle),
            retry=RetryPolicy(max_attempts=2, initial_backoff_s=0.0),
            sleeper=lambda _seconds: None,
        )

    @property
    def calls(self) -> int:
        return len(self.requests)

    @property
    def last_system(self) -> str:
        return "\n\n".join(
            block["text"] for block in self.requests[-1]["system"] if "text" in block
        )

    @property
    def last_features(self) -> dict[str, Any]:
        text = self.requests[-1]["messages"][0]["content"]
        payload = text.split("Kennzahlen:\n", 1)[1]
        payload = payload.split("\n\nFrage des Athleten:", 1)[0]
        result: dict[str, Any] = json.loads(payload)
        return result


@pytest.fixture
def recorder() -> Recorder:
    return Recorder()


@pytest.fixture
def open_session(engine: Engine) -> Callable[[], Session]:
    return session_factory(engine)


@pytest.fixture
def configured(
    settings: Settings, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> Settings:
    monkeypatch.setenv("TEMPO_PASSWORD_HASH", PASSWORD_HASH)
    monkeypatch.setenv("ANTHROPIC_API_KEY", ANTHROPIC_KEY)
    monkeypatch.setenv("ANTHROPIC_MODEL_DAILY", "claude-sonnet-5")
    monkeypatch.setenv("ANTHROPIC_MODEL_PLANNING", "claude-opus-5")
    monkeypatch.setenv("TEMPO_MONTHLY_BUDGET_EUR", "5")
    return load_settings()


@pytest.fixture
def client(configured: Settings, recorder: Recorder) -> Iterator[TestClient]:
    with TestClient(
        create_app(configured, ai_client_factory=recorder.factory),
        base_url="https://testserver",
    ) as test_client:
        test_client.post("/api/auth/login", json={"password": PASSWORD})
        yield test_client


# --- seeding -----------------------------------------------------------


def seed_athlete(session: Session) -> None:
    session.add(
        AthleteSettings(
            id=1, hr_max=188, hr_rest=48, lthr=168, threshold_pace_s_per_km=330.0
        )
    )


def seed_run(
    session: Session,
    activity_id: str,
    *,
    day: dt.date,
    seconds: int = 1800,
    speed: float = 3.0,
    with_stream: bool = False,
) -> None:
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
                hr=141 + offset % 8,
                speed_m_s=speed,
                altitude_m=200.0,
                cadence=82,
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
                source="intervals",
            )
        )


# --- the feature document ----------------------------------------------


def test_feature_document_stays_under_the_size_limit(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    """A year of training still has to fit in a page of numbers."""
    with open_session() as session:
        seed_athlete(session)
        seed_wellness(session, 120)
        for index in range(80):
            seed_run(session, f"a{index}", day=today() - dt.timedelta(days=index))
        session.commit()
    recompute_all(engine)

    document = build_features(engine)

    assert document.size_bytes <= MAX_FEATURE_BYTES
    assert not document.trimmed


def test_feature_document_carries_no_stream_and_a_capped_activity_list(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    """The per-second recording never reaches the context window."""
    with open_session() as session:
        seed_athlete(session)
        for index in range(30):
            seed_run(
                session,
                f"a{index}",
                day=today() - dt.timedelta(days=index),
                with_stream=index == 0,
            )
        session.commit()
    recompute_all(engine)

    document = build_features(engine)

    assert len(document.payload["recent_activities"]) <= MAX_RECENT_ACTIVITIES
    for marker in ("offset_s", "speed_m_s", "altitude_m", "cadence"):
        assert marker not in document.json_text


def test_every_metric_carries_its_confidence_metadata(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    """A number the app will not show is a number the model must not read."""
    with open_session() as session:
        seed_athlete(session)
        seed_wellness(session, 5)
        session.commit()
    recompute_all(engine)

    metrics = build_features(engine).payload["metrics"]

    assert metrics
    for name, entry in metrics.items():
        assert set(entry) >= {"value", "confidence", "have", "required"}, name
        if entry["value"] is None and entry["required"]:
            assert entry["have"] < entry["required"], name


def test_activity_block_summarises_one_session_without_its_stream(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        seed_athlete(session)
        seed_run(session, "i1", day=today(), with_stream=True)
        session.commit()
    recompute_all(engine)

    block = activity_block(engine, "i1")

    assert block is not None
    assert block["activity"]["id"] == "i1"
    assert "offset_s" not in json.dumps(block)
    assert activity_block(engine, "does-not-exist") is None


# --- the system prompt -------------------------------------------------


@pytest.mark.parametrize("task", sorted(prompts.TASKS))
def test_system_prompt_carries_the_four_required_rules(task: str) -> None:
    """Each rule is required by docs/PLAN.md; none may be edited away."""
    text = "\n\n".join(block["text"] for block in prompts.system_blocks(task))

    assert prompts.RETURNING_ATHLETE in text
    assert prompts.CONFIDENCE_RULE in text
    assert prompts.NO_MEDICAL_ADVICE in text
    assert prompts.CONCRETE_RECOMMENDATIONS in text


def test_system_prompt_marks_the_stable_part_as_cacheable() -> None:
    blocks = prompts.system_blocks("daily", athlete_profile={"configured": False})

    assert blocks[0]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in blocks[-1]
    assert prompts.system_blocks("daily", cache=False)[0].get("cache_control") is None


def test_unknown_task_is_refused() -> None:
    with pytest.raises(ValueError, match="unknown task"):
        prompts.system_blocks("freestyle")


# --- the client --------------------------------------------------------


def test_client_parses_text_and_usage(configured: Settings) -> None:
    recorder = Recorder(input_tokens=10, output_tokens=20, cache_read=5)

    with recorder.factory(configured) as client:
        completion = client.complete(
            model="claude-sonnet-5",
            system=prompts.system_blocks("daily"),
            messages=[prompts.user_message("{}")],
        )

    assert completion.text == CAUTIOUS_ANSWER
    assert completion.usage.output_tokens == 20
    assert completion.usage.cache_read_input_tokens == 5


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [(401, AiAuthError), (429, AiRateLimited), (500, AiApiError), (400, AiApiError)],
)
def test_client_maps_status_codes(
    configured: Settings, status_code: int, expected: type[Exception]
) -> None:
    recorder = Recorder(status_code=status_code)

    with recorder.factory(configured) as client, pytest.raises(expected):
        client.complete(
            model="claude-sonnet-5",
            system=prompts.system_blocks("daily"),
            messages=[prompts.user_message("{}")],
        )


def test_client_refuses_without_a_key() -> None:
    with pytest.raises(AiAuthError):
        AnthropicClient("")


# --- the budget --------------------------------------------------------


def test_budget_is_the_sum_of_the_calendar_month(engine: Engine) -> None:
    record_call(
        engine,
        endpoint="daily",
        model="claude-sonnet-5",
        usage=Usage(input_tokens=1_000_000, output_tokens=0),
        usd_per_eur=2.0,
    )

    state = budget_state(engine, budget_eur=5.0)

    # A million input tokens of Sonnet is $2.00, which at two dollars per
    # euro is one euro.
    assert state.spent_eur == pytest.approx(1.0)
    assert state.remaining_eur == pytest.approx(4.0)
    assert not state.exhausted


def test_budget_stop_makes_no_call(
    engine: Engine,
    configured: Settings,
    recorder: Recorder,
    open_session: Callable[[], Session],
) -> None:
    """A reached limit is a refusal, not a smaller request."""
    with open_session() as session:
        session.add(
            AiCall(
                ts=dt.datetime.now(tz=dt.UTC),
                endpoint="daily",
                model="claude-opus-5",
                input_tokens=0,
                output_tokens=0,
                cost_eur=configured.monthly_budget_eur,
            )
        )
        session.commit()

    with pytest.raises(BudgetExceeded) as raised:
        answer(engine, configured, task="daily", client_factory=recorder.factory)

    assert recorder.calls == 0
    assert raised.value.budget_eur == configured.monthly_budget_eur


def test_budget_of_zero_permits_nothing(
    engine: Engine, configured: Settings, recorder: Recorder
) -> None:
    spent_nothing = configured.model_copy(update={"monthly_budget_eur": 0.0})

    with pytest.raises(BudgetExceeded):
        answer(engine, spent_nothing, task="daily", client_factory=recorder.factory)

    assert recorder.calls == 0


def test_endpoint_reports_the_spent_budget_as_a_defined_state(
    client: TestClient, recorder: Recorder, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        session.add(
            AiCall(
                ts=dt.datetime.now(tz=dt.UTC),
                endpoint="daily",
                model="claude-opus-5",
                input_tokens=0,
                output_tokens=0,
                cost_eur=99.0,
            )
        )
        session.commit()

    response = client.post("/api/ai/daily")

    assert response.status_code == 402
    body = response.json()["detail"]
    assert body["budget"]["exhausted"] is True
    assert body["budget"]["remaining_eur"] == 0.0
    assert "Monatsbudget" in body["detail"]
    assert recorder.calls == 0


# --- the service -------------------------------------------------------


def test_answer_records_the_call_and_caches_the_result(
    engine: Engine,
    configured: Settings,
    recorder: Recorder,
    open_session: Callable[[], Session],
) -> None:
    with open_session() as session:
        seed_athlete(session)
        seed_wellness(session, 30)
        session.commit()
    recompute_all(engine)

    first = answer(engine, configured, task="daily", client_factory=recorder.factory)
    second = answer(engine, configured, task="daily", client_factory=recorder.factory)

    assert first is not None and second is not None
    assert recorder.calls == 1
    assert first.cached is False
    assert second.cached is True
    assert second.text == first.text

    with open_session() as session:
        calls = list(session.scalars(select(AiCall)))
        stored = list(session.scalars(select(AiResponse)))
    assert len(calls) == 1
    assert calls[0].endpoint == "daily"
    assert calls[0].cost_eur > 0.0
    assert len(stored) == 1
    assert stored[0].answer == CAUTIOUS_ANSWER


def test_refresh_asks_again(
    engine: Engine,
    configured: Settings,
    recorder: Recorder,
    open_session: Callable[[], Session],
) -> None:
    with open_session() as session:
        seed_athlete(session)
        seed_wellness(session, 30)
        session.commit()
    recompute_all(engine)

    answer(engine, configured, task="daily", client_factory=recorder.factory)
    again = answer(
        engine, configured, task="daily", client_factory=recorder.factory, refresh=True
    )

    assert again is not None and again.cached is False
    assert recorder.calls == 2
    with open_session() as session:
        assert len(list(session.scalars(select(AiResponse)))) == 1


def test_the_model_comes_from_the_configuration(configured: Settings) -> None:
    assert model_for("daily", configured) == "claude-sonnet-5"
    assert model_for("chat", configured) == "claude-sonnet-5"
    assert model_for("plan_week", configured) == "claude-opus-5"

    other = configured.model_copy(
        update={
            "anthropic_model_daily": "claude-haiku-4-5",
            "anthropic_model_planning": "claude-sonnet-5",
        }
    )
    assert model_for("daily", other) == "claude-haiku-4-5"
    assert model_for("plan_week", other) == "claude-sonnet-5"


def test_plan_week_uses_the_planning_model(
    client: TestClient, recorder: Recorder
) -> None:
    response = client.post("/api/ai/plan-week")

    assert response.status_code == 200
    assert recorder.requests[-1]["model"] == "claude-opus-5"
    assert response.json()["model"] == "claude-opus-5"


def test_chat_carries_the_question_and_nothing_else(
    client: TestClient, recorder: Recorder
) -> None:
    response = client.post("/api/ai/chat", json={"question": "Wie war meine Woche?"})

    assert response.status_code == 200
    content = recorder.requests[-1]["messages"][0]["content"]
    assert "Wie war meine Woche?" in content
    assert len(recorder.requests[-1]["messages"]) == 1


def test_activity_endpoint_answers_404_for_an_unknown_session(
    client: TestClient, recorder: Recorder
) -> None:
    response = client.post("/api/ai/activity/nope")

    assert response.status_code == 404
    assert recorder.calls == 0


def test_answers_require_a_session(configured: Settings, recorder: Recorder) -> None:
    with TestClient(
        create_app(configured, ai_client_factory=recorder.factory),
        base_url="https://testserver",
    ) as anonymous:
        assert anonymous.post("/api/ai/daily").status_code == 401
    assert recorder.calls == 0


def test_no_response_carries_the_key(client: TestClient) -> None:
    for path in ("/api/ai/daily", "/api/ai/budget"):
        response = client.post(path) if path.endswith("daily") else client.get(path)
        assert ANTHROPIC_KEY not in response.text
        assert "sk-ant-api03" not in response.text


# --- the state the app is actually in ----------------------------------


def test_null_metrics_with_progress_claim_no_trend(
    client: TestClient, recorder: Recorder, open_session: Callable[[], Session]
) -> None:
    """The mandatory case: five nights of data and nothing else.

    Every published metric is below its minimum history. What the model is
    given must say so — a null value, the progress towards the minimum, and
    the rule that forbids reading either as a development — and what comes
    back must reach the interface unchanged, with no interpretation added
    on the way.
    """
    with open_session() as session:
        seed_athlete(session)
        seed_wellness(session, 5)
        session.commit()

    response = client.post("/api/ai/daily")

    assert response.status_code == 200
    assert recorder.calls == 1

    # 1. Nothing that could be read as a trend was sent. A single night's
    #    sleep is exempt: it is one measurement of one night, needs no
    #    history, and is not a development of anything.
    metrics = recorder.last_features["metrics"]
    derived = {name: entry for name, entry in metrics.items() if entry["required"] > 1}
    assert set(derived) == {
        "readiness",
        "form",
        "acwr",
        "hrv_baseline",
        "resting_hr_baseline",
    }
    for name, entry in derived.items():
        assert entry["value"] is None, name
        assert entry["confidence"] == 0.0, name
        assert entry["have"] < entry["required"], name
        assert "available_from" in entry, name
    readiness = metrics["readiness"]
    assert readiness["have"] == 5
    assert readiness["required"] == 14
    assert "available_from" in readiness

    # 2. The rule against reading it as one travelled with it.
    assert prompts.CONFIDENCE_RULE in recorder.last_system
    assert prompts.RETURNING_ATHLETE in recorder.last_system

    # 3. The answer is passed through as written.
    body = response.json()
    assert body["text"] == CAUTIOUS_ANSWER
    assert body["features_bytes"] <= MAX_FEATURE_BYTES
    assert body["cached"] is False


def test_the_real_data_situation_produces_a_document(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    """Three wellness blocks with gaps, one short run, nothing since.

    The shape of the athlete's actual history: the document must be built
    from it without anything averaging across a gap, and every metric must
    come out unavailable rather than confident.
    """
    with open_session() as session:
        seed_athlete(session)
        for end, length in (
            (dt.date(2025, 9, 24), 24),
            (dt.date(2026, 1, 25), 25),
            (dt.date(2026, 5, 16), 16),
        ):
            seed_wellness(session, length, end=end)
        seed_run(session, "i1", day=dt.date(2026, 1, 15), seconds=1200, speed=2.9)
        session.commit()
    recompute_all(engine, today=dt.date(2026, 9, 8))

    document = build_features(engine, as_of=dt.date(2026, 9, 8))

    assert document.size_bytes <= MAX_FEATURE_BYTES
    for name, entry in document.payload["metrics"].items():
        if entry["required"] > 1:
            assert entry["value"] is None, name
    assert document.payload["last_wellness_day"] == "2026-05-16"


def test_usd_per_eur_is_positive() -> None:
    assert USD_PER_EUR > 0
