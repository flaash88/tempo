"""Proposing a session, confirming it, and sending it to the watch.

The order is the point. A generated session is a suggestion until the
athlete confirms it, and only a confirmed session is written to the
intervals.icu calendar, from where Garmin picks it up. Nothing here
overwrites a confirmed entry on its own, and nothing here touches an entry
the athlete created in the source's own interface.
"""

from __future__ import annotations

import datetime as dt
import logging

from fastapi import APIRouter, HTTPException, Request, status

from tempo.api.deps import EngineDep, SessionDep, SettingsDep
from tempo.api.routes.today import planned_summary
from tempo.api.schemas import (
    PlanAdoptDay,
    PlanAdoptRequest,
    PlanAdoptResponse,
    PlanAdoptResult,
    PlannedWorkoutSummary,
    WorkoutProposalRequest,
    WorkoutPushResponse,
)
from tempo.db.models import PlannedWorkout
from tempo.db.session import session_scope
from tempo.ingest.writeback import (
    AlreadyConfirmed,
    NotConfirmed,
    NotOurWorkout,
    TransferInFlight,
    WorkoutProposal,
    confirm_workout,
    propose_workout,
    push_workout,
    update_workout,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/plan/workouts", tags=["plan"])

# How far back a session may be planned. Yesterday is not a plan, and a
# date typed a year wrong should not quietly become one.
MAX_BACKDATING_DAYS = 1


def _proposal(payload: WorkoutProposalRequest) -> WorkoutProposal:
    return WorkoutProposal(
        date=payload.date,
        sport=payload.sport,
        name=payload.name,
        description=payload.description,
        target_time_s=payload.target_time_s,
        target_dist_m=payload.target_dist_m,
        target_load=payload.target_load,
        workout_doc=payload.workout_doc,
    )


def _check_date(day: dt.date) -> None:
    today = dt.datetime.now(tz=dt.UTC).date()
    if day < today - dt.timedelta(days=MAX_BACKDATING_DAYS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Eine Einheit lässt sich nicht in die Vergangenheit planen",
        )


def _summary(entry: PlannedWorkout) -> PlannedWorkoutSummary:
    return planned_summary(entry)


def _conflict(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)


@router.post(
    "",
    response_model=PlannedWorkoutSummary,
    status_code=status.HTTP_201_CREATED,
    summary="Einheit vorschlagen",
)
def create_workout(
    payload: WorkoutProposalRequest,
    _session: SessionDep,
    engine: EngineDep,
    replace: bool = False,
) -> PlannedWorkoutSummary:
    """Store a suggested session. Unconfirmed, and nowhere near the watch.

    A day that already carries a confirmed session is refused unless
    ``replace`` says otherwise, and a session the source owns is refused
    either way — replacing what the athlete wrote elsewhere is not
    something an endpoint decides.
    """
    _check_date(payload.date)
    try:
        entry = propose_workout(engine, _proposal(payload), replace=replace)
    except AlreadyConfirmed as exc:
        raise _conflict(
            "Für diesen Tag ist bereits eine bestätigte Einheit hinterlegt. "
            "Mit replace=true wird sie ersetzt und muss neu bestätigt werden.",
        ) from exc
    return _summary(entry)


def _from_plan_day(day: PlanAdoptDay) -> WorkoutProposal:
    """A generated day, as a session the calendar can hold.

    The description is assembled here rather than sent by the client, so
    what lands in the athlete's calendar is built from the fields that
    were validated and not from a string somebody put together on the way.
    """
    parts = []
    if day.zone_label:
        parts.append(f"Zielzone {day.zone_label}.")
    if day.purpose.strip():
        parts.append(day.purpose.strip())
    return WorkoutProposal(
        date=day.date,
        sport="Run",
        name=day.title.strip(),
        description=" ".join(parts) or None,
        target_time_s=day.duration_s,
    )


@router.post(
    "/adopt",
    response_model=PlanAdoptResponse,
    summary="Generierte Tage übernehmen",
)
def adopt_plan(
    payload: PlanAdoptRequest,
    _session: SessionDep,
    engine: EngineDep,
) -> PlanAdoptResponse:
    """Take generated days into the calendar, confirmed.

    The athlete's click on a generated day *is* the confirmation — there
    is nothing else it could mean — so each day is proposed and confirmed
    in one step. That is still short of the watch: sending stays the
    separate, explicit act it was in phase 6.

    A day that cannot be taken over is reported as a result rather than
    raised as an error, because the week button would otherwise fail whole
    over a single Wednesday that already carries something. Nothing is
    overwritten unless ``replace`` says so, and a session the source owns
    is not overwritten even then.
    """
    results: list[PlanAdoptResult] = []
    for day in payload.days:
        results.append(_adopt_day(engine, day, replace=payload.replace))
    created = sum(1 for result in results if result.created)
    return PlanAdoptResponse(
        results=results, created=created, skipped=len(results) - created
    )


def _adopt_day(
    engine: EngineDep, day: PlanAdoptDay, *, replace: bool
) -> PlanAdoptResult:
    today = dt.datetime.now(tz=dt.UTC).date()
    if day.date < today - dt.timedelta(days=MAX_BACKDATING_DAYS):
        return PlanAdoptResult(
            date=day.date,
            created=False,
            detail="Liegt in der Vergangenheit und wird nicht angelegt",
        )
    try:
        entry = propose_workout(engine, _from_plan_day(day), replace=replace)
    except AlreadyConfirmed:
        return PlanAdoptResult(
            date=day.date,
            created=False,
            detail=("Für diesen Tag ist bereits eine bestätigte Einheit hinterlegt"),
        )
    except NotOurWorkout:  # pragma: no cover - propose_workout refuses earlier
        return PlanAdoptResult(
            date=day.date,
            created=False,
            detail="Für diesen Tag steht ein Eintrag aus dem Kalender der Quelle",
        )
    confirmed = confirm_workout(engine, entry.id)
    return PlanAdoptResult(
        date=day.date, created=True, workout=_summary(confirmed or entry)
    )


@router.put(
    "/{workout_id}",
    response_model=PlannedWorkoutSummary,
    summary="Einheit ändern",
)
def edit_workout(
    workout_id: str,
    payload: WorkoutProposalRequest,
    _session: SessionDep,
    engine: EngineDep,
) -> PlannedWorkoutSummary:
    """Change a proposal. The confirmation does not survive the change.

    What was agreed to was a particular session. A changed one is a new
    suggestion, and one that is already on the watch becomes *outdated*
    rather than *unsent* — the old version is still out there.
    """
    _check_date(payload.date)
    try:
        entry = update_workout(engine, workout_id, _proposal(payload))
    except NotOurWorkout as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Diese Einheit stammt aus dem Kalender der Quelle",
        ) from exc
    except TransferInFlight as exc:
        raise _conflict("Die Übertragung läuft gerade") from exc
    except AlreadyConfirmed as exc:
        raise _conflict(
            "Für diesen Tag ist bereits eine bestätigte Einheit hinterlegt"
        ) from exc
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Einheit nicht gefunden"
        )
    return _summary(entry)


