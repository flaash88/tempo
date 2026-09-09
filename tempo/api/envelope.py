"""Turning a metric result into its wire form.

One conversion, used by every route, so no endpoint can accidentally answer
with a bare number or drop half the evidence.
"""

from __future__ import annotations

import math
from collections.abc import Callable

from tempo.api.schemas import BaselineValue, FormValue, MetricEnvelope
from tempo.metrics.confidence import MetricResult
from tempo.metrics.wellness import Baseline
from tempo.snapshot import Form


def envelope[T, U](
    result: MetricResult[T], render: Callable[[T], U]
) -> MetricEnvelope[U]:
    """Wrap a result, mapping its value through ``render`` when there is one."""
    return MetricEnvelope[U](
        value=None if result.value is None else render(result.value),
        confidence=result.confidence,
        days_of_history=result.days_of_history,
        have=result.have,
        required=result.required,
        available_from=result.available_from,
        last_data_point=result.last_data_point,
        stale=result.stale,
    )


def plain[T](result: MetricResult[T]) -> MetricEnvelope[T]:
    """Wrap a result whose value already serialises on its own."""
    return envelope(result, lambda value: value)


def baseline_value(baseline: Baseline) -> BaselineValue:
    """A baseline in the readings' own units.

    The mean and band are undone from the transform they were built in, so
    the interface shows milliseconds rather than logarithms — while the
    baseline itself keeps carrying which space it was computed in.
    """
    if baseline.log_transformed:
        return BaselineValue(
            mean=math.exp(baseline.mean),
            lower=math.exp(baseline.lower),
            upper=math.exp(baseline.upper),
            sd=baseline.sd,
            days=baseline.days,
            source_field=baseline.source_field,
        )
    return BaselineValue(
        mean=baseline.mean,
        lower=baseline.lower,
        upper=baseline.upper,
        sd=baseline.sd,
        days=baseline.days,
        source_field=baseline.source_field,
    )


def form_value(form: Form) -> FormValue:
    return FormValue(ctl=form.ctl, atl=form.atl, tsb=form.tsb)
