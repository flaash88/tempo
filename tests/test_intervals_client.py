"""intervals.icu client against a mocked transport.

No test here touches the network. httpx.MockTransport answers every
request, so a regression that reintroduces a live call fails loudly
instead of quietly reaching out.
"""

from __future__ import annotations

import base64
import datetime as dt
from pathlib import Path
from typing import Any

import httpx
import pytest

from tempo.ingest.errors import (
    IngestError,
    IntervalsApiError,
    IntervalsAuthError,
    RateLimited,
)
from tempo.ingest.intervals_client import (
    BASIC_AUTH_USERNAME,
    IntervalsClient,
    RetryPolicy,
    to_activity_summary,
    to_wellness_values,
)

API_KEY = "super-secret-key"
OLDEST = dt.date(2026, 1, 1)
NEWEST = dt.date(2026, 1, 31)

ACTIVITY_PAYLOAD: dict[str, Any] = {
    "id": "i12345",
    "start_date_local": "2026-01-15T07:30:00",
    "type": "Run",
    "distance": 3500.0,
    "moving_time": 1260,
    "elapsed_time": 1300,
    "total_elevation_gain": 12.0,
    "average_heartrate": 142.4,
    "max_heartrate": 168,
}

WELLNESS_PAYLOAD: dict[str, Any] = {
    "id": "2026-01-15",
    "restingHR": 52,
    "hrv": 44.5,
    "hrvSDNN": 88.0,
    "sleepSecs": 25200,
    "sleepScore": 72,
    "vo2max": 48.1,
    "weight": 74.3,
}