@router.post(
    "/{workout_id}/confirm",
    response_model=PlannedWorkoutSummary,
    summary="Einheit bestätigen",
)
def confirm(
    workout_id: str, _session: SessionDep, engine: EngineDep
) -> PlannedWorkoutSummary:
    """The athlete's yes, and the only thing that makes a transfer possible."""
    try:
        entry = confirm_workout(engine, workout_id)
    except NotOurWorkout as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Diese Einheit stammt aus dem Kalender der Quelle",
        ) from exc
    if entry is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Einheit nicht gefunden"
        )
    return _summary(entry)


@router.post(
    "/{workout_id}/push",
    response_model=WorkoutPushResponse,
    summary="An Uhr senden",
)
def push(
    request: Request,
    workout_id: str,
    _session: SessionDep,
    settings: SettingsDep,
    engine: EngineDep,
) -> WorkoutPushResponse:
    """Write the confirmed session to the calendar.

    Synchronous: it is one request, and the athlete is standing in front of
    the screen waiting to know whether it worked. An unchanged session
    already on the watch is not sent a second time.
    """
    if not settings.has_intervals_credentials:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="INTERVALS_API_KEY ist nicht gesetzt",
        )
    try:
        report = push_workout(
            engine,
            settings,
            workout_id,
            client_factory=request.app.state.client_factory,
        )
    except NotOurWorkout as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Diese Einheit stammt aus dem Kalender der Quelle",
        ) from exc
    except NotConfirmed as exc:
        raise _conflict(
            "Die Einheit ist noch nicht bestätigt und wird nicht übertragen"
        ) from exc
    except TransferInFlight as exc:
        raise _conflict("Die Übertragung läuft bereits") from exc
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Einheit nicht gefunden"
        )

    with session_scope(engine) as session:
        entry = session.get(PlannedWorkout, workout_id)
        if entry is None:  # pragma: no cover - deleted mid-request
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Einheit nicht gefunden"
            )
        session.expunge(entry)

    return WorkoutPushResponse(
        workout=_summary(entry), sent=report.sent, detail=report.detail
    )
