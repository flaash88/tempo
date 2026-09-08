"""Training zones.

Heart rate zones come from threshold heart rate (Friel's run model, the
default) or from maximum heart rate; pace zones come from threshold pace.
All three are expressed the same way — five ascending lower bounds in the
value's own unit — so one lookup and one time-in-zone routine serve them
all.

Pace zones are held as *speeds* rather than paces. A faster pace is a
higher zone, and speed ascends with effort the way heart rate does, so
holding both the same way removes a class of inverted-comparison bug.

Zone colours describe, they do not judge: nothing here decides whether a
zone is good, only which zone a value falls in.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from tempo.metrics.thresholds import (
    FRIEL_LTHR_ZONE_LOWER_BOUNDS,
    FRIEL_PACE_ZONE_LOWER_BOUNDS,
    PERCENT_HR_MAX_ZONE_LOWER_BOUNDS,
    ZONE_COUNT,
)

Kind = Literal["hr", "speed"]
Model = Literal["friel_run_lthr", "percent_hr_max", "friel_run_pace"]

_SECONDS_PER_KM = 1000.0


@dataclass(frozen=True, slots=True)
class ZoneSet:
    """Five zones, as ascending lower bounds in the value's own unit."""

    kind: Kind
    model: Model
    lower_bounds: tuple[float, ...]

    def __post_init__(self) -> None:
        if len(self.lower_bounds) != ZONE_COUNT:
            raise ValueError(f"expected {ZONE_COUNT} zone bounds")
        if list(self.lower_bounds) != sorted(self.lower_bounds):
            raise ValueError("zone bounds must ascend")


@dataclass(frozen=True, slots=True)
class ZoneTimes:
    """Seconds spent in each zone, plus what did not land in one.

    ``below`` and ``unknown`` are kept apart from the zones and from each
    other: time under zone 1 was measured and was simply too easy to count
    as training, while unknown time was never measured at all.
    """

    seconds: tuple[int, ...]
    below: int = 0
    unknown: int = 0

    @property
    def total_in_zones(self) -> int:
        return sum(self.seconds)

    @property
    def minutes(self) -> tuple[float, ...]:
        return tuple(value / 60.0 for value in self.seconds)


def hr_zones_from_lthr(lthr: int) -> ZoneSet:
    """Friel's five run zones from lactate threshold heart rate."""
    if lthr <= 0:
        raise ValueError("lthr must be positive")
    return ZoneSet(
        kind="hr",
        model="friel_run_lthr",
        lower_bounds=tuple(
            fraction * lthr for fraction in FRIEL_LTHR_ZONE_LOWER_BOUNDS
        ),
    )


def hr_zones_from_hr_max(hr_max: int) -> ZoneSet:
    """Five zones as fractions of maximum heart rate."""
    if hr_max <= 0:
        raise ValueError("hr_max must be positive")
    return ZoneSet(
        kind="hr",
        model="percent_hr_max",
        lower_bounds=tuple(
            fraction * hr_max for fraction in PERCENT_HR_MAX_ZONE_LOWER_BOUNDS
        ),
    )


def pace_zones_from_threshold(threshold_pace_s_per_km: float) -> ZoneSet:
    """Friel's run pace zones, as speeds in metres per second."""
    if threshold_pace_s_per_km <= 0:
        raise ValueError("threshold pace must be positive")
    threshold_speed = _SECONDS_PER_KM / threshold_pace_s_per_km
    return ZoneSet(
        kind="speed",
        model="friel_run_pace",
        lower_bounds=tuple(
            fraction * threshold_speed for fraction in FRIEL_PACE_ZONE_LOWER_BOUNDS
        ),
    )


def speed_to_pace_s_per_km(speed_m_s: float) -> float | None:
    """Pace for a speed, or None when standing still."""
    if speed_m_s <= 0:
        return None
    return _SECONDS_PER_KM / speed_m_s


def zone_of(value: float | None, zones: ZoneSet) -> int | None:
    """The zone a value falls in, 1 to 5, or None if it falls below them.

    Below the first bound is not zone 1: on the %HRmax model that region is
    resting heart rate, and counting it as easy training would inflate every
    load number built on time in zone.
    """
    if value is None:
        return None
    if value < zones.lower_bounds[0]:
        return None
    zone = 1
    for index, bound in enumerate(zones.lower_bounds):
        if value >= bound:
            zone = index + 1
    return zone


def time_in_zones(values: Sequence[float | None], zones: ZoneSet) -> ZoneTimes:
    """Seconds per zone over a one hertz series.

    Each sample counts as one second — which is what the parser's dense
    grid guarantees. A sample with no value adds to ``unknown`` rather than
    being dropped silently, so a recording with dropouts cannot look like a
    shorter session.
    """
    seconds = [0] * ZONE_COUNT
    below = 0
    unknown = 0
    for value in values:
        if value is None:
            unknown += 1
            continue
        zone = zone_of(value, zones)
        if zone is None:
            below += 1
        else:
            seconds[zone - 1] += 1
    return ZoneTimes(seconds=tuple(seconds), below=below, unknown=unknown)


def zones_for_athlete(
    *,
    lthr: int | None,
    hr_max: int | None,
    zone_model: str | None = None,
) -> ZoneSet | None:
    """The heart rate zones an athlete's settings support.

    Friel's threshold model is the default and is preferred whenever a
    threshold heart rate is known; %HRmax is the fallback. With neither
    number there are no zones, and nothing is guessed from age.
    """
    if zone_model == "percent_hr_max" and hr_max:
        return hr_zones_from_hr_max(hr_max)
    if lthr:
        return hr_zones_from_lthr(lthr)
    if hr_max:
        return hr_zones_from_hr_max(hr_max)
    return None
