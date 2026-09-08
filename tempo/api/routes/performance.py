"""Best efforts, critical speed and race predictions.

A prediction rests on one best effort. Once that effort is older than the
prediction horizon the answer still carries it — a personal best does not
stop being a fact — but flagged, with the date it came from, so nothing
presents a spring result as this autumn's expectation.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter

from tempo.api.deps import EngineDep, SessionDep
from tempo.api.envelope import envelope, plain
from tempo.api.schemas import (
    BestEffortOut,
    CriticalSpeedValue,
    PerformanceResponse,
    PredictionOut,
    RacePrediction,
)
from tempo.metrics.performance import CriticalSpeed
from tempo.metrics.thresholds import PREDICTION_STALE_AFTER_DAYS
from tempo.reports import RACE_LABELS, build_performance_report
from tempo.snapshot import pace_of

router = APIRouter(prefix="/api", tags=["performance"])


def _critical_speed_value(value: CriticalSpeed) -> CriticalSpeedValue:
    pace = pace_of(value.cs_m_s)
    return CriticalSpeedValue(
        cs_m_s=value.cs_m_s,
        cs_pace_s_per_km=pace if pace is not None else 0.0,
        d_prime_m=value.d_prime_m,
        points=value.points,
        r_squared=value.r_squared,
    )


@router.get("/performance", response_model=PerformanceResponse, summary="Leistung")
def get_performance(_session: SessionDep, engine: EngineDep) -> PerformanceResponse:
    report = build_performance_report(engine, as_of=dt.datetime.now(tz=dt.UTC).date())

    predictions: dict[str, RacePrediction] = {}
    for name, entries in report.predictions.items():
        distance = RACE_LABELS.get(name, 0.0)
        predictions[name] = RacePrediction(
            distance_m=distance,
            predictions=[
                PredictionOut(
                    method="riegel" if method == "riegel" else "vdot",
                    seconds=seconds,
                    pace_s_per_km=(
                        seconds / (distance / 1000.0) if distance > 0 else 0.0
                    ),
                )
                for method, seconds in entries
            ],
            based_on_date=report.reference.date if report.reference else None,
            stale=report.predictions_stale,
        )

    return PerformanceResponse(
        best_efforts=[
            BestEffortOut(
                duration_s=effort.duration_s,
                distance_m=effort.distance_m,
                speed_m_s=effort.speed_m_s,
                pace_s_per_km=pace_of(effort.speed_m_s) or 0.0,
                activity_id=effort.activity_id,
                date=effort.date,
            )
            for effort in report.best_efforts
        ],
        critical_speed=envelope(report.critical_speed, _critical_speed_value),
        vdot=plain(report.vdot),
        predictions=predictions,
        reference_distance_m=(
            report.reference.distance_m if report.reference else None
        ),
        reference_seconds=(
            float(report.reference.duration_s) if report.reference else None
        ),
        reference_date=report.reference.date if report.reference else None,
        predictions_stale=report.predictions_stale,
        prediction_stale_after_days=PREDICTION_STALE_AFTER_DAYS,
    )
