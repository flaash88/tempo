"""Fitness, fatigue, form — and the ratios built on them.

```
CTL_t = CTL_{t-1} + (Load_t - CTL_{t-1}) / 42
ATL_t = ATL_{t-1} + (Load_t - ATL_{t-1}) / 7
TSB_t = CTL_{t-1} - ATL_{t-1}
ACWR  = sum 7d / (sum 28d / 4)
```

Days without a session carry load zero, and that is the point: the series
runs over every calendar day so the curves decay through a rest week
instead of freezing at the last value. Whether a computed value is
*delivered* is a separate question, answered by ``confidence`` — a curve
computed across three months of untracked days is arithmetic, not
information.

Form is deliberately yesterday's fitness minus yesterday's fatigue, as the
plan states it: today's session has not been absorbed yet when the athlete
gets up and decides what to do.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import pstdev

from tempo.metrics.thresholds import (
    ACWR_ACUTE_DAYS,
    ACWR_CHRONIC_DAYS,
    ATL_TIME_CONSTANT_DAYS,
    CTL_TIME_CONSTANT_DAYS,
    MONOTONY_WINDOW_DAYS,
)


@dataclass(frozen=True, slots=True)
class FitnessPoint:
    """One day of the fitness series."""

    date: dt.date
    load: float
    ctl: float
    atl: float
    tsb: float


def _step(previous: float, load: float, time_constant_days: int) -> float:
    return previous + (load - previous) / time_constant_days


def fitness_series(
    days: Sequence[tuple[dt.date, float | None]],
    *,
    ctl_seed: float = 0.0,
    atl_seed: float = 0.0,
) -> tuple[FitnessPoint, ...]:
    """Walk the exponential averages across a run of consecutive days.

    ``None`` for a day's load is read as zero here: by the time the series
    is built, an unknown load has already been reported as unknown, and the
    curve still has to advance through that day rather than stop.

    The seeds are both zero by default, which is the honest start for an
    athlete with no history — the curve builds up from nothing, and the
    minimum history rule keeps it from being shown while it does.
    """
    ctl = ctl_seed
    atl = atl_seed
    points: list[FitnessPoint] = []
    for day, raw_load in days:
        load = raw_load or 0.0
        # Form is yesterday's balance: today's session is not absorbed yet.
        tsb = ctl - atl
        ctl = _step(ctl, load, CTL_TIME_CONSTANT_DAYS)
        atl = _step(atl, load, ATL_TIME_CONSTANT_DAYS)
        points.append(FitnessPoint(date=day, load=load, ctl=ctl, atl=atl, tsb=tsb))
    return tuple(points)


def acwr(loads: Sequence[float | None]) -> float | None:
    """Acute to chronic workload ratio over the trailing days.

    Needs a full chronic window; below that the denominator would describe a
    shorter period than it claims. ``None`` when the chronic load is zero —
    a ratio against nothing is not a large number, it is undefined.
    """
    if len(loads) < ACWR_CHRONIC_DAYS:
        return None
    chronic_days = [load or 0.0 for load in loads[-ACWR_CHRONIC_DAYS:]]
    acute_days = chronic_days[-ACWR_ACUTE_DAYS:]
    chronic = sum(chronic_days) / (ACWR_CHRONIC_DAYS / ACWR_ACUTE_DAYS)
    if chronic <= 0:
        return None
    return sum(acute_days) / chronic


def monotony(week_loads: Sequence[float | None]) -> float | None:
    """Mean daily load over its spread, across one week.

    Uses the population standard deviation: the week is the whole
    population, not a sample of a longer one. A week of identical days has
    no spread and therefore no monotony — the ratio would be infinite, and
    infinity is not a training insight.
    """
    if len(week_loads) != MONOTONY_WINDOW_DAYS:
        raise ValueError(f"expected {MONOTONY_WINDOW_DAYS} daily loads")
    loads = [load or 0.0 for load in week_loads]
    spread = pstdev(loads)
    if spread <= 0:
        return None
    return sum(loads) / len(loads) / spread


def strain(week_loads: Sequence[float | None]) -> float | None:
    """Weekly load multiplied by its monotony."""
    ratio = monotony(week_loads)
    if ratio is None:
        return None
    return sum(load or 0.0 for load in week_loads) * ratio
