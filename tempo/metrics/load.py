"""Training load.

Every formula here is stated in the plan and implemented literally, with
the coefficients living in ``thresholds``. All of it is pure: sequences in,
numbers out, so each formula can be checked against a hand-computed case.

Two habits run through the module. Anything that cannot be computed
returns ``None`` rather than a zero — a run without heart rate has no
TRIMP, and calling that nought would let it drag a fitness curve down as if
the athlete had rested. And a gap in the recording is never bridged: a
window that overlaps unmeasured seconds is skipped, not filled in.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from statistics import median

from tempo.metrics.thresholds import (
    ALTITUDE_MEDIAN_WINDOW_S,
    DECOUPLING_MIN_DURATION_S,
    EDWARDS_ZONE_WEIGHTS,
    GAP_MAX_GRADE,
    MINETTI_RUN_COST_COEFFICIENTS,
    NGP_NORMALISATION_POWER,
    NGP_ROLLING_WINDOW_S,
    SECONDS_PER_HOUR,
    TRIMP_BANISTER_EXPONENT,
    TRIMP_BANISTER_FACTOR,
    TSS_AT_THRESHOLD_HOUR,
)

# Cost of running on the flat, the reference the gradient correction is
# expressed against.
FLAT_RUN_COST_J_PER_KG_M = MINETTI_RUN_COST_COEFFICIENTS[-1]


def heart_rate_reserve(
    hr_avg: float | None, hr_rest: int | None, hr_max: int | None
) -> float | None:
    """Fraction of the heart rate reserve used: (HR - rest) / (max - rest).

    ``None`` unless all three numbers are there and the reserve is a real
    span. Clamped to 0..1, because a single sample above the recorded
    maximum is a stale maximum, not an intensity above total effort.
    """
    if hr_avg is None or hr_rest is None or hr_max is None:
        return None
    reserve = hr_max - hr_rest
    if reserve <= 0:
        return None
    return min(1.0, max(0.0, (hr_avg - hr_rest) / reserve))


def trimp_banister(
    duration_s: int | None,
    hr_avg: float | None,
    hr_rest: int | None,
    hr_max: int | None,
) -> float | None:
    """Banister TRIMP: t · HRr · 0.64 · e^(1.92 · HRr), t in minutes."""
    if not duration_s or duration_s <= 0:
        return None
    hrr = heart_rate_reserve(hr_avg, hr_rest, hr_max)
    if hrr is None:
        return None
    minutes = duration_s / 60.0
    return (
        minutes * hrr * TRIMP_BANISTER_FACTOR * math.exp(TRIMP_BANISTER_EXPONENT * hrr)
    )


def trimp_edwards(zone_seconds: Sequence[int]) -> float | None:
    """Edwards TRIMP: minutes in each zone, weighted one through five."""
    if len(zone_seconds) != len(EDWARDS_ZONE_WEIGHTS):
        raise ValueError(f"expected {len(EDWARDS_ZONE_WEIGHTS)} zone durations")
    if not any(zone_seconds):
        return None
    return sum(
        (seconds / 60.0) * weight
        for seconds, weight in zip(zone_seconds, EDWARDS_ZONE_WEIGHTS, strict=True)
    )


def minetti_run_cost(grade: float) -> float:
    """Metabolic cost of running at a gradient, in J/kg/m.

    Minetti et al. 2002, as a polynomial in the gradient.
    """
    cost = 0.0
    for coefficient in MINETTI_RUN_COST_COEFFICIENTS:
        cost = cost * grade + coefficient
    return cost


def grade_adjusted_speed(speed_m_s: float, grade: float) -> float:
    """The flat speed that would cost the same as this speed on this hill.

    A gradient beyond the outlier limit is discarded rather than trusted:
    barometric altitude at one second resolution throws spikes that would
    otherwise turn into imaginary hills.
    """
    if abs(grade) > GAP_MAX_GRADE:
        return speed_m_s
    return speed_m_s * minetti_run_cost(grade) / FLAT_RUN_COST_J_PER_KG_M


def smooth_altitude(
    altitudes: Sequence[float | None],
    window_s: int = ALTITUDE_MEDIAN_WINDOW_S,
) -> tuple[float | None, ...]:
    """Median smoothed altitude over a centred window.

    Only measured values inside the window count; a window with none stays
    ``None``, so an unrecorded stretch does not acquire an altitude.
    """
    if window_s < 1:
        raise ValueError("window must be at least one second")
    half = window_s // 2
    smoothed: list[float | None] = []
    for index in range(len(altitudes)):
        start = max(0, index - half)
        window = [
            value for value in altitudes[start : index + half + 1] if value is not None
        ]
        smoothed.append(median(window) if window else None)
    return tuple(smoothed)


def grades(
    altitudes: Sequence[float | None], speeds: Sequence[float | None]
) -> tuple[float | None, ...]:
    """Gradient per second, from smoothed altitude and horizontal speed.

    Undefined where either altitude is missing, where the previous second
    was not measured, or where the athlete was barely moving — dividing a
    real altitude change by a near-zero distance manufactures a cliff.
    """
    if len(altitudes) != len(speeds):
        raise ValueError("altitude and speed series must be the same length")
    smoothed = smooth_altitude(altitudes)
    result: list[float | None] = [None] * len(smoothed)
    for index in range(1, len(smoothed)):
        here, before = smoothed[index], smoothed[index - 1]
        speed = speeds[index]
        if here is None or before is None or speed is None or speed <= 0.5:
            continue
        result[index] = (here - before) / speed
    return tuple(result)


def gap_speeds(
    speeds: Sequence[float | None], altitudes: Sequence[float | None]
) -> tuple[float | None, ...]:
    """Grade adjusted speed per second.

    Seconds with no speed stay ``None``; seconds with a speed but no usable
    gradient are taken as they are, which is the flat case.
    """
    gradient = grades(altitudes, speeds)
    adjusted: list[float | None] = []
    for speed, grade in zip(speeds, gradient, strict=True):
        if speed is None:
            adjusted.append(None)
        elif grade is None:
            adjusted.append(speed)
        else:
            adjusted.append(grade_adjusted_speed(speed, grade))
    return tuple(adjusted)


def rolling_mean(values: Sequence[float | None], window_s: int) -> tuple[float, ...]:
    """Rolling means over complete windows only.

    A window that overlaps an unmeasured second is skipped rather than
    averaged over a shorter span: that is the difference between a pause and
    a slow patch, and blurring it is what the no-interpolation rule forbids.
    """
    if window_s < 1:
        raise ValueError("window must be at least one second")
    means: list[float] = []
    for start in range(0, len(values) - window_s + 1):
        window = values[start : start + window_s]
        if any(value is None for value in window):
            continue
        means.append(sum(value for value in window if value is not None) / window_s)
    return tuple(means)


def normalised_graded_speed(
    speeds: Sequence[float | None],
    altitudes: Sequence[float | None],
) -> float | None:
    """Normalised graded pace, as a speed in metres per second.

    The rolling mean of grade adjusted speed, then the fourth-power mean of
    those — which is what weights the hard patches the way the body pays for
    them. ``None`` when no complete window survives, for instance in a
    recording that is shorter than the window itself.
    """
    windows = rolling_mean(gap_speeds(speeds, altitudes), NGP_ROLLING_WINDOW_S)
    if not windows:
        return None
    power = NGP_NORMALISATION_POWER
    mean_of_powers = sum(value**power for value in windows) / len(windows)
    return float(mean_of_powers ** (1.0 / power))


def intensity_factor(
    normalised_speed: float | None, threshold_speed_m_s: float | None
) -> float | None:
    """NGP as a fraction of threshold speed."""
    if normalised_speed is None or not threshold_speed_m_s:
        return None
    if threshold_speed_m_s <= 0:
        return None
    return normalised_speed / threshold_speed_m_s


def r_tss(moving_s: int | None, factor: float | None) -> float | None:
    """Running TSS: moving seconds · IF² / 3600 · 100."""
    if not moving_s or moving_s <= 0 or factor is None:
        return None
    return (moving_s * factor**2 / SECONDS_PER_HOUR) * TSS_AT_THRESHOLD_HOUR


def trimp_at_threshold_hour(
    lthr: int | None, hr_rest: int | None, hr_max: int | None
) -> float | None:
    """Banister TRIMP for one hour at threshold — the hrTSS reference."""
    return trimp_banister(SECONDS_PER_HOUR, lthr, hr_rest, hr_max)


def hr_tss(trimp: float | None, reference_trimp: float | None) -> float | None:
    """Heart rate TSS: TRIMP scaled so an hour at threshold is 100."""
    if trimp is None or not reference_trimp or reference_trimp <= 0:
        return None
    return trimp / reference_trimp * TSS_AT_THRESHOLD_HOUR


def efficiency_factor(
    normalised_speed: float | None, hr_avg: float | None
) -> float | None:
    """Normalised graded speed per heartbeat: NGP in m/s over mean heart rate."""
    if normalised_speed is None or not hr_avg or hr_avg <= 0:
        return None
    return normalised_speed / hr_avg


def mean_of(values: Sequence[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    if not present:
        return None
    return sum(present) / len(present)


def decoupling(
    speeds: Sequence[float | None],
    altitudes: Sequence[float | None],
    heart_rates: Sequence[float | None],
) -> float | None:
    """Pa:HR decoupling — how far efficiency fell across the session.

    ``(EF_first_half - EF_second_half) / EF_first_half``. Only for sessions
    of at least the minimum duration whose heart rate ran throughout: a
    dropout in the middle would compare two different things, and a value
    that looks like drift but is a missing strap reading is worse than no
    value at all.
    """
    length = len(speeds)
    if length != len(altitudes) or length != len(heart_rates):
        raise ValueError("series must be the same length")
    if length < DECOUPLING_MIN_DURATION_S:
        return None
    if any(rate is None for rate in heart_rates):
        return None
    if any(speed is None for speed in speeds):
        return None

    middle = length // 2
    halves: list[float] = []
    for start, end in ((0, middle), (middle, length)):
        speed = normalised_graded_speed(speeds[start:end], altitudes[start:end])
        rate = mean_of(heart_rates[start:end])
        factor = efficiency_factor(speed, rate)
        if factor is None or factor <= 0:
            return None
        halves.append(factor)

    first, second = halves
    return (first - second) / first
