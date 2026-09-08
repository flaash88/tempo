"""The Heute screen.

Every tile is a metric envelope: a value with its evidence, or no value
with its progress. Several tiles being in different states at once is the
normal case, not an exception, so nothing here collapses them into one
screen-wide state.
"""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter

from tempo.api.deps import EngineDep, SessionDep, SettingsDep
from tempo.api.envelope import baseline_value, envelope, form_value, plain
from tempo.api.routes.sync import collect_status
from tempo.api.schemas import (
    PlannedWorkoutSummary,
    SleepValue,
    SubjectiveDay,
    TodayResponse,
)
from tempo.db.models import PlannedWorkout
from tempo.reports import build_today_extras
from tempo.snapshot import build_snapshot

router = APIRouter(prefix="/api", tags=["today"])


def planned_summary(
    entry: PlannedWorkout, *, done: bool = False
) -> PlannedWorkoutSummary:
    return PlannedWorkoutSummary(
        id=entry.id,
        date=entry.date,
        category=entry.category,
        sport=entry.sport,
        name=entry.name,
        description=entry.description,
        target_time_s=entry.target_time_s,
        target_dist_m=entry.target_dist_m,
        target_load=entry.target_load,
        workout_doc=entry.workout_doc,
        external_id=entry.external_id,
        done=done,
    )


@router.get("/today", response_model=TodayResponse, summary="Heute")
def get_today(
    _session: SessionDep, settings: SettingsDep, engine: EngineDep
) -> TodayResponse:
    as_of = dt.datetime.now(tz=dt.UTC).date()
    snapshot = build_snapshot(engine, as_of=as_of, weights=settings.readiness_weights)
    extras = build_today_extras(engine, as_of=as_of)

    return TodayResponse(
        date=as_of,
        readiness=plain(snapshot.readiness),
        readiness_components={
            "hrv": snapshot.readiness_components.hrv,
            "resting_hr": snapshot.readiness_components.resting_hr,
            "sleep": snapshot.readiness_components.sleep,
            "tsb": snapshot.readiness_components.tsb,
        },
        readiness_weights=snapshot.readiness_weights.as_dict(),
        hrv=envelope(snapshot.hrv, baseline_value),
        hrv_latest=extras.hrv_latest,
        hrv_source_field=snapshot.hrv_source_field,
        resting_hr=envelope(snapshot.resting_hr, baseline_value),
        resting_hr_latest=extras.resting_hr_latest,
        sleep=envelope(extras.sleep, lambda seconds: SleepValue(seconds=seconds)),
        subjective=(
            None
            if extras.subjective is None
            else SubjectiveDay(
                date=extras.subjective.date,
                fatigue=extras.subjective.fatigue,
                soreness=extras.subjective.soreness,
                mood=extras.subjective.mood,
                source=extras.subjective.source,
            )
        ),
        form=envelope(snapshot.form, form_value),
        acwr=plain(snapshot.acwr),
        planned=(
            None
            if extras.planned is None
            else planned_summary(extras.planned, done=extras.planned_done)
        ),
        plan_context=extras.plan_context,
        sync=collect_status(engine),
        # Rendered from the configuration, never hard coded in the interface.
        ai_model=settings.anthropic_model_daily,
    )
