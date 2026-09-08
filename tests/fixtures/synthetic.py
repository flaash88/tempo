"""Builders for synthetic records.

Deliberately not a copy of the design mock-ups: those numbers are invented
and partly inconsistent with each other.
"""

from __future__ import annotations

import datetime as dt

from tempo.db.models import (
    Activity,
    ActivityStream,
    DataSource,
    Lap,
    WellnessDay,
)

# A fixed reference day so that no test depends on the wall clock.
REFERENCE_DATE = dt.date(2026, 1, 15)


def make_activity(
    activity_id: str = "syn-1",
    *,
    start_local: dt.datetime | None = None,
    sport: str = "Run",
    distance_m: float | None = 3500.0,
    moving_s: int | None = 1260,
    avg_hr: int | None = 142,
) -> Activity:
    return Activity(
        id=activity_id,
        start_local=start_local or dt.datetime.combine(REFERENCE_DATE, dt.time(7, 30)),
        sport=sport,
        distance_m=distance_m,
        moving_s=moving_s,
        elapsed_s=moving_s,
        elevation_gain_m=12.0,
        avg_hr=avg_hr,
        max_hr=None if avg_hr is None else avg_hr + 20,
        avg_pace_s_per_km=(
            None if not distance_m or not moving_s else moving_s / (distance_m / 1000.0)
        ),
        source=DataSource.INTERVALS,
        fit_path=f"fit/{activity_id}.fit",
    )


def make_stream(
    activity_id: str = "syn-1", *, seconds: int = 5, with_hr: bool = True
) -> list[ActivityStream]:
    """A short 1 Hz stream. Second 2 has no heart rate — dropouts happen."""
    samples: list[ActivityStream] = []
    for offset in range(seconds):
        hr: int | None = None
        if with_hr and offset != 2:
            hr = 130 + offset
        samples.append(
            ActivityStream(
                activity_id=activity_id,
                offset_s=offset,
                hr=hr,
                speed_m_s=2.8,
                altitude_m=210.0 + offset * 0.2,
                cadence=82,
                power=None,
                lat=None,
                lon=None,
            )
        )
    return samples


def make_lap(activity_id: str = "syn-1", index: int = 0) -> Lap:
    return Lap(
        activity_id=activity_id,
        index=index,
        distance_m=1000.0,
        duration_s=360,
        avg_hr=140,
        avg_pace_s_per_km=360.0,
    )


def make_wellness_block(
    start: dt.date, days: int, *, with_hrv: bool = True
) -> list[WellnessDay]:
    """A contiguous block of wellness days, as the real data comes in."""
    return [
        WellnessDay(
            date=start + dt.timedelta(days=offset),
            resting_hr=52 + (offset % 3),
            hrv=44.0 + (offset % 5) if with_hrv else None,
            hrv_source_field="hrv" if with_hrv else None,
            sleep_secs=25_200,
            sleep_score=72,
            vo2max=None,
            weight_kg=None,
            source=DataSource.INTERVALS,
        )
        for offset in range(days)
    ]
