"""A minimal FIT encoder, for tests only.

Real FIT files carry real heart rate data and must never enter this
repository, so the parser tests generate their input instead. This writes
just enough of the FIT binary format for fitdecode to read back: a header,
definition and data messages for file_id, activity, session, lap, event
and record, and the two CRCs.

Not a general purpose encoder — it covers the fields the parser reads.
"""

from __future__ import annotations

import datetime as dt
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

# FIT timestamps count seconds since this epoch, in UTC.
FIT_EPOCH: Final = dt.datetime(1989, 12, 31, tzinfo=dt.UTC)

# Global message numbers from the FIT profile.
MESG_FILE_ID: Final = 0
MESG_SESSION: Final = 18
MESG_LAP: Final = 19
MESG_RECORD: Final = 20
MESG_EVENT: Final = 21
MESG_ACTIVITY: Final = 34

# Base type ids with their byte width and "invalid" sentinel.
UINT8: Final = (0x02, 1, 0xFF)
UINT16: Final = (0x84, 2, 0xFFFF)
UINT32: Final = (0x86, 4, 0xFFFFFFFF)
SINT32: Final = (0x85, 4, 0x7FFFFFFF)
ENUM: Final = (0x00, 1, 0xFF)

_CRC_TABLE: Final = (
    0x0000,
    0xCC01,
    0xD801,
    0x1400,
    0xF001,
    0x3C00,
    0x2800,
    0xE401,
    0xA001,
    0x6C00,
    0x7800,
    0xB401,
    0x5000,
    0x9C01,
    0x8801,
    0x4400,
)

# Degrees per semicircle, the unit FIT stores coordinates in.
SEMICIRCLE: Final = 180.0 / 2**31


def fit_crc(data: bytes, crc: int = 0) -> int:
    for byte in data:
        for nibble in (byte & 0x0F, (byte >> 4) & 0x0F):
            tmp = _CRC_TABLE[crc & 0x0F]
            crc = (crc >> 4) & 0x0FFF
            crc = crc ^ tmp ^ _CRC_TABLE[nibble]
    return crc


def to_fit_timestamp(moment: dt.datetime) -> int:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=dt.UTC)
    return int((moment - FIT_EPOCH).total_seconds())


def to_semicircles(degrees: float) -> int:
    return round(degrees / SEMICIRCLE)


@dataclass(frozen=True, slots=True)
class Field:
    """One field of a message definition."""

    number: int
    base_type: tuple[int, int, int]


@dataclass
class _Definition:
    local_number: int
    global_number: int
    fields: tuple[Field, ...]

    def encode(self) -> bytes:
        header = bytes([0x40 | self.local_number])
        body = struct.pack("<BBHB", 0, 0, self.global_number, len(self.fields))
        for item in self.fields:
            type_id, size, _invalid = item.base_type
            body += struct.pack("<BBB", item.number, size, type_id)
        return header + body


@dataclass
class FitBuilder:
    """Collects messages and renders them as a FIT file."""

    _body: bytearray = field(default_factory=bytearray)
    _definitions: dict[int, _Definition] = field(default_factory=dict)
    _next_local: int = 0

    def message(
        self,
        global_number: int,
        values: dict[int, tuple[tuple[int, int, int], int | None]],
    ) -> None:
        """Append one data message.

        ``values`` maps field number to (base type, raw value). ``None``
        writes the base type's invalid sentinel, which is how FIT says
        "this field is not present in this record".
        """
        fields = tuple(
            Field(number=number, base_type=base_type)
            for number, (base_type, _value) in values.items()
        )
        definition = self._definitions.get(global_number)
        if definition is None or definition.fields != fields:
            definition = _Definition(
                local_number=self._next_local % 16,
                global_number=global_number,
                fields=fields,
            )
            self._next_local += 1
            self._definitions[global_number] = definition
            self._body += definition.encode()

        payload = bytearray([definition.local_number])
        for base_type, value in values.values():
            type_id, size, invalid = base_type
            raw = invalid if value is None else value
            if type_id == SINT32[0]:
                payload += struct.pack("<i", raw)
            elif size == 1:
                payload += struct.pack("<B", raw & 0xFF)
            elif size == 2:
                payload += struct.pack("<H", raw & 0xFFFF)
            else:
                payload += struct.pack("<I", raw & 0xFFFFFFFF)
        self._body += payload

    def render(self) -> bytes:
        header = struct.pack("<BBHI4s", 14, 0x20, 2132, len(self._body), b".FIT")
        header += struct.pack("<H", fit_crc(header))
        content = header + bytes(self._body)
        return content + struct.pack("<H", fit_crc(content))

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(self.render())
        return path


