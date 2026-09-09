"""The Trends screen: the series behind the curves."""

from __future__ import annotations

import datetime as dt
from typing import Annotated, Literal

from fastapi import APIRouter, HTTPException, Query, status

from tempo.api.deps import EngineDep, SessionDep
from tempo.api.envelope import baseline_value, envelope, plain
from tempo.api.schemas import (
    BaselinePoint,
    FitnessPointOut,
    TrendsResponse,
    WeekVolume,
    ZoneShare,
)
from tempo.reports import build_trends_report

router = APIRouter(prefix="/api", tags=["trends"])

Window = Literal["6w", "12w", "52w"]


@router.get("/trends", response_model=TrendsResponse, summary="Trends")
def get_trends(
    _session: SessionDep,
    engine: EngineDep,
    window: Annotated[Window, Query()] = "12w",
) -> TrendsResponse:
    try:
        report = build_trends_report(
            engine, window=window, as_of=dt.datetime.now(tz=dt.UTC).date()
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return TrendsResponse(
        window=window,
        from_date=report.from_date,
        to_date=report.to_date,
        fitness=[
            FitnessPointOut(
                date=point.date,
                load=point.load,
                ctl=point.ctl,
                atl=point.atl,
                tsb=point.tsb,
                confidence=point.confidence,
                days_of_history=point.days_of_history,
            )
            for point in report.fitness
        ],
        weeks=[
            WeekVolume(
                week_start=week.week_start,
                distance_m=week.distance_m,
                duration_s=week.duration_s,
                load=week.load,
                zones=[
                    ZoneShare(zone=index + 1, seconds=seconds)
                    for index, seconds in enumerate(week.zone_seconds)
                ],
            )
            for week in report.weeks
        ],
        hrv=envelope(report.hrv, baseline_value),
        hrv_series=[
            BaselinePoint(
                date=point.date,
                value=point.value,
                mean=point.mean,
                lower=point.lower,
                upper=point.upper,
            )
            for point in report.hrv_series
        ],
        hrv_source_field=report.hrv_source_field,
        resting_hr=envelope(report.resting_hr, baseline_value),
        resting_hr_series=[
            BaselinePoint(
                date=point.date,
                value=point.value,
                mean=point.mean,
                lower=point.lower,
                upper=point.upper,
            )
            for point in report.resting_hr_series
        ],
        vo2max=plain(report.vo2max),
        vdot=plain(report.vdot),
        monotony=plain(report.monotony),
        strain=plain(report.strain),
        average_week_distance_m=report.average_week_distance_m,
    )
