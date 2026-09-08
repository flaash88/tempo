"""Wellness baselines and readiness.

**Nothing here interprets what the heart rate variability number is.** The
source's own field name travels with every reading, and the only thing this
module does with it is refuse to mix two of them: a baseline built from one
measure cannot absorb a value from another, so a change of source field ends
the window exactly as a gap in the data would. The transform, the rolling
mean and the band are arithmetic on whatever the value is; no code here says
"rMSSD", and no text produced here claims to.

The reset rule is the other thing that shapes the module. After more than
:data:`~tempo.metrics.thresholds.BASELINE_RESET_GAP_DAYS` days without
readings the baseline is discarded and built again. There is no averaging
across a gap, in any direction, ever — which is why three blocks of
readings months apart produce one baseline at most, not a blend of three.
"""

from __future__ import annotations

import datetime as dt
import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import pstdev

from tempo.metrics.confidence import (
    MetricResult,
    current_window,
    from_window,
    newest_of,
)
from tempo.metrics.thresholds import (
    HRV_BAND_SD_MULTIPLIER,
    HRV_REFERENCE_WINDOW_DAYS,
    HRV_ROLLING_MEAN_DAYS,
    MIN_DAYS_HRV_BASELINE,
    MIN_NIGHTS_READINESS,
    READINESS_DEVIATION_CLAMP_SD,
    READINESS_MIN_COMPONENTS,
    READINESS_SLEEP_TARGET_S,
    READINESS_TSB_RANGE,
    READINESS_WEIGHT_HRV,
    READINESS_WEIGHT_RESTING_HR,
    READINESS_WEIGHT_SLEEP,
    READINESS_WEIGHT_TSB,
)


@dataclass(frozen=True, slots=True)
class Reading:
    """One day's value, with the source field it came from.

    ``source_field`` is carried, not interpreted. It exists so that a
    baseline can tell whether the series it is built from is one quantity or
    two.
    """

    date: dt.date
    value: float
    source_field: str | None = None


@dataclass(frozen=True, slots=True)
class Baseline:
    """A rolling baseline and the band around it.

    ``log_transformed`` says whether the numbers are the readings or their
    natural logarithms, because the deviation of a reading has to be
    measured in the same space the baseline was built in.
    """

    mean: float
    sd: float
    days: int
    last_data_point: dt.date
    source_field: str | None = None
    log_transformed: bool = False

    @property
    def lower(self) -> float:
        return self.mean - HRV_BAND_SD_MULTIPLIER * self.sd

    @property
    def upper(self) -> float:
        return self.mean + HRV_BAND_SD_MULTIPLIER * self.sd

    def deviation_sd(self, value: float | None) -> float | None:
        """How far a reading sits from the baseline, in standard deviations.

        Applies the same transform the baseline was built with, so a reading
        is never compared against a mean computed in another space.
        """
        if value is None or self.sd <= 0:
            return None
        if self.log_transformed:
            if value <= 0:
                return None
            value = math.log(value)
        return (value - self.mean) / self.sd


@dataclass(frozen=True, slots=True)
class ReadinessComponents:
    """The inputs that went into a readiness score, each 0..100 or absent.

    Kept alongside the score so the interface and the AI layer can say what
    the number is made of, and say what was missing, rather than presenting
    a figure whose basis is invisible.
    """

    hrv: float | None = None
    resting_hr: float | None = None
    sleep: float | None = None
    tsb: float | None = None

    @property
    def present(self) -> int:
        return sum(
            1
            for value in (self.hrv, self.resting_hr, self.sleep, self.tsb)
            if value is not None
        )


def homogeneous_tail(readings: Sequence[Reading]) -> tuple[Reading, ...]:
    """The most recent run of readings that share one source field.

    A baseline spanning two different measures would be a blend of two
    quantities. Where the field changes, the older part is dropped and the
    baseline rebuilds — the same treatment a gap in the data gets.
    """
    if not readings:
        return ()
    ordered = sorted(readings, key=lambda reading: reading.date)
    field = ordered[-1].source_field
    start = 0
    for index in range(len(ordered) - 1, -1, -1):
        if ordered[index].source_field != field:
            start = index + 1
            break
    return tuple(ordered[start:])


