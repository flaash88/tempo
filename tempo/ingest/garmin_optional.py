"""Optional Garmin Connect connector. Off unless explicitly enabled.

It exists for two values intervals.icu does not carry: Body Battery and
Training Readiness. Everything else comes through intervals.icu, and Tempo
is fully functional with this module switched off — which is the default.

Two rules govern it, both because repeated failed attempts lead to blocks
at the account level:

* **At most one attempt per day**, enforced through the ``garmin`` row in
  ``sync_state``.
* **On 401, 403 or 429 the module disables itself**, writes the reason to
  ``sync_log`` and stops. There is deliberately no retry loop here.

``garminconnect`` is an optional extra, not a dependency. With the
connector off nothing imports it; with it on but not installed, that is
reported as one more refusal and the module stands down.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final, Protocol

from sqlalchemy import Engine

from tempo.config import Settings
from tempo.db.models import DataSource, SyncStatus
from tempo.db.session import session_scope
from tempo.ingest import store

log = logging.getLogger(__name__)

SOURCE: Final = DataSource.GARMIN

# One attempt per day, per the ingestion rules.
MIN_ATTEMPT_INTERVAL: Final = dt.timedelta(days=1)

# Statuses after which the module refuses to try again in this run.
STAND_DOWN_STATUSES: Final[frozenset[int]] = frozenset({401, 403, 429})

# Garmin only serves these two values for recent days, and a wide window is
# what attracts rate limiting.
LOOKBACK_DAYS: Final = 7


class GarminApi(Protocol):
    """The slice of ``garminconnect.Garmin`` this module uses."""

    def get_body_battery(self, startdate: str, enddate: str) -> Any: ...

    def get_training_readiness(self, cdate: str) -> Any: ...


ApiFactory = Callable[[Settings], GarminApi]


@dataclass
class GarminReport:
    status: str = SyncStatus.OK
    days_updated: int = 0
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"days updated {self.days_updated}"]
        if self.notes:
            parts.append(f"notes: {'; '.join(self.notes[:10])}")
        return ", ".join(parts)


def build_api(settings: Settings) -> GarminApi:
    """Log in to Garmin Connect, reusing a persisted token.

    The token store lives in the data volume so a restart does not mean
    another login; a fresh login on every run is itself a way to get
    rate limited.
    """
    try:
        import garminconnect
    except ImportError as exc:  # pragma: no cover - the extra is not installed
        raise RuntimeError(
            "the garmin extra is not installed: uv sync --extra garmin"
        ) from exc

    email = settings.garmin_email
    password = settings.garmin_password.get_secret_value()
    if not email or not password:
        raise RuntimeError("GARMIN_EMAIL and GARMIN_PASSWORD are not configured")

    token_dir = settings.garmin_token_dir
    token_dir.mkdir(parents=True, exist_ok=True)
    api = garminconnect.Garmin(email=email, password=password)
    api.login(str(token_dir))
    return api  # type: ignore[no-any-return]


def status_code_of(error: BaseException) -> int | None:
    """The HTTP status behind an exception, if there is one.

    ``garminconnect`` wraps several different libraries, so the status can
    sit on the exception, on an attached response, or nowhere at all.
    """
    seen: set[int] = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        for holder in (current, getattr(current, "response", None)):
            code = getattr(holder, "status_code", None)
            if isinstance(code, int):
                return code
        current = current.__cause__ or current.__context__
    return None


def stand_down_reason(error: BaseException) -> str | None:
    """Why this error means "stop asking", or None if it does not.

    Matching on the class name as well as the status because an
    authentication or rate limit error does not always carry one.
    """
    code = status_code_of(error)
    if code in STAND_DOWN_STATUSES:
        return f"Garmin refused with {code}"

    name = type(error).__name__
    if "Authentication" in name:
        return f"Garmin rejected the credentials ({name})"
    if "TooManyRequests" in name or "RateLimit" in name:
        return f"Garmin rate limited the account ({name})"
    return None


def body_battery_for_day(payload: Any) -> int | None:
    """The day's Body Battery reading from one payload entry.

    Garmin reports a series over the day rather than one number. The peak is
    what is taken: Body Battery tops out after sleep, and that post-sleep
    level is the recovery statement the app is after. A payload that already
    carries a single level is used as it stands.
    """
    if not isinstance(payload, dict):
        return None

    for key in ("bodyBatteryLevel", "bodyBatteryMax", "maxBodyBattery"):
        value = _as_level(payload.get(key))
        if value is not None:
            return value

    series = payload.get("bodyBatteryValuesArray")
    if not isinstance(series, list):
        return None
    levels = [
        _as_level(entry[2])
        for entry in series
        if isinstance(entry, list | tuple) and len(entry) > 2
    ]
    present = [level for level in levels if level is not None]
    return max(present) if present else None


def training_readiness_for_day(payload: Any) -> int | None:
    """The readiness score from one payload entry."""
    entry = payload
    if isinstance(entry, list):
        entry = entry[0] if entry else None
    if not isinstance(entry, dict):
        return None
    for key in ("score", "trainingReadinessScore", "level"):
        value = _as_level(entry.get(key))
        if value is not None:
            return value
    return None


def day_of(payload: Any) -> dt.date | None:
    if not isinstance(payload, dict):
        return None
    for key in ("date", "calendarDate", "startDate"):
        raw = payload.get(key)
        if isinstance(raw, str) and raw.strip():
            try:
                return dt.date.fromisoformat(raw.strip()[:10])
            except ValueError:
                continue
    return None


def _as_level(value: Any) -> int | None:
    """A 0..100 reading, or None. Anything outside the range is not a level."""
    if value is None or isinstance(value, bool):
        return None
    try:
        level = round(float(value))
    except (TypeError, ValueError):
        return None
    return level if 0 <= level <= 100 else None


def due_for_attempt(last_success_at: dt.datetime | None, now: dt.datetime) -> bool:
    if last_success_at is None:
        return True
    if last_success_at.tzinfo is None:
        last_success_at = last_success_at.replace(tzinfo=dt.UTC)
    return now - last_success_at >= MIN_ATTEMPT_INTERVAL


def sync_garmin(
    engine: Engine,
    settings: Settings,
    *,
    now: dt.datetime | None = None,
    api_factory: ApiFactory | None = None,
) -> GarminReport:
    """Fetch Body Battery and Training Readiness, at most once a day."""
    report = GarminReport()
    if not settings.garmin_direct_enabled:
        report.status = SyncStatus.DISABLED
        report.notes.append("Garmin direct connector is disabled")
        return report

    pinned_clock = now is not None
    now = now or dt.datetime.now(tz=dt.UTC)

    with session_scope(engine) as session:
        state = store.get_or_create_state(session, SOURCE)
        last_success = state.last_success_at

    if not due_for_attempt(last_success, now):
        report.notes.append("skipped: already attempted within the last day")
        return report

    with session_scope(engine) as session:
        log_id = store.open_sync_log(session, SOURCE, now).id

    newest = now.date()
    oldest = newest - dt.timedelta(days=LOOKBACK_DAYS - 1)
    readings: dict[dt.date, tuple[int | None, int | None]] = {}

    try:
        api = (api_factory or build_api)(settings)
        for day, level in _read_body_battery(api, oldest, newest):
            existing = readings.get(day, (None, None))
            readings[day] = (level, existing[1])
        for day, score in _read_training_readiness(api, oldest, newest):
            existing = readings.get(day, (None, None))
            readings[day] = (existing[0], score)
    except Exception as exc:
        # Broad on purpose: whatever the optional library throws, the
        # answer is the same — stop asking and record why.
        reason = stand_down_reason(exc)
        report.status = SyncStatus.DISABLED if reason else SyncStatus.FAILED
        report.notes.append(reason or f"Garmin call failed: {exc}")
        log.warning(
            "garmin connector standing down",
            extra={"reason": report.notes[-1], "status": report.status},
        )

    if readings:
        with session_scope(engine) as session:
            for day, (level, score) in sorted(readings.items()):
                if store.upsert_garmin_wellness(
                    session, day, body_battery=level, training_readiness=score
                ):
                    report.days_updated += 1

    finished = now if pinned_clock else dt.datetime.now(tz=dt.UTC)
    with session_scope(engine) as session:
        if report.status == SyncStatus.OK:
            state = store.get_or_create_state(session, SOURCE)
            state.last_success_at = finished
        store.close_sync_log(
            session,
            log_id,
            status=report.status,
            finished_at=finished,
            detail=report.summary(),
        )
    return report


def _read_body_battery(
    api: GarminApi, oldest: dt.date, newest: dt.date
) -> list[tuple[dt.date, int | None]]:
    payload = api.get_body_battery(oldest.isoformat(), newest.isoformat())
    entries = payload if isinstance(payload, list) else [payload]
    found: list[tuple[dt.date, int | None]] = []
    for entry in entries:
        day = day_of(entry)
        if day is None:
            continue
        found.append((day, body_battery_for_day(entry)))
    return found


def _read_training_readiness(
    api: GarminApi, oldest: dt.date, newest: dt.date
) -> list[tuple[dt.date, int | None]]:
    """One request per day — the endpoint takes a single date.

    Sequential and small on purpose; the window is a week at most.
    """
    found: list[tuple[dt.date, int | None]] = []
    day = oldest
    while day <= newest:
        score = training_readiness_for_day(api.get_training_readiness(day.isoformat()))
        if score is not None:
            found.append((day, score))
        day += dt.timedelta(days=1)
    return found