class Recorder:
    """Collects requests and the waits the client asked for."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.waits: list[float] = []

    def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)


def make_client(
    responses: list[httpx.Response] | httpx.Response,
    *,
    recorder: Recorder | None = None,
    retry: RetryPolicy | None = None,
) -> tuple[IntervalsClient, Recorder]:
    """A client whose transport hands back the given responses in order."""
    recorder = recorder or Recorder()
    queue = responses if isinstance(responses, list) else [responses]
    remaining = list(queue)

    def handler(request: httpx.Request) -> httpx.Response:
        recorder.requests.append(request)
        if len(remaining) > 1:
            return remaining.pop(0)
        return remaining[0]

    client = IntervalsClient(
        API_KEY,
        transport=httpx.MockTransport(handler),
        retry=retry or RetryPolicy(max_attempts=3, initial_backoff_s=1.0),
        sleeper=recorder.sleep,
    )
    return client, recorder


def json_response(payload: Any, status: int = 200) -> httpx.Response:
    return httpx.Response(status, json=payload)


# --- authentication ----------------------------------------------------


def test_requests_use_basic_auth_with_the_literal_api_key_username() -> None:
    client, recorder = make_client(json_response([]))

    with client:
        client.list_activities(OLDEST, NEWEST)

    header = recorder.requests[0].headers["authorization"]
    scheme, _, encoded = header.partition(" ")
    assert scheme == "Basic"
    assert base64.b64decode(encoded).decode() == f"{BASIC_AUTH_USERNAME}:{API_KEY}"


def test_a_client_without_a_key_refuses_to_be_built() -> None:
    with pytest.raises(IntervalsAuthError, match="no intervals\\.icu API key"):
        IntervalsClient("")


def test_a_rejected_key_raises_immediately_without_retrying() -> None:
    client, recorder = make_client(json_response({"error": "nope"}, status=401))

    with client, pytest.raises(IntervalsAuthError) as caught:
        client.list_activities(OLDEST, NEWEST)

    assert caught.value.status_code == 401
    assert len(recorder.requests) == 1
    assert recorder.waits == []


def test_a_403_points_at_the_basic_auth_requirement() -> None:
    """A bearer token is the usual cause, so the message says so."""
    client, _ = make_client(json_response({}, status=403))

    with client, pytest.raises(IntervalsAuthError, match=BASIC_AUTH_USERNAME):
        client.list_activities(OLDEST, NEWEST)


def test_no_error_message_carries_the_api_key() -> None:
    for response in (
        json_response({}, status=401),
        json_response({}, status=403),
        json_response({}, status=404),
        json_response({}, status=500),
    ):
        client, _ = make_client(response, retry=RetryPolicy(max_attempts=1))
        with client:
            try:
                client.list_activities(OLDEST, NEWEST)
            except IngestError as exc:
                assert API_KEY not in str(exc)
            else:
                pytest.fail("expected an error")


# --- endpoints ---------------------------------------------------------


def test_list_activities_asks_for_the_window_on_the_athlete_path() -> None:
    client, recorder = make_client(json_response([ACTIVITY_PAYLOAD]))

    with client:
        payloads = client.list_activities(OLDEST, NEWEST)

    request = recorder.requests[0]
    assert request.url.path == "/api/v1/athlete/0/activities"
    assert request.url.params["oldest"] == "2026-01-01"
    assert request.url.params["newest"] == "2026-01-31"
    assert payloads == [ACTIVITY_PAYLOAD]


def test_list_activities_passes_a_field_selection() -> None:
    client, recorder = make_client(json_response([]))

    with client:
        client.list_activities(OLDEST, NEWEST, fields=["id", "type"])

    assert recorder.requests[0].url.params["fields"] == "id,type"


def test_a_window_that_runs_backwards_is_refused_before_any_request() -> None:
    client, recorder = make_client(json_response([]))

    with client, pytest.raises(ValueError, match="newest"):
        client.list_activities(NEWEST, OLDEST)

    assert recorder.requests == []


def test_get_activity_returns_the_object() -> None:
    client, recorder = make_client(json_response(ACTIVITY_PAYLOAD))

    with client:
        payload = client.get_activity("i12345")

    assert recorder.requests[0].url.path == "/api/v1/activity/i12345"
    assert payload["id"] == "i12345"


def test_get_activity_refuses_a_list() -> None:
    client, _ = make_client(json_response([ACTIVITY_PAYLOAD]))

    with client, pytest.raises(IntervalsApiError, match="did not return an object"):
        client.get_activity("i12345")


def test_get_streams_returns_the_raw_payload() -> None:
    streams = [{"type": "heartrate", "data": [130, 131]}]
    client, recorder = make_client(json_response(streams))

    with client:
        assert client.get_streams("i12345") == streams

    assert recorder.requests[0].url.path == "/api/v1/activity/i12345/streams"


def test_list_wellness_wraps_a_single_day_object_in_a_list() -> None:
    client, _ = make_client(json_response(WELLNESS_PAYLOAD))

    with client:
        assert client.list_wellness(OLDEST, NEWEST) == [WELLNESS_PAYLOAD]


def test_list_wellness_drops_non_objects_from_the_list() -> None:
    client, _ = make_client(json_response([WELLNESS_PAYLOAD, "junk", None]))

    with client:
        assert client.list_wellness(OLDEST, NEWEST) == [WELLNESS_PAYLOAD]


def test_list_events_uses_the_events_path() -> None:
    client, recorder = make_client(json_response([]))

    with client:
        client.list_events(OLDEST, NEWEST)

    assert recorder.requests[0].url.path == "/api/v1/athlete/0/events"


def test_a_non_json_body_is_reported_as_such() -> None:
    client, _ = make_client(httpx.Response(200, text="<html>nope</html>"))

    with client, pytest.raises(IntervalsApiError, match="did not return JSON"):
        client.list_activities(OLDEST, NEWEST)


def test_an_object_where_a_list_belongs_is_reported() -> None:
    client, _ = make_client(json_response({"unexpected": True}))

    with client, pytest.raises(IntervalsApiError, match="did not return a list"):
        client.list_activities(OLDEST, NEWEST)


# --- FIT download ------------------------------------------------------


def test_download_fit_writes_the_file_and_leaves_no_partial(
    tmp_path: Path,
) -> None:
    client, recorder = make_client(httpx.Response(200, content=b"FITBYTES"))
    destination = tmp_path / "fit" / "i12345.fit"

    with client:
        written = client.download_fit("i12345", destination)

    assert written == destination
    assert destination.read_bytes() == b"FITBYTES"
    assert list(destination.parent.iterdir()) == [destination]
    assert recorder.requests[0].url.path == "/api/v1/activity/i12345/fit-file"


def test_download_fit_refuses_an_empty_body(tmp_path: Path) -> None:
    client, _ = make_client(httpx.Response(200, content=b""))
    destination = tmp_path / "i12345.fit"

    with client, pytest.raises(IntervalsApiError, match="empty FIT file"):
        client.download_fit("i12345", destination)

    assert not destination.exists()


# --- backoff -----------------------------------------------------------


def test_a_429_is_retried_using_the_servers_retry_after() -> None:
    client, recorder = make_client(
        [
            httpx.Response(429, headers={"Retry-After": "7"}, json={}),
            json_response([ACTIVITY_PAYLOAD]),
        ]
    )

    with client:
        payloads = client.list_activities(OLDEST, NEWEST)

    assert payloads == [ACTIVITY_PAYLOAD]
    assert recorder.waits == [7.0]
    assert len(recorder.requests) == 2


def test_a_429_without_retry_after_falls_back_to_exponential_backoff() -> None:
    client, recorder = make_client(
        [json_response({}, status=429), json_response([])],
        retry=RetryPolicy(max_attempts=3, initial_backoff_s=2.0),
    )

    with client:
        client.list_activities(OLDEST, NEWEST)

    assert recorder.waits == [2.0]


def test_persistent_rate_limiting_gives_up_after_the_attempt_budget() -> None:
    client, recorder = make_client(
        json_response({}, status=429),
        retry=RetryPolicy(max_attempts=3, initial_backoff_s=1.0),
    )

    with client, pytest.raises(RateLimited, match="3 attempts"):
        client.list_activities(OLDEST, NEWEST)

    assert len(recorder.requests) == 3
    assert recorder.waits == [1.0, 2.0]


def test_backoff_is_capped() -> None:
    policy = RetryPolicy(max_attempts=10, initial_backoff_s=2.0, max_backoff_s=10.0)

    assert [policy.backoff_for(n) for n in (1, 2, 3, 4, 5)] == [
        2.0,
        4.0,
        8.0,
        10.0,
        10.0,
    ]


def test_a_server_error_is_retried_and_then_succeeds() -> None:
    client, recorder = make_client(
        [json_response({}, status=503), json_response([ACTIVITY_PAYLOAD])]
    )

    with client:
        assert client.list_activities(OLDEST, NEWEST) == [ACTIVITY_PAYLOAD]

    assert len(recorder.requests) == 2


def test_a_persistent_server_error_surfaces_the_status() -> None:
    client, _ = make_client(
        json_response({}, status=502), retry=RetryPolicy(max_attempts=2)
    )

    with client, pytest.raises(IntervalsApiError, match="502"):
        client.list_activities(OLDEST, NEWEST)


def test_a_client_error_is_not_retried() -> None:
    client, recorder = make_client(json_response({}, status=404))

    with client, pytest.raises(IntervalsApiError, match="404"):
        client.get_activity("missing")

    assert len(recorder.requests) == 1


def test_a_dropped_connection_is_retried() -> None:
    recorder = Recorder()
    attempts: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        recorder.requests.append(request)
        attempts.append(1)
        if len(attempts) == 1:
            raise httpx.ConnectError("connection reset", request=request)
        return json_response([ACTIVITY_PAYLOAD])

    client = IntervalsClient(
        API_KEY,
        transport=httpx.MockTransport(handler),
        retry=RetryPolicy(max_attempts=3, initial_backoff_s=1.0),
        sleeper=recorder.sleep,
    )

    with client:
        assert client.list_activities(OLDEST, NEWEST) == [ACTIVITY_PAYLOAD]

    assert len(attempts) == 2
    assert recorder.waits == [1.0]


def test_a_successful_call_never_sleeps() -> None:
    client, recorder = make_client(json_response([]))

    with client:
        client.list_activities(OLDEST, NEWEST)

    assert recorder.waits == []


# --- mapping -----------------------------------------------------------


def test_an_activity_payload_maps_onto_the_stored_fields() -> None:
    summary = to_activity_summary(ACTIVITY_PAYLOAD)

    assert summary.id == "i12345"
    assert summary.start_local == dt.datetime(2026, 1, 15, 7, 30)
    assert summary.start_local.tzinfo is None
    assert summary.sport == "Run"
    assert summary.distance_m == pytest.approx(3500.0)
    assert summary.moving_s == 1260
    assert summary.elapsed_s == 1300
    assert summary.avg_hr == 142
    assert summary.avg_pace_s_per_km == pytest.approx(1260 / 3.5)


def test_a_numeric_activity_id_becomes_a_string() -> None:
    assert to_activity_summary({**ACTIVITY_PAYLOAD, "id": 4711}).id == "4711"


def test_a_zone_marker_on_the_local_start_is_discarded() -> None:
    payload = {**ACTIVITY_PAYLOAD, "start_date_local": "2026-01-15T07:30:00Z"}

    assert to_activity_summary(payload).start_local == dt.datetime(2026, 1, 15, 7, 30)


def test_an_activity_without_distance_has_no_pace() -> None:
    payload = {**ACTIVITY_PAYLOAD, "distance": None}

    assert to_activity_summary(payload).avg_pace_s_per_km is None


def test_an_unknown_sport_becomes_other() -> None:
    assert to_activity_summary({**ACTIVITY_PAYLOAD, "type": "Kayaking"}).sport == (
        "Other"
    )


@pytest.mark.parametrize(
    "payload",
    [
        {"start_date_local": "2026-01-15T07:30:00"},
        {"id": "", "start_date_local": "2026-01-15T07:30:00"},
        {"id": "i1"},
        {"id": "i1", "start_date_local": "not a date"},
        {"id": "i1", "start_date_local": None},
    ],
)
def test_an_unusable_activity_payload_is_rejected(payload: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        to_activity_summary(payload)


def test_a_wellness_payload_records_which_field_the_hrv_came_from() -> None:
    values = to_wellness_values(WELLNESS_PAYLOAD)

    assert values.date == dt.date(2026, 1, 15)
    assert values.resting_hr == 52
    assert values.hrv == pytest.approx(44.5)
    assert values.hrv_source_field == "hrv"
    assert values.sleep_secs == 25200
    assert values.sleep_score == 72
    assert values.vo2max == pytest.approx(48.1)
    assert values.weight_kg == pytest.approx(74.3)
    assert values.is_empty is False


def test_a_second_hrv_field_is_used_but_named_as_itself() -> None:
    """Two different measures must not be silently pooled in one column."""
    payload = {"id": "2026-01-15", "hrvSDNN": 88.0}

    values = to_wellness_values(payload)

    assert values.hrv == pytest.approx(88.0)
    assert values.hrv_source_field == "hrvSDNN"


def test_a_day_without_any_hrv_field_names_none() -> None:
    values = to_wellness_values({"id": "2026-01-15", "restingHR": 52})

    assert values.hrv is None
    assert values.hrv_source_field is None


def test_a_wellness_day_with_nothing_in_it_reports_itself_as_empty() -> None:
    assert to_wellness_values({"id": "2026-01-15"}).is_empty is True


@pytest.mark.parametrize("payload", [{}, {"id": ""}, {"id": "kein Datum"}])
def test_an_unusable_wellness_payload_is_rejected(payload: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        to_wellness_values(payload)


def test_batch_mapping_keeps_the_good_and_reports_the_bad() -> None:
    mapped, skipped = IntervalsClient.activities_from([ACTIVITY_PAYLOAD, {"no": "id"}])

    assert [item.id for item in mapped] == ["i12345"]
    assert len(skipped) == 1
    assert "no id" in skipped[0]