def _rolling_means(values: Sequence[float], window: int) -> list[float]:
    """Rolling means over complete windows, oldest first."""
    if len(values) < window:
        return []
    return [
        sum(values[start : start + window]) / window
        for start in range(len(values) - window + 1)
    ]


def build_baseline(
    readings: Sequence[Reading],
    *,
    as_of: dt.date,
    required_days: int,
    log_transform: bool,
) -> Baseline | None:
    """A rolling baseline over the current, single-measure window.

    Returns ``None`` unless the window reaches up to ``as_of``, holds one
    measure throughout, and is at least ``required_days`` long. The reference
    window is capped so that a year of readings does not flatten a change
    that happened last month.
    """
    tail = homogeneous_tail(readings)
    if not tail:
        return None

    by_date = {reading.date: reading for reading in tail}
    window = current_window(by_date, as_of=as_of)
    if len(window) < required_days:
        return None

    usable = [
        by_date[day] for day in window if not log_transform or by_date[day].value > 0
    ]
    if len(usable) < required_days:
        return None

    values = [
        math.log(reading.value) if log_transform else reading.value
        for reading in usable
    ]
    means = _rolling_means(values, HRV_ROLLING_MEAN_DAYS)
    if not means:
        return None
    reference = means[-HRV_REFERENCE_WINDOW_DAYS:]

    return Baseline(
        mean=reference[-1],
        sd=pstdev(reference) if len(reference) > 1 else 0.0,
        days=len(usable),
        last_data_point=usable[-1].date,
        source_field=usable[-1].source_field,
        log_transformed=log_transform,
    )


def hrv_baseline(
    readings: Sequence[Reading], *, as_of: dt.date
) -> MetricResult[Baseline]:
    """The heart rate variability baseline, with its evidence.

    The values are log transformed before averaging. That is a property of
    how variability measures are distributed, not a claim about which
    measure this one is — and the transform is recorded on the baseline so
    every later comparison happens in the same space.
    """
    tail = homogeneous_tail(readings)
    window = current_window((reading.date for reading in tail), as_of=as_of)
    baseline = build_baseline(
        readings,
        as_of=as_of,
        required_days=MIN_DAYS_HRV_BASELINE,
        log_transform=True,
    )
    return from_window(
        baseline,
        window=window,
        required=MIN_DAYS_HRV_BASELINE,
        as_of=as_of,
        newest_data_point=newest_of(reading.date for reading in readings),
    )


def resting_hr_baseline(
    readings: Sequence[Reading], *, as_of: dt.date
) -> MetricResult[Baseline]:
    """The resting heart rate baseline, built the same way.

    Without the log transform: the rolling mean and the band carry over, the
    transform does not, because resting heart rate is not distributed like a
    variability measure.
    """
    window = current_window((reading.date for reading in readings), as_of=as_of)
    baseline = build_baseline(
        readings,
        as_of=as_of,
        required_days=MIN_DAYS_HRV_BASELINE,
        log_transform=False,
    )
    return from_window(
        baseline,
        window=window,
        required=MIN_DAYS_HRV_BASELINE,
        as_of=as_of,
        newest_data_point=newest_of(reading.date for reading in readings),
    )


def _clamped_deviation_score(
    deviation_sd: float | None, *, higher_is_better: bool
) -> float | None:
    """Map a deviation in standard deviations onto 0..100, 50 at baseline."""
    if deviation_sd is None:
        return None
    clamped = max(
        -READINESS_DEVIATION_CLAMP_SD,
        min(READINESS_DEVIATION_CLAMP_SD, deviation_sd),
    )
    fraction = clamped / READINESS_DEVIATION_CLAMP_SD
    if not higher_is_better:
        fraction = -fraction
    return 50.0 + 50.0 * fraction