# --- Field numbers, per the FIT profile --------------------------------

_FILE_ID_TYPE = 0
_FILE_ID_MANUFACTURER = 1
_FILE_ID_TIME_CREATED = 4

_ACTIVITY_TIMESTAMP = 253
_ACTIVITY_TOTAL_TIMER_TIME = 0
_ACTIVITY_NUM_SESSIONS = 1
_ACTIVITY_LOCAL_TIMESTAMP = 5

_SESSION_MESSAGE_INDEX = 254
_SESSION_TIMESTAMP = 253
_SESSION_START_TIME = 2
_SESSION_SPORT = 5
_SESSION_TOTAL_ELAPSED_TIME = 7
_SESSION_TOTAL_TIMER_TIME = 8
_SESSION_TOTAL_DISTANCE = 9
_SESSION_AVG_SPEED = 14
_SESSION_AVG_HEART_RATE = 16
_SESSION_MAX_HEART_RATE = 17
_SESSION_TOTAL_ASCENT = 22

_LAP_MESSAGE_INDEX = 254
_LAP_TIMESTAMP = 253
_LAP_START_TIME = 2
_LAP_TOTAL_ELAPSED_TIME = 7
_LAP_TOTAL_TIMER_TIME = 8
_LAP_TOTAL_DISTANCE = 9
_LAP_AVG_SPEED = 13
_LAP_AVG_HEART_RATE = 15

_RECORD_TIMESTAMP = 253
_RECORD_POSITION_LAT = 0
_RECORD_POSITION_LONG = 1
_RECORD_ALTITUDE = 2
_RECORD_HEART_RATE = 3
_RECORD_CADENCE = 4
_RECORD_DISTANCE = 5
_RECORD_SPEED = 6
_RECORD_POWER = 7

_EVENT_TIMESTAMP = 253
_EVENT_EVENT = 0
_EVENT_EVENT_TYPE = 1

SPORT_RUNNING: Final = 1
SPORT_CYCLING: Final = 2
SPORT_WALKING: Final = 11
# 4 is fitness_equipment: a sport Tempo has no category for.
SPORT_FITNESS_EQUIPMENT: Final = 4

EVENT_TIMER: Final = 0
EVENT_TYPE_START: Final = 0
EVENT_TYPE_STOP_ALL: Final = 4


@dataclass(frozen=True, slots=True)
class Sample:
    """One record message. ``None`` means the channel has no value."""

    offset_s: int
    hr: int | None = 140
    speed_m_s: float | None = 2.8
    altitude_m: float | None = 210.0
    cadence: int | None = 82
    power: int | None = None
    lat: float | None = 47.07
    lon: float | None = 15.44
    distance_m: float | None = None


