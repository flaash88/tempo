"""Best efforts, critical speed, VDOT and race predictions.

Best efforts come out of the one hertz stream, and a window that overlaps
an unrecorded second is skipped rather than closed up — a "best 5 minutes"
assembled across a traffic light is not a best five minutes.

Riegel and VDOT predictions are both reported and never averaged. They
disagree in a way that is informative: Riegel extrapolates from one
performance, VDOT places it on a physiological curve, and a gap between
them says something about endurance relative to speed that a single blended
number would hide.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

from tempo.metrics.thresholds import (
    CRITICAL_SPEED_MAX_DURATION_S,
    CRITICAL_SPEED_MIN_DURATION_S,
    MIN_PERFORMANCES_CRITICAL_SPEED,
    PEAK_DURATIONS_S,
    RIEGEL_EXPONENT,
)

# Daniels' VDOT relations. The percentage of VO2max sustainable for a
# duration, and the oxygen cost of a running velocity.
_PCT_VO2MAX_BASE = 0.8
_PCT_VO2MAX_SLOW_FACTOR = 0.1894393
_PCT_VO2MAX_SLOW_RATE = -0.012778
_PCT_VO2MAX_FAST_FACTOR = 0.2989558
_PCT_VO2MAX_FAST_RATE = -0.1932605

_VO2_INTERCEPT = -4.60
_VO2_LINEAR = 0.182258
_VO2_QUADRATIC = 0.000104

# Bracket for inverting the VDOT relation, in velocity rather than in
# duration: the oxygen cost relation turns negative below roughly 25 m/min,
# so a duration bracket wide enough for a marathon would run off the end of
# the model for a 5k. 60 m/min is a walk, 400 m/min is world class, and
# every running performance sits between them at every distance.
_SOLVE_MIN_VELOCITY_M_PER_MIN = 60.0
_SOLVE_MAX_VELOCITY_M_PER_MIN = 400.0
_SOLVE_ITERATIONS = 80

RACE_DISTANCES_M: dict[str, float] = {
    "5k": 5_000.0,
    "10k": 10_000.0,
    "half_marathon": 21_097.5,
    "marathon": 42_195.0,
}


@dataclass(frozen=True, slots=True)
class BestEffort:
    """The most distance covered in a given time."""

    duration_s: int
    distance_m: float

    @property
    def speed_m_s(self) -> float:
        return self.distance_m / self.duration_s

    @property
    def pace_s_per_km(self) -> float:
        return self.duration_s / (self.distance_m / 1000.0)


@dataclass(frozen=True, slots=True)
class CriticalSpeed:
    """The asymptote of the distance-time line, and the reserve above it.

    ``d = CS · t + D'``: ``cs_m_s`` is the speed theoretically sustainable
    indefinitely, ``d_prime_m`` the finite distance available above it.
    """

    cs_m_s: float
    d_prime_m: float
    points: int
    r_squared: float


@dataclass(frozen=True, slots=True)
class Prediction:
    """A race prediction from one method, in seconds."""

    distance_m: float
    seconds: float
    method: str


def best_effort(speeds: Sequence[float | None], duration_s: int) -> BestEffort | None:
    """The best distance covered in any complete window of that length.

    Each sample is one second, so the distance over a window is the sum of
    its speeds. Windows containing an unrecorded second are skipped: a best
    effort stitched across a pause never happened.
    """
    if duration_s <= 0:
        raise ValueError("duration must be positive")
    if len(speeds) < duration_s:
        return None

    # Prefix sums of speed and of "was this second measured", so each
    # window is two subtractions rather than a scan.
    total = [0.0]
    measured = [0]
    for value in speeds:
        total.append(total[-1] + (value or 0.0))
        measured.append(measured[-1] + (1 if value is not None else 0))

    best: float | None = None
    for start in range(len(speeds) - duration_s + 1):
        end = start + duration_s
        if measured[end] - measured[start] != duration_s:
            continue
        distance = total[end] - total[start]
        if best is None or distance > best:
            best = distance
    if best is None:
        return None
    return BestEffort(duration_s=duration_s, distance_m=best)


def best_efforts(
    speeds: Sequence[float | None],
    durations: Sequence[int] = PEAK_DURATIONS_S,
) -> dict[int, BestEffort]:
    """Best efforts for each duration the recording is long enough for."""
    found: dict[int, BestEffort] = {}
    for duration in durations:
        effort = best_effort(speeds, duration)
        if effort is not None:
            found[duration] = effort
    return found


def critical_speed(efforts: Sequence[BestEffort]) -> CriticalSpeed | None:
    """Least squares fit of ``d = CS · t + D'`` over qualifying efforts.

    Only efforts inside the duration band count: below it anaerobic
    contribution dominates the line, above it the model drifts. Fewer than
    the minimum number of points gives ``None`` rather than a line drawn
    through two dots.
    """
    usable = [
        effort
        for effort in efforts
        if CRITICAL_SPEED_MIN_DURATION_S
        <= effort.duration_s
        <= CRITICAL_SPEED_MAX_DURATION_S
        and effort.distance_m > 0
    ]
    # One point per duration: a duplicate would weight it twice.
    by_duration = {effort.duration_s: effort for effort in usable}
    points = sorted(by_duration.values(), key=lambda effort: effort.duration_s)
    if len(points) < MIN_PERFORMANCES_CRITICAL_SPEED:
        return None

    times = [float(effort.duration_s) for effort in points]
    distances = [effort.distance_m for effort in points]
    count = len(points)
    mean_t = sum(times) / count
    mean_d = sum(distances) / count
    variance_t = sum((time - mean_t) ** 2 for time in times)
    if variance_t <= 0:
        return None
    covariance = sum(
        (time - mean_t) * (distance - mean_d)
        for time, distance in zip(times, distances, strict=True)
    )
    slope = covariance / variance_t
    intercept = mean_d - slope * mean_t
    if slope <= 0:
        # A negative asymptote is not a speed; the fit says nothing usable.
        return None

    residuals = sum(
        (distance - (slope * time + intercept)) ** 2
        for time, distance in zip(times, distances, strict=True)
    )
    total = sum((distance - mean_d) ** 2 for distance in distances)
    r_squared = 1.0 - residuals / total if total > 0 else 1.0

    return CriticalSpeed(
        cs_m_s=slope,
        d_prime_m=intercept,
        points=count,
        r_squared=r_squared,
    )


def percent_vo2max(minutes: float) -> float:
    """Fraction of VO2max sustainable for a duration, per Daniels."""
    return (
        _PCT_VO2MAX_BASE
        + _PCT_VO2MAX_SLOW_FACTOR * math.exp(_PCT_VO2MAX_SLOW_RATE * minutes)
        + _PCT_VO2MAX_FAST_FACTOR * math.exp(_PCT_VO2MAX_FAST_RATE * minutes)
    )


def oxygen_cost(velocity_m_per_min: float) -> float:
    """Oxygen cost of a running velocity, in ml/kg/min, per Daniels."""
    return (
        _VO2_INTERCEPT
        + _VO2_LINEAR * velocity_m_per_min
        + _VO2_QUADRATIC * velocity_m_per_min**2
    )


def vdot(distance_m: float, seconds: float) -> float | None:
    """VDOT for a performance, per Daniels."""
    if distance_m <= 0 or seconds <= 0:
        return None
    minutes = seconds / 60.0
    velocity = distance_m / minutes
    cost = oxygen_cost(velocity)
    fraction = percent_vo2max(minutes)
    if cost <= 0 or fraction <= 0:
        return None
    return cost / fraction


def vdot_prediction(vdot_value: float, distance_m: float) -> float | None:
    """The time a VDOT predicts for a distance, in seconds.

    Inverted by bisection: the relation is monotonic in duration, so the
    bracket closes on the one answer. ``None`` when the target lies outside
    the bracket, which means the VDOT or the distance is not a running
    performance.
    """
    if vdot_value <= 0 or distance_m <= 0:
        return None

    def implied(velocity_m_per_min: float) -> float | None:
        return vdot(distance_m, distance_m / velocity_m_per_min * 60.0)

    slow_end, fast_end = _SOLVE_MIN_VELOCITY_M_PER_MIN, _SOLVE_MAX_VELOCITY_M_PER_MIN
    slowest, fastest = implied(slow_end), implied(fast_end)
    if slowest is None or fastest is None:
        return None
    # VDOT rises with velocity, so the target has to sit between the ends.
    if not slowest <= vdot_value <= fastest:
        return None

    for _ in range(_SOLVE_ITERATIONS):
        middle = (slow_end + fast_end) / 2
        value = implied(middle)
        if value is None:
            return None
        if value < vdot_value:
            slow_end = middle
        else:
            fast_end = middle
    velocity = (slow_end + fast_end) / 2
    return distance_m / velocity * 60.0


def riegel_prediction(
    known_distance_m: float, known_seconds: float, target_distance_m: float
) -> float | None:
    """Riegel: ``T2 = T1 · (D2 / D1) ** 1.06``."""
    if known_distance_m <= 0 or known_seconds <= 0 or target_distance_m <= 0:
        return None
    ratio = target_distance_m / known_distance_m
    return float(known_seconds * ratio**RIEGEL_EXPONENT)


def race_predictions(
    known_distance_m: float,
    known_seconds: float,
    distances: dict[str, float] | None = None,
) -> dict[str, list[Prediction]]:
    """Both predictions for each race distance, side by side.

    Never averaged: where Riegel and VDOT disagree, the disagreement is the
    information.
    """
    targets = distances if distances is not None else RACE_DISTANCES_M
    reference_vdot = vdot(known_distance_m, known_seconds)

    predictions: dict[str, list[Prediction]] = {}
    for name, distance in targets.items():
        entries: list[Prediction] = []
        riegel = riegel_prediction(known_distance_m, known_seconds, distance)
        if riegel is not None:
            entries.append(
                Prediction(distance_m=distance, seconds=riegel, method="riegel")
            )
        if reference_vdot is not None:
            from_vdot = vdot_prediction(reference_vdot, distance)
            if from_vdot is not None:
                entries.append(
                    Prediction(distance_m=distance, seconds=from_vdot, method="vdot")
                )
        if entries:
            predictions[name] = entries
    return predictions