def _sleep_score(sleep_secs: int | None) -> float | None:
    if sleep_secs is None or sleep_secs < 0:
        return None
    return 100.0 * min(1.0, sleep_secs / READINESS_SLEEP_TARGET_S)


def _tsb_score(tsb: float | None) -> float | None:
    if tsb is None:
        return None
    low, high = READINESS_TSB_RANGE
    if high <= low:
        return None
    return 100.0 * min(1.0, max(0.0, (tsb - low) / (high - low)))


def readiness_components(
    *,
    hrv_deviation_sd: float | None,
    resting_hr_deviation_sd: float | None,
    sleep_secs: int | None,
    tsb: float | None,
) -> ReadinessComponents:
    """Score each input on 0..100, leaving absent ones absent."""
    return ReadinessComponents(
        hrv=_clamped_deviation_score(hrv_deviation_sd, higher_is_better=True),
        # A resting heart rate above baseline is the unwelcome direction.
        resting_hr=_clamped_deviation_score(
            resting_hr_deviation_sd, higher_is_better=False
        ),
        sleep=_sleep_score(sleep_secs),
        tsb=_tsb_score(tsb),
    )


def readiness_score(components: ReadinessComponents) -> int | None:
    """Weighted mean of the inputs that are present, 0..100.

    The weights of the present inputs are renormalised rather than the score
    being dragged down by something nobody measured. Below
    :data:`~tempo.metrics.thresholds.READINESS_MIN_COMPONENTS` inputs there
    is no score: one number on its own would say more about what is missing
    than about the athlete.
    """
    if components.present < READINESS_MIN_COMPONENTS:
        return None
    weighted = (
        (components.hrv, READINESS_WEIGHT_HRV),
        (components.resting_hr, READINESS_WEIGHT_RESTING_HR),
        (components.sleep, READINESS_WEIGHT_SLEEP),
        (components.tsb, READINESS_WEIGHT_TSB),
    )
    total_weight = sum(weight for value, weight in weighted if value is not None)
    if total_weight <= 0:
        return None
    total = sum(value * weight for value, weight in weighted if value is not None)
    return round(min(100.0, max(0.0, total / total_weight)))


def readiness_baselines(
    hrv_readings: Sequence[Reading],
    resting_hr_readings: Sequence[Reading],
    *,
    as_of: dt.date,
) -> tuple[Baseline | None, Baseline | None]:
    """Baselines for the readiness comparison, over the readiness window.

    These are built from :data:`MIN_NIGHTS_READINESS` nights, not the
    twenty-one the published HRV baseline needs, and the difference is
    deliberate. The two are different products of the same arithmetic:
    readiness asks "how does today compare with the recent norm", which
    fourteen nights answer; the baseline tile draws a trend with a band
    around it, which needs more data before the band means anything.

    Without this split the readiness minimum would be unreachable — every
    one of its inputs would be waiting on a longer window than its own.
    """
    return (
        build_baseline(
            hrv_readings,
            as_of=as_of,
            required_days=MIN_NIGHTS_READINESS,
            log_transform=True,
        ),
        build_baseline(
            resting_hr_readings,
            as_of=as_of,
            required_days=MIN_NIGHTS_READINESS,
            log_transform=False,
        ),
    )


def readiness(
    *,
    nights: Sequence[dt.date],
    components: ReadinessComponents,
    as_of: dt.date,
    newest_data_point: dt.date | None = None,
) -> MetricResult[int]:
    """Readiness with its evidence.

    ``nights`` is the current window of wellness days — the count that has
    to reach the minimum before any score is delivered, however many
    components happen to be computable today.
    """
    window = current_window(nights, as_of=as_of)
    return from_window(
        readiness_score(components),
        window=window,
        required=MIN_NIGHTS_READINESS,
        as_of=as_of,
        newest_data_point=newest_data_point or newest_of(nights),
    )
