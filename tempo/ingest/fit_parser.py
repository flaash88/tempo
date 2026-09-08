"""FIT parsing.

Reads the raw files as they were saved, without touching the network.
Three rules shape everything here:

* **Gaps are never interpolated.** The stream is normalised onto a one
  second grid; a second with no record becomes a sample whose channels are
  all ``None``, and a channel the device did not report stays ``None``
  while its neighbours keep their values. A paused recording is therefore
  visibly a hole rather than a straight line drawn across it.
* **Nothing is invented.** Summary values come from the file's own session
  and lap messages. Only where the session message is missing entirely are
  averages derived from the samples, and that is stated in the result.
* **A file that is missing things is still data.** No GPS, no heart rate,
  no laps, no session — each of those is a normal recording, not an error.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

import fitdecode

from tempo.ingest.errors import FitFileError
from tempo.ingest.sports import normalise_sport

log = logging.getLogger(__name__)

# Degrees per semicircle. FIT stores coordinates as signed semicircles;
# fitdecode hands them over unconverted.
SEMICIRCLE_DEGREES: Final = 180.0 / 2**31

# Guard against a corrupt timestamp turning into millions of stream rows.
# Not a metric threshold — a parser sanity limit, so it lives here.
MAX_ACTIVITY_SPAN_S: Final = 48 * 3600

_SECONDS_PER_KM: Final = 1000.0


@dataclass(frozen=True, slots=True)
class StreamSample:
    """One second of the recording. ``None`` means "not recorded"."""

    offset_s: int
    hr: int | None = None
    speed_m_s: float | None = None
    altitude_m: float | None = None
    cadence: int | None = None
    power: int | None = None
    lat: float | None = None
    lon: float | None = None

    @property
    def is_empty(self) -> bool:
        """True when this second carries no data at all — a gap."""
        return all(
            value is None
            for value in (
                self.hr,
                self.speed_m_s,
                self.altitude_m,
                self.cadence,
                self.power,
                self.lat,
                self.lon,
            )
        )


@dataclass(frozen=True, slots=True)
class ParsedLap:
    index: int
    distance_m: float | None = None
    duration_s: int | None = None
    avg_hr: int | None = None
    avg_pace_s_per_km: float | None = None


@dataclass(frozen=True, slots=True)
class ParsedActivity:
    """Everything one FIT file has to say about one session."""

    start_local: dt.datetime
    start_utc: dt.datetime
    sport: str
    distance_m: float | None = None
    moving_s: int | None = None
    elapsed_s: int | None = None
    elevation_gain_m: float | None = None
    avg_hr: int | None = None
    max_hr: int | None = None
    avg_pace_s_per_km: float | None = None
    laps: tuple[ParsedLap, ...] = ()
    samples: tuple[StreamSample, ...] = ()
    # True when the file had no session message and the summary had to be
    # derived from the records themselves.
    derived_summary: bool = False
    # True when no activity message supplied a UTC offset, so start_local
    # is the UTC time for want of anything better.
    local_time_assumed: bool = False

    @property
    def gap_seconds(self) -> int:
        return sum(1 for sample in self.samples if sample.is_empty)


@dataclass
class _Collected:
    records: list[dict[str, Any]] = field(default_factory=list)
    laps: list[dict[str, Any]] = field(default_factory=list)
    session: dict[str, Any] | None = None
    activity: dict[str, Any] | None = None
    file_id: dict[str, Any] | None = None


def _value(message: fitdecode.FitDataMessage, *names: str) -> Any:
    """First present value among ``names``, else None.

    FIT carries both a legacy and an "enhanced" variant of some fields;
    the enhanced one has the wider range and is preferred.
    """
    for name in names:
        if message.has_field(name):
            value = message.get_value(name, fallback=None)
            if value is not None:
                return value
    return None


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return round(float(value))
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_utc(value: Any) -> dt.datetime | None:
    if not isinstance(value, dt.datetime):
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=dt.UTC)
    return value.astimezone(dt.UTC)


def _collect(path: Path) -> _Collected:
    collected = _Collected()
    try:
        with fitdecode.FitReader(path) as reader:
            for frame in reader:
                if frame.frame_type != fitdecode.FIT_FRAME_DATA:
                    continue
                if frame.name == "record":
                    collected.records.append(_read_record(frame))
                elif frame.name == "lap":
                    collected.laps.append(_read_lap(frame))
                elif frame.name == "session" and collected.session is None:
                    collected.session = _read_session(frame)
                elif frame.name == "activity" and collected.activity is None:
                    collected.activity = _read_activity(frame)
                elif frame.name == "file_id" and collected.file_id is None:
                    collected.file_id = {
                        "time_created": _as_utc(_value(frame, "time_created"))
                    }
    except (
        fitdecode.FitError,
        struct.error,
        ValueError,
        KeyError,
        IndexError,
    ) as exc:
        # A truncated, corrupt or non-FIT file. OSError deliberately
        # propagates: a missing or unreadable file is the caller's problem.
        raise FitFileError(f"{path.name} could not be parsed as FIT") from exc
    return collected


def _read_record(frame: fitdecode.FitDataMessage) -> dict[str, Any]:
    lat_raw = _as_float(_value(frame, "position_lat"))
    lon_raw = _as_float(_value(frame, "position_long"))
    return {
        "timestamp": _as_utc(_value(frame, "timestamp")),
        "hr": _as_int(_value(frame, "heart_rate")),
        "speed_m_s": _as_float(_value(frame, "enhanced_speed", "speed")),
        "altitude_m": _as_float(_value(frame, "enhanced_altitude", "altitude")),
        "cadence": _as_int(_value(frame, "cadence")),
        "power": _as_int(_value(frame, "power")),
        "lat": None if lat_raw is None else lat_raw * SEMICIRCLE_DEGREES,
        "lon": None if lon_raw is None else lon_raw * SEMICIRCLE_DEGREES,
        "distance_m": _as_float(_value(frame, "distance")),
    }


def _read_lap(frame: fitdecode.FitDataMessage) -> dict[str, Any]:
    return {
        "index": _as_int(_value(frame, "message_index")),
        "distance_m": _as_float(_value(frame, "total_distance")),
        "duration_s": _as_int(_value(frame, "total_timer_time", "total_elapsed_time")),
        "avg_hr": _as_int(_value(frame, "avg_heart_rate")),
        "avg_speed_m_s": _as_float(_value(frame, "enhanced_avg_speed", "avg_speed")),
    }


def _read_session(frame: fitdecode.FitDataMessage) -> dict[str, Any]:
    return {
        "start_time": _as_utc(_value(frame, "start_time")),
        "sport": _value(frame, "sport"),
        "elapsed_s": _as_int(_value(frame, "total_elapsed_time")),
        "moving_s": _as_int(_value(frame, "total_timer_time")),
        "distance_m": _as_float(_value(frame, "total_distance")),
        "elevation_gain_m": _as_float(_value(frame, "total_ascent")),
        "avg_hr": _as_int(_value(frame, "avg_heart_rate")),
        "max_hr": _as_int(_value(frame, "max_heart_rate")),
    }


def _read_activity(frame: fitdecode.FitDataMessage) -> dict[str, Any]:
    return {
        "timestamp": _as_utc(_value(frame, "timestamp")),
        "local_timestamp": _as_utc(_value(frame, "local_timestamp")),
        "moving_s": _as_int(_value(frame, "total_timer_time")),
    }


def _utc_offset(collected: _Collected) -> dt.timedelta | None:
    """The recording's UTC offset, from the activity message.

    FIT stores local time as a second timestamp on the same instant, so
    the difference between the two is the offset that was in force.
    """
    activity = collected.activity
    if not activity:
        return None
    local = activity.get("local_timestamp")
    utc = activity.get("timestamp")
    if not isinstance(local, dt.datetime) or not isinstance(utc, dt.datetime):
        return None
    return local - utc


def _build_grid(
    records: list[dict[str, Any]], start_utc: dt.datetime
) -> tuple[StreamSample, ...]:
    """Normalise records onto a dense one second grid.

    Seconds without a record become empty samples. Where a device wrote
    several records into the same second, the first one wins — averaging
    them would be inventing a value that was never measured.
    """
    by_offset: dict[int, dict[str, Any]] = {}
    for record in records:
        stamp = record["timestamp"]
        if stamp is None:
            continue
        offset = int((stamp - start_utc).total_seconds())
        if offset < 0:
            continue
        by_offset.setdefault(offset, record)

    if not by_offset:
        return ()

    span = max(by_offset)
    if span > MAX_ACTIVITY_SPAN_S:
        raise FitFileError(
            f"activity spans {span} s, beyond the {MAX_ACTIVITY_SPAN_S} s limit"
        )

    samples: list[StreamSample] = []
    for offset in range(span + 1):
        at_second = by_offset.get(offset)
        if at_second is None:
            samples.append(StreamSample(offset_s=offset))
            continue
        samples.append(
            StreamSample(
                offset_s=offset,
                hr=at_second["hr"],
                speed_m_s=at_second["speed_m_s"],
                altitude_m=at_second["altitude_m"],
                cadence=at_second["cadence"],
                power=at_second["power"],
                lat=at_second["lat"],
                lon=at_second["lon"],
            )
        )
    return tuple(samples)


def _pace_s_per_km(distance_m: float | None, seconds: int | None) -> float | None:
    if not distance_m or not seconds or distance_m <= 0 or seconds <= 0:
        return None
    return seconds / (distance_m / _SECONDS_PER_KM)


def _laps_from(collected: _Collected) -> tuple[ParsedLap, ...]:
    laps: list[ParsedLap] = []
    for position, raw in enumerate(collected.laps):
        index = raw["index"]
        distance_m = raw["distance_m"]
        duration_s = raw["duration_s"]
        pace = _pace_s_per_km(distance_m, duration_s)
        if pace is None and raw["avg_speed_m_s"]:
            pace = _SECONDS_PER_KM / raw["avg_speed_m_s"]
        laps.append(
            ParsedLap(
                index=position if index is None else index,
                distance_m=distance_m,
                duration_s=duration_s,
                avg_hr=raw["avg_hr"],
                avg_pace_s_per_km=pace,
            )
        )
    return tuple(laps)


def parse_fit_file(path: Path) -> ParsedActivity:
    """Parse one FIT file into a plain, database independent result."""
    collected = _collect(path)
    session = collected.session or {}

    start_utc = (
        session.get("start_time")
        or next(
            (
                record["timestamp"]
                for record in collected.records
                if record["timestamp"] is not None
            ),
            None,
        )
        or (collected.file_id or {}).get("time_created")
    )
    if start_utc is None:
        raise FitFileError(f"{path.name} carries no timestamp to anchor on")

    samples = _build_grid(collected.records, start_utc)
    offset = _utc_offset(collected)
    start_local = (start_utc + (offset or dt.timedelta())).replace(tzinfo=None)

    measured_hr = [sample.hr for sample in samples if sample.hr is not None]
    recorded_seconds = sum(1 for sample in samples if not sample.is_empty)
    record_distances = [
        record["distance_m"]
        for record in collected.records
        if record["distance_m"] is not None
    ]

    elapsed_s = session.get("elapsed_s")
    if elapsed_s is None:
        elapsed_s = samples[-1].offset_s if samples else None

    moving_s = session.get("moving_s") or (collected.activity or {}).get("moving_s")
    if moving_s is None and not collected.session:
        # No session message: the seconds that carry data are the closest
        # honest stand-in for moving time.
        moving_s = recorded_seconds or None

    distance_m = session.get("distance_m")
    if distance_m is None and record_distances:
        distance_m = max(record_distances)

    avg_hr = session.get("avg_hr")
    max_hr = session.get("max_hr")
    if avg_hr is None and not collected.session and measured_hr:
        avg_hr = round(sum(measured_hr) / len(measured_hr))
    if max_hr is None and not collected.session and measured_hr:
        max_hr = max(measured_hr)

    parsed = ParsedActivity(
        start_local=start_local,
        start_utc=start_utc,
        sport=normalise_sport(session.get("sport")),
        distance_m=distance_m,
        moving_s=moving_s,
        elapsed_s=elapsed_s,
        elevation_gain_m=session.get("elevation_gain_m"),
        avg_hr=avg_hr,
        max_hr=max_hr,
        avg_pace_s_per_km=_pace_s_per_km(distance_m, moving_s),
        laps=_laps_from(collected),
        samples=samples,
        derived_summary=collected.session is None,
        local_time_assumed=offset is None,
    )
    log.debug(
        "parsed FIT file",
        extra={
            "file": path.name,
            "samples": len(parsed.samples),
            "gap_seconds": parsed.gap_seconds,
            "derived_summary": parsed.derived_summary,
        },
    )
    return parsed


def fit_activity_id(path: Path) -> str:
    """A stable id for an activity that only exists as a file.

    Derived from the file's contents, so re-importing the same export
    updates the same row instead of adding a second one.
    """
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return f"fit-{digest[:16]}"
