"""The activity list, one session's detail, and its streams."""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, status

from tempo.api.deps import EngineDep, SessionDep
from tempo.api.envelope import plain
from tempo.api.routes.today import planned_summary
from tempo.api.schemas import (
    ActivityDetailResponse,
    ActivityListItem,
    ActivityListResponse,
    LapItem,
    StreamsResponse,
    ZoneBounds,
    ZoneShare,
)
from tempo.reports import (
    build_activity_report,
    build_stream_report,
    list_activities,
)

router = APIRouter(prefix="/api/activities", tags=["activities"])


@router.get("", response_model=ActivityListResponse, summary="Aktivitäten")
def get_activities(
    _session: SessionDep,
    engine: EngineDep,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
    sport: str | None = None,
) -> ActivityListResponse:
    rows, total = list_activities(engine, limit=limit, offset=offset, sport=sport)
    return ActivityListResponse(
        activities=[
            ActivityListItem(
                id=row.activity.id,
                start_local=row.activity.start_local,
                sport=row.activity.sport,
                distance_m=row.activity.distance_m,
                moving_s=row.activity.moving_s,
                avg_hr=row.activity.avg_hr,
                avg_pace_s_per_km=row.activity.avg_pace_s_per_km,
                has_stream=row.has_stream,
            )
            for row in rows
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get(
    "/{activity_id}",
    response_model=ActivityDetailResponse,
    summary="Aktivitätsdetail",
)
def get_activity(
    _session: SessionDep, engine: EngineDep, activity_id: str
) -> ActivityDetailResponse:
    report = build_activity_report(
        engine, activity_id, as_of=dt.datetime.now(tz=dt.UTC).date()
    )
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Aktivität nicht gefunden"
        )

    activity = report.activity
    return ActivityDetailResponse(
        id=activity.id,
        start_local=activity.start_local,
        sport=activity.sport,
        distance_m=activity.distance_m,
        moving_s=activity.moving_s,
        elapsed_s=activity.elapsed_s,
        elevation_gain_m=activity.elevation_gain_m,
        avg_hr=activity.avg_hr,
        max_hr=activity.max_hr,
        avg_pace_s_per_km=activity.avg_pace_s_per_km,
        source=activity.source,
        has_stream=report.has_stream,
        trimp=plain(report.trimp),
        hr_tss=plain(report.hr_tss),
        r_tss=plain(report.r_tss),
        gap_pace_s_per_km=plain(report.gap_pace),
        efficiency_factor=plain(report.efficiency_factor),
        decoupling=plain(report.decoupling),
        avg_cadence_spm=report.avg_cadence_spm,
        zones=[
            ZoneShare(zone=index + 1, seconds=seconds)
            for index, seconds in enumerate(report.zone_seconds)
        ],
        zone_bounds=(
            None
            if report.zone_bounds is None
            else ZoneBounds(
                kind=report.zone_bounds.kind,
                model=report.zone_bounds.model,
                lower_bounds=list(report.zone_bounds.lower_bounds),
            )
        ),
        seconds_below_zone_1=report.seconds_below_zone_1,
        seconds_unknown=report.seconds_unknown,
        laps=[
            LapItem(
                index=lap.index,
                distance_m=lap.distance_m,
                duration_s=lap.duration_s,
                avg_hr=lap.avg_hr,
                avg_pace_s_per_km=lap.avg_pace_s_per_km,
                pace_delta_s_per_km=lap.pace_delta_s_per_km,
            )
            for lap in report.laps
        ],
        planned=(None if report.planned is None else planned_summary(report.planned)),
    )


@router.get(
    "/{activity_id}/streams",
    response_model=StreamsResponse,
    summary="Streams einer Aktivität",
)
def get_streams(
    _session: SessionDep,
    engine: EngineDep,
    activity_id: str,
    fields: str | None = None,
    resolution: Annotated[int, Query(ge=1, le=3600)] = 1,
) -> StreamsResponse:
    """Stream channels, decimated rather than smoothed.

    A coarser resolution keeps every nth recorded second and leaves the rest
    out. Averaging a bucket would invent a reading for a second that was
    never measured and would quietly close a gap the parser preserved on
    purpose.
    """
    wanted = (
        [name.strip() for name in fields.split(",") if name.strip()] if fields else None
    )
    try:
        report = build_stream_report(
            engine, activity_id, fields=wanted, resolution_s=resolution
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Aktivität nicht gefunden"
        )

    return StreamsResponse(
        activity_id=report.activity_id,
        resolution_s=report.resolution_s,
        samples=len(report.offset_s),
        fields=list(report.series),
        offset_s=list(report.offset_s),
        series=report.series,
    )
