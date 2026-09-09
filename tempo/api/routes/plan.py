"""The Plan screen: the calendar as the source has it."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException, status

from tempo.api.deps import EngineDep, SessionDep
from tempo.api.routes.today import planned_summary
from tempo.api.schemas import PlanResponse
from tempo.reports import build_plan_report

router = APIRouter(prefix="/api", tags=["plan"])

# A plan is read a week at a time; without a range the answer is the week
# the athlete is standing in, Monday to Sunday.
DEFAULT_SPAN_DAYS = 6


@router.get("/plan", response_model=PlanResponse, summary="Plan")
def get_plan(
    _session: SessionDep,
    engine: EngineDep,
    from_date: dt.date | None = None,
    to_date: dt.date | None = None,
) -> PlanResponse:
    today = dt.datetime.now(tz=dt.UTC).date()
    start = from_date or today - dt.timedelta(days=today.weekday())
    end = to_date or start + dt.timedelta(days=DEFAULT_SPAN_DAYS)

    try:
        report = build_plan_report(engine, from_date=start, to_date=end)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc

    return PlanResponse(
        from_date=report.from_date,
        to_date=report.to_date,
        workouts=[
            planned_summary(entry, done=entry.id in report.done_ids)
            for entry in report.workouts
        ],
        planned_distance_m=report.planned_distance_m,
        planned_duration_s=report.planned_duration_s,
        planned_load=report.planned_load,
        races=[planned_summary(entry) for entry in report.races],
    )