def write_activity_file(
    path: Path,
    *,
    start_utc: dt.datetime,
    samples: list[Sample],
    sport: int = SPORT_RUNNING,
    utc_offset_s: int = 3600,
    elapsed_s: int | None = None,
    timer_s: int | None = None,
    distance_m: float | None = 3500.0,
    total_ascent_m: int | None = 12,
    avg_hr: int | None = 142,
    max_hr: int | None = 168,
    laps: list[tuple[int, int, float, int | None]] | None = None,
    with_session: bool = True,
    with_activity: bool = True,
    stop_event_at: int | None = None,
) -> Path:
    """Write one synthetic activity file.

    ``laps`` entries are ``(start_offset_s, duration_s, distance_m, avg_hr)``.
    ``utc_offset_s`` becomes the difference between the activity message's
    local and UTC timestamps, which is how the parser recovers local time.
    """
    span = samples[-1].offset_s if samples else 0
    elapsed = elapsed_s if elapsed_s is not None else span
    timer = timer_s if timer_s is not None else elapsed

    builder = FitBuilder()
    builder.message(
        MESG_FILE_ID,
        {
            _FILE_ID_TYPE: (ENUM, 4),  # 4 = activity file
            _FILE_ID_MANUFACTURER: (UINT16, 1),
            _FILE_ID_TIME_CREATED: (UINT32, to_fit_timestamp(start_utc)),
        },
    )
    builder.message(
        MESG_EVENT,
        {
            _EVENT_TIMESTAMP: (UINT32, to_fit_timestamp(start_utc)),
            _EVENT_EVENT: (ENUM, EVENT_TIMER),
            _EVENT_EVENT_TYPE: (ENUM, EVENT_TYPE_START),
        },
    )

    for sample in samples:
        moment = start_utc + dt.timedelta(seconds=sample.offset_s)
        builder.message(
            MESG_RECORD,
            {
                _RECORD_TIMESTAMP: (UINT32, to_fit_timestamp(moment)),
                _RECORD_POSITION_LAT: (
                    SINT32,
                    None if sample.lat is None else to_semicircles(sample.lat),
                ),
                _RECORD_POSITION_LONG: (
                    SINT32,
                    None if sample.lon is None else to_semicircles(sample.lon),
                ),
                _RECORD_ALTITUDE: (
                    UINT16,
                    None
                    if sample.altitude_m is None
                    else round((sample.altitude_m + 500.0) * 5),
                ),
                _RECORD_HEART_RATE: (UINT8, sample.hr),
                _RECORD_CADENCE: (UINT8, sample.cadence),
                _RECORD_DISTANCE: (
                    UINT32,
                    None
                    if sample.distance_m is None
                    else round(sample.distance_m * 100),
                ),
                _RECORD_SPEED: (
                    UINT16,
                    None
                    if sample.speed_m_s is None
                    else round(sample.speed_m_s * 1000),
                ),
                _RECORD_POWER: (UINT16, sample.power),
            },
        )

    if stop_event_at is not None:
        builder.message(
            MESG_EVENT,
            {
                _EVENT_TIMESTAMP: (
                    UINT32,
                    to_fit_timestamp(start_utc + dt.timedelta(seconds=stop_event_at)),
                ),
                _EVENT_EVENT: (ENUM, EVENT_TIMER),
                _EVENT_EVENT_TYPE: (ENUM, EVENT_TYPE_STOP_ALL),
            },
        )

    for index, (lap_start, lap_duration, lap_distance, lap_hr) in enumerate(laps or []):
        lap_begin = start_utc + dt.timedelta(seconds=lap_start)
        builder.message(
            MESG_LAP,
            {
                _LAP_MESSAGE_INDEX: (UINT16, index),
                _LAP_TIMESTAMP: (
                    UINT32,
                    to_fit_timestamp(lap_begin + dt.timedelta(seconds=lap_duration)),
                ),
                _LAP_START_TIME: (UINT32, to_fit_timestamp(lap_begin)),
                _LAP_TOTAL_ELAPSED_TIME: (UINT32, lap_duration * 1000),
                _LAP_TOTAL_TIMER_TIME: (UINT32, lap_duration * 1000),
                _LAP_TOTAL_DISTANCE: (UINT32, round(lap_distance * 100)),
                _LAP_AVG_SPEED: (
                    UINT16,
                    round(lap_distance / lap_duration * 1000) if lap_duration else None,
                ),
                _LAP_AVG_HEART_RATE: (UINT8, lap_hr),
            },
        )

    if with_session:
        builder.message(
            MESG_SESSION,
            {
                _SESSION_MESSAGE_INDEX: (UINT16, 0),
                _SESSION_TIMESTAMP: (
                    UINT32,
                    to_fit_timestamp(start_utc + dt.timedelta(seconds=elapsed)),
                ),
                _SESSION_START_TIME: (UINT32, to_fit_timestamp(start_utc)),
                _SESSION_SPORT: (ENUM, sport),
                _SESSION_TOTAL_ELAPSED_TIME: (UINT32, elapsed * 1000),
                _SESSION_TOTAL_TIMER_TIME: (UINT32, timer * 1000),
                _SESSION_TOTAL_DISTANCE: (
                    UINT32,
                    None if distance_m is None else round(distance_m * 100),
                ),
                _SESSION_AVG_SPEED: (
                    UINT16,
                    None
                    if not distance_m or not timer
                    else round(distance_m / timer * 1000),
                ),
                _SESSION_AVG_HEART_RATE: (UINT8, avg_hr),
                _SESSION_MAX_HEART_RATE: (UINT8, max_hr),
                _SESSION_TOTAL_ASCENT: (UINT16, total_ascent_m),
            },
        )

    if with_activity:
        end_utc = start_utc + dt.timedelta(seconds=elapsed)
        builder.message(
            MESG_ACTIVITY,
            {
                _ACTIVITY_TIMESTAMP: (UINT32, to_fit_timestamp(end_utc)),
                _ACTIVITY_TOTAL_TIMER_TIME: (UINT32, timer * 1000),
                _ACTIVITY_NUM_SESSIONS: (UINT16, 1),
                _ACTIVITY_LOCAL_TIMESTAMP: (
                    UINT32,
                    to_fit_timestamp(end_utc) + utc_offset_s,
                ),
            },
        )

    return builder.write(path)
