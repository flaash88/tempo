"""The four AI endpoints.

Each one does the same thing with a different task: aggregate the metrics,
hand the model a compact document, store what comes back. None of them
computes anything, and none of them sends anything the metric engine has
not already finished.

A spent budget is answered with 402 and a body that says how much of what
was used. It is a defined state of the application rather than a fault:
nothing failed, the month is simply over. The status code is not 429 —
nothing here is rate limited and waiting a minute changes nothing — and
not 200, because an interface that shows a refusal as an answer is an
interface that will eventually show it as advice.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Sequence

from fastapi import APIRouter, HTTPException, Request, status

from tempo.ai.budget import BudgetState, budget_state
from tempo.ai.errors import (
    AiApiError,
    AiAuthError,
    AiRateLimited,
    AnswerFormatError,
    BudgetExceeded,
    FeaturesTooLarge,
)
from tempo.ai.plan_week import (
    PlanFormatProblem,
    WeekPlan,
    check,
    format_contract,
    parse,
    plan_window,
    resolve,
)
from tempo.ai.service import (
    AiAnswer,
    AiClientFactory,
    AnswerValidator,
    answer,
    default_ai_client_factory,
)
from tempo.api.deps import EngineDep, SessionDep, SettingsDep
from tempo.api.schemas import (
    AiAnswerResponse,
    AiBudgetState,
    AiChatRequest,
    AiWeekPlanResponse,
    PlanDayOut,
    WeekPlanOut,
)
from tempo.config import Settings
from tempo.db.models import AthleteSettings
from tempo.db.session import session_scope

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ai", tags=["ai"])

# 402 for a spent budget: the request is well formed and the server is
# healthy, and the only thing standing in the way is that the month's money
# is gone.
BUDGET_EXHAUSTED_STATUS = status.HTTP_402_PAYMENT_REQUIRED


def _budget(state: BudgetState) -> AiBudgetState:
    return AiBudgetState(
        month=state.month,
        spent_eur=round(state.spent_eur, 4),
        budget_eur=state.budget_eur,
        remaining_eur=round(state.remaining_eur, 4),
        exhausted=state.exhausted,
    )


def _response(result: AiAnswer) -> AiAnswerResponse:
    return AiAnswerResponse(
        task=result.task,
        text=result.text,
        model=result.model,
        created_at=result.created_at,
        cached=result.cached,
        budget=_budget(result.budget),
        features_bytes=result.features_bytes,
        features_trimmed=list(result.features_trimmed),
        cost_eur=round(result.cost_eur, 6),
    )


def _client_factory(request: Request) -> AiClientFactory:
    factory = getattr(request.app.state, "ai_client_factory", None)
    if factory is None:  # pragma: no cover - create_app always sets it
        return default_ai_client_factory
    return factory  # type: ignore[no-any-return]


def _execute(
    request: Request,
    settings: Settings,
    engine: EngineDep,
    *,
    task: str,
    as_of: dt.date | None = None,
    activity_id: str | None = None,
    question: str | None = None,
    history: Sequence[tuple[str, str]] = (),
    refresh: bool = False,
    task_extra: str | None = None,
    validator: AnswerValidator | None = None,
) -> AiAnswer:
    """Every endpoint's body, including the failure modes.

    The exceptions are translated here rather than in the service, so that
    the service stays usable from the CLI and the scheduler without a web
    framework's vocabulary leaking into it.
    """
    try:
        result = answer(
            engine,
            settings,
            task=task,
            client_factory=_client_factory(request),
            as_of=as_of,
            activity_id=activity_id,
            question=question,
            history=history,
            refresh=refresh,
            task_extra=task_extra,
            validator=validator,
        )
    except BudgetExceeded as exc:
        state = budget_state(engine, budget_eur=settings.monthly_budget_eur)
        raise HTTPException(
            status_code=BUDGET_EXHAUSTED_STATUS,
            detail={
                "detail": (
                    f"Monatsbudget von {exc.budget_eur:.2f} EUR erreicht — "
                    "keine weitere Auswertung in diesem Monat"
                ),
                "budget": _budget(state).model_dump(),
            },
        ) from exc
    except AiAuthError as exc:
        # The message never carries the key; see tempo.ai.client.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="ANTHROPIC_API_KEY fehlt oder wurde abgelehnt",
        ) from exc
    except AiRateLimited as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Anthropic ist gerade nicht erreichbar, bitte später erneut",
        ) from exc
    except AiApiError as exc:
        log.warning("AI call failed", extra={"task": task})
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Die Auswertung konnte nicht erstellt werden",
        ) from exc
    except FeaturesTooLarge as exc:
        log.error("feature document did not fit", extra={"task": task})
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Die Kennzahlen passen nicht in eine Anfrage",
        ) from exc
    except AnswerFormatError as exc:
        # Twice asked, twice the wrong shape. Better a stated failure than
        # a half-drawn screen built from whatever came back.
        log.error("AI answer did not match its contract", extra={"task": task})
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Die Antwort kam nicht im erwarteten Format zurück: {exc}",
        ) from exc

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aktivität nicht gefunden",
        )
    return result


def _run(
    request: Request,
    settings: Settings,
    engine: EngineDep,
    *,
    task: str,
    activity_id: str | None = None,
    question: str | None = None,
    history: Sequence[tuple[str, str]] = (),
    refresh: bool = False,
) -> AiAnswerResponse:
    """The unstructured endpoints: one answer, one block of text."""
    return _response(
        _execute(
            request,
            settings,
            engine,
            task=task,
            activity_id=activity_id,
            question=question,
            history=history,
            refresh=refresh,
        )
    )


@router.post(
    "/daily",
    response_model=AiAnswerResponse,
    summary="Tageseinschätzung",
)
def ai_daily(
    request: Request,
    _session: SessionDep,
    settings: SettingsDep,
    engine: EngineDep,
    refresh: bool = False,
) -> AiAnswerResponse:
    """Today's assessment, from today's numbers.

    Repeating the request costs nothing until a number changes: the stored
    answer is keyed on the feature document, so a second look at the same
    day is a read.
    """
    return _run(request, settings, engine, task="daily", refresh=refresh)


@router.post(
    "/activity/{activity_id}",
    response_model=AiAnswerResponse,
    summary="Einheit einordnen",
)
def ai_activity(
    request: Request,
    activity_id: str,
    _session: SessionDep,
    settings: SettingsDep,
    engine: EngineDep,
    refresh: bool = False,
) -> AiAnswerResponse:
    return _run(
        request,
        settings,
        engine,
        task="activity",
        activity_id=activity_id,
        refresh=refresh,
    )


def _plan_out(plan: WeekPlan) -> WeekPlanOut:
    return WeekPlanOut(
        from_date=plan.from_date,
        to_date=plan.to_date,
        rationale=plan.rationale,
        days=[
            PlanDayOut(
                date=day.date,
                weekday=day.weekday,
                weekday_long=day.weekday_long,
                kind="session" if day.kind == "session" else "rest",
                title=day.title,
                duration_s=day.duration_s,
                zone=day.zone,
                zone_label=day.zone_label,
                target_hr_low=day.target_hr_low,
                target_hr_high=day.target_hr_high,
                purpose=day.purpose,
            )
            for day in plan.days
        ],
        limitations=list(plan.limitations),
        hr_source=plan.hr_source,
        hr_note=plan.hr_note,
    )


def _athlete(engine: EngineDep) -> AthleteSettings | None:
    with session_scope(engine) as session:
        athlete = session.get(AthleteSettings, 1)
        if athlete is not None:
            session.expunge(athlete)
    return athlete


@router.post(
    "/plan-week",
    response_model=AiWeekPlanResponse,
    summary="Wochenplan",
)
def ai_plan_week(
    request: Request,
    _session: SessionDep,
    settings: SettingsDep,
    engine: EngineDep,
    refresh: bool = False,
) -> AiWeekPlanResponse:
    """The coming week, as seven days rather than a page of prose.

    The dates are decided here and handed to the model as part of the
    contract; the shape of what comes back is checked before anything is
    stored, and one repair round is spent on an answer that misses it. The
    heart rates are added afterwards from the athlete's own zone bounds —
    the model names a zone and never a number.
    """
    as_of = dt.datetime.now(tz=dt.UTC).date()
    window = plan_window(as_of)
    result = _execute(
        request,
        settings,
        engine,
        task="plan_week",
        as_of=as_of,
        refresh=refresh,
        task_extra=format_contract(window),
        validator=lambda text: check(text, window=window),
    )
    try:
        parsed = parse(result.text, window=window)
    except PlanFormatProblem as problem:  # pragma: no cover - the validator ran first
        # Unreachable while the validator above is the same check. Kept
        # because "unreachable" is a claim about today's code, and the
        # alternative is a stack trace on a German screen.
        log.error("plan parsed differently than it validated")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Die Antwort kam nicht im erwarteten Format zurück: {problem}",
        ) from problem
    plan = resolve(parsed, window=window, athlete=_athlete(engine))
    return AiWeekPlanResponse(**_response(result).model_dump(), plan=_plan_out(plan))


@router.post(
    "/chat",
    response_model=AiAnswerResponse,
    summary="Rückfrage",
)
def ai_chat(
    request: Request,
    payload: AiChatRequest,
    _session: SessionDep,
    settings: SettingsDep,
    engine: EngineDep,
    refresh: bool = False,
) -> AiAnswerResponse:
    """One question against the same document the other endpoints see.

    The client sends the earlier turns; the server decides how many of them
    survive. Each is truncated to the configured length, at most the
    configured number of turns is kept, and the whole history is then cut
    to a fixed byte ceiling — so no client can use the conversation to get
    more into the context window than the feature document's own limit
    allows.

    Numbers still come only from the document. An earlier answer is
    context, never an input, which is what keeps a figure the model once
    invented from circulating as though it were measured.
    """
    return _run(
        request,
        settings,
        engine,
        task="chat",
        question=payload.question,
        history=[(turn.role, turn.text) for turn in payload.history],
        refresh=refresh,
    )


@router.get("/budget", response_model=AiBudgetState, summary="Budgetstand")
def ai_budget(
    _session: SessionDep, settings: SettingsDep, engine: EngineDep
) -> AiBudgetState:
    """What is left this month, without making a call to find out."""
    return _budget(
        budget_state(
            engine,
            budget_eur=settings.monthly_budget_eur,
            as_of=dt.datetime.now(tz=dt.UTC).date(),
        )
    )
