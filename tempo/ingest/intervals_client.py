"""intervals.icu API client.

Authentication is Basic auth with the username set to the literal string
``API_KEY`` and the key as the password. A bearer token is rejected with
403, so it is not offered as an option. Athlete id ``0`` is a
self-reference to the authenticated athlete.

Requests are sequential by design. A single user's history is small, and
hammering a free API in parallel is how an account gets blocked. On 429
the client backs off, preferring the server's own ``Retry-After``.
"""

from __future__ import annotations

import datetime as dt
import logging
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType
from typing import Any, Final, Self

import httpx

from tempo.ingest.errors import (
    IntervalsApiError,
    IntervalsAuthError,
    RateLimited,
)
from tempo.ingest.sports import normalise_sport

log = logging.getLogger(__name__)

DEFAULT_BASE_URL: Final = "https://intervals.icu/api/v1"

# The API requires this literal username; the key goes in the password.
BASIC_AUTH_USERNAME: Final = "API_KEY"

DEFAULT_TIMEOUT_S: Final = 30.0
_SECONDS_PER_KM: Final = 1000.0


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Backoff for rate limiting and transient failures."""

    max_attempts: int = 4
    initial_backoff_s: float = 2.0
    max_backoff_s: float = 60.0

    def backoff_for(self, attempt: int) -> float:
        """Exponential backoff, capped. ``attempt`` counts from 1."""
        return min(self.initial_backoff_s * 2.0 ** (attempt - 1), self.max_backoff_s)


@dataclass(frozen=True, slots=True)
class ActivitySummary:
    """The summary fields of one activity, as intervals.icu reports them."""

    id: str
    start_local: dt.datetime
    sport: str
    distance_m: float | None = None
    moving_s: int | None = None
    elapsed_s: int | None = None
    elevation_gain_m: float | None = None
    avg_hr: int | None = None
    max_hr: int | None = None
    avg_pace_s_per_km: float | None = None


@dataclass(frozen=True, slots=True)
class WellnessValues:
    """One day of wellness readings."""

    date: dt.date
    resting_hr: int | None = None
    hrv_rmssd: float | None = None
    sleep_secs: int | None = None
    sleep_score: int | None = None
    vo2max: float | None = None
    weight_kg: float | None = None

    @property
    def is_empty(self) -> bool:
        """True when the day carries no reading at all."""
        return all(
            value is None
            for value in (
                self.resting_hr,
                self.hrv_rmssd,
                self.sleep_secs,
                self.sleep_score,
                self.vo2max,
                self.weight_kg,
            )
        )


def _as_int(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return round(float(value))
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_local_datetime(value: Any) -> dt.datetime | None:
    """Parse a local timestamp, discarding any zone marker.

    ``start_date_local`` is wall clock time at the athlete's location; an
    offset appended to it would only be the offset of the moment, which is
    not what the column stores.
    """
    if not isinstance(value, str) or not value:
        return None
    text = value.strip().replace("Z", "")
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    return parsed.replace(tzinfo=None)


def _pace_s_per_km(distance_m: float | None, seconds: int | None) -> float | None:
    if not distance_m or not seconds or distance_m <= 0 or seconds <= 0:
        return None
    return seconds / (distance_m / _SECONDS_PER_KM)


def to_activity_summary(payload: dict[str, Any]) -> ActivitySummary:
    """Map one activity payload onto the fields Tempo stores.

    Raises ``ValueError`` when the payload has no id or no start time —
    without either it cannot be stored or ordered, and the caller skips it
    rather than aborting the whole sync.
    """
    raw_id = payload.get("id")
    activity_id = str(raw_id).strip() if raw_id is not None else ""
    if not activity_id:
        raise ValueError("activity payload has no id")

    start_local = _as_local_datetime(payload.get("start_date_local"))
    if start_local is None:
        raise ValueError(f"activity {activity_id} has no usable start_date_local")

    distance_m = _as_float(payload.get("distance"))
    moving_s = _as_int(payload.get("moving_time"))
    return ActivitySummary(
        id=activity_id,
        start_local=start_local,
        sport=normalise_sport(payload.get("type")),
        distance_m=distance_m,
        moving_s=moving_s,
        elapsed_s=_as_int(payload.get("elapsed_time")),
        elevation_gain_m=_as_float(payload.get("total_elevation_gain")),
        avg_hr=_as_int(payload.get("average_heartrate")),
        max_hr=_as_int(payload.get("max_heartrate")),
        # Derived rather than read from the payload's own pace field, whose
        # unit is not documented in a way worth relying on.
        avg_pace_s_per_km=_pace_s_per_km(distance_m, moving_s),
    )


def to_wellness_values(payload: dict[str, Any]) -> WellnessValues:
    """Map one wellness payload onto the fields Tempo stores.

    ``hrv`` is intervals.icu's rMSSD; ``hrvSDNN`` is a different measure
    and is deliberately not mixed into the same column.
    """
    raw_date = payload.get("id") or payload.get("date")
    if not isinstance(raw_date, str) or not raw_date.strip():
        raise ValueError("wellness payload has no date")
    try:
        day = dt.date.fromisoformat(raw_date.strip()[:10])
    except ValueError as exc:
        raise ValueError(f"wellness payload has an unusable date {raw_date!r}") from exc

    return WellnessValues(
        date=day,
        resting_hr=_as_int(payload.get("restingHR")),
        hrv_rmssd=_as_float(payload.get("hrv")),
        sleep_secs=_as_int(payload.get("sleepSecs")),
        sleep_score=_as_int(payload.get("sleepScore")),
        vo2max=_as_float(payload.get("vo2max")),
        weight_kg=_as_float(payload.get("weight")),
    )


class IntervalsClient:
    """Thin, sequential client for the endpoints Tempo needs."""

    def __init__(
        self,
        api_key: str,
        athlete_id: str = "0",
        *,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.BaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
        retry: RetryPolicy | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise IntervalsAuthError("no intervals.icu API key configured")
        self.athlete_id = athlete_id or "0"
        self.retry = retry or RetryPolicy()
        self._sleep = sleeper
        self._client = httpx.Client(
            base_url=base_url,
            auth=httpx.BasicAuth(BASIC_AUTH_USERNAME, api_key),
            transport=transport,
            timeout=timeout,
            follow_redirects=True,
            headers={"Accept": "application/json"},
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    # --- transport -----------------------------------------------------

    def _retry_after(self, response: httpx.Response, attempt: int) -> float:
        """Seconds to wait, preferring the server's own instruction."""
        header = response.headers.get("Retry-After", "").strip()
        if header.isdigit():
            return min(float(header), self.retry.max_backoff_s)
        return self.retry.backoff_for(attempt)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
    ) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(1, self.retry.max_attempts + 1):
            try:
                response = self._client.request(method, path, params=params)
            except httpx.TransportError as exc:
                # A dropped connection is worth one more try; the message
                # never carries the credential, which lives in a header.
                last_error = IntervalsApiError(
                    f"{method} {path} failed: {type(exc).__name__}"
                )
                if attempt == self.retry.max_attempts:
                    break
                self._sleep(self.retry.backoff_for(attempt))
                continue

            status = response.status_code
            if status == 401:
                raise IntervalsAuthError(
                    "intervals.icu rejected the credentials", status_code=401
                )
            if status == 403:
                raise IntervalsAuthError(
                    "intervals.icu refused the request — basic auth with "
                    f"username {BASIC_AUTH_USERNAME} is required",
                    status_code=403,
                )
            if status == 429:
                wait_s = self._retry_after(response, attempt)
                if attempt == self.retry.max_attempts:
                    raise RateLimited(
                        f"rate limited by intervals.icu after {attempt} attempts",
                        status_code=429,
                    )
                log.info(
                    "rate limited, backing off",
                    extra={"path": path, "wait_s": wait_s, "attempt": attempt},
                )
                self._sleep(wait_s)
                continue
            if status >= 500:
                last_error = IntervalsApiError(
                    f"intervals.icu returned {status} for {path}",
                    status_code=status,
                )
                if attempt == self.retry.max_attempts:
                    break
                self._sleep(self.retry.backoff_for(attempt))
                continue
            if status >= 400:
                raise IntervalsApiError(
                    f"intervals.icu returned {status} for {path}",
                    status_code=status,
                )
            return response

        raise last_error or IntervalsApiError(f"{method} {path} failed")

    def _get_json(self, path: str, *, params: dict[str, str] | None = None) -> Any:
        response = self._request("GET", path, params=params)
        try:
            return response.json()
        except ValueError as exc:
            raise IntervalsApiError(f"{path} did not return JSON") from exc

    @staticmethod
    def _as_payload_list(data: Any, path: str) -> list[dict[str, Any]]:
        if not isinstance(data, list):
            raise IntervalsApiError(f"{path} did not return a list")
        return [item for item in data if isinstance(item, dict)]

    @staticmethod
    def _window(oldest: dt.date, newest: dt.date) -> dict[str, str]:
        if newest < oldest:
            raise ValueError("newest must not be before oldest")
        return {"oldest": oldest.isoformat(), "newest": newest.isoformat()}

    # --- endpoints -----------------------------------------------------

    def list_activities(
        self,
        oldest: dt.date,
        newest: dt.date,
        fields: Sequence[str] | None = None,
    ) -> list[dict[str, Any]]:
        path = f"/athlete/{self.athlete_id}/activities"
        params = self._window(oldest, newest)
        if fields:
            params["fields"] = ",".join(fields)
        return self._as_payload_list(self._get_json(path, params=params), path)

    def get_activity(self, activity_id: str) -> dict[str, Any]:
        path = f"/activity/{activity_id}"
        data = self._get_json(path)
        if not isinstance(data, dict):
            raise IntervalsApiError(f"{path} did not return an object")
        return data

    def get_streams(self, activity_id: str) -> Any:
        """Raw stream payload.

        Tempo fills its own streams from the downloaded FIT file, which is
        the authoritative recording. This is here for the cases where no
        FIT file exists on the server.
        """
        return self._get_json(f"/activity/{activity_id}/streams")

    def download_fit(self, activity_id: str, destination: Path) -> Path:
        """Save the raw FIT file, atomically.

        Written to a temporary name and renamed, so an interrupted download
        can never be mistaken for a complete file on the next run.
        """
        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        response = self._request("GET", f"/activity/{activity_id}/fit-file")
        payload = response.content
        if not payload:
            raise IntervalsApiError(
                f"activity {activity_id} returned an empty FIT file"
            )
        partial.write_bytes(payload)
        partial.replace(destination)
        return destination

    def list_wellness(self, oldest: dt.date, newest: dt.date) -> list[dict[str, Any]]:
        path = f"/athlete/{self.athlete_id}/wellness"
        data = self._get_json(path, params=self._window(oldest, newest))
        if isinstance(data, dict):
            # The API answers a single day as an object rather than a list.
            return [data]
        return self._as_payload_list(data, path)

    def list_events(self, oldest: dt.date, newest: dt.date) -> list[dict[str, Any]]:
        path = f"/athlete/{self.athlete_id}/events"
        return self._as_payload_list(
            self._get_json(path, params=self._window(oldest, newest)), path
        )

    # --- mapping helpers ------------------------------------------------

    @staticmethod
    def activities_from(
        payloads: Iterable[dict[str, Any]],
    ) -> tuple[list[ActivitySummary], list[str]]:
        """Map payloads, collecting the reasons for anything skipped."""
        mapped: list[ActivitySummary] = []
        skipped: list[str] = []
        for payload in payloads:
            try:
                mapped.append(to_activity_summary(payload))
            except ValueError as exc:
                skipped.append(str(exc))
        return mapped, skipped

    @staticmethod
    def wellness_from(
        payloads: Iterable[dict[str, Any]],
    ) -> tuple[list[WellnessValues], list[str]]:
        mapped: list[WellnessValues] = []
        skipped: list[str] = []
        for payload in payloads:
            try:
                mapped.append(to_wellness_values(payload))
            except ValueError as exc:
                skipped.append(str(exc))
        return mapped, skipped
