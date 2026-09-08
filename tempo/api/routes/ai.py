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

from fastapi import APIRouter, HTTPException, Request, status

from tempo.ai.budget import BudgetState, budget_state
from tempo.ai.errors import (
    AiApiError,
    AiAuthError,
    AiRateLimited,
    BudgetExceeded,
    FeaturesTooLarge,
)
from tempo.ai.service import (
    AiAnswer,
    AiClientFactory,
    answer,
    default_ai_client_factory,
)
from tempo.api.deps import EngineDep, SessionDep, SettingsDep
from tempo.api.schemas import AiAnswerResponse, AiBudgetState, AiChatRequest
from tempo.config import Settings

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


def _run(
    request: Request,
    settings: Settings,
    engine: EngineDep,
    *,
    task: str,
    activity_id: str | None = None,
    question: str | None = None,
    refresh: bool = False,
) -> AiAnswerResponse:
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
            activity_id=activity_id,
            question=question,
            refresh=refresh,
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

    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Aktivität nicht gefunden",
        )
    return _response(result)


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


@router.post(
    "/plan-week",
    response_model=AiAnswerResponse,
    summary="Wochenplan",
)
def ai_plan_week(
    request: Request,
    _session: SessionDep,
    settings: SettingsDep,
    engine: EngineDep,
    refresh: bool = False,
) -> AiAnswerResponse:
    """The coming week. Uses the planning model from the configuration."""
    return _run(request, settings, engine, task="plan_week", refresh=refresh)


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

    There is no conversation history. Each question is answered from the
    numbers alone, which is what keeps the context window bounded and what
    keeps an earlier answer from becoming an input to the next one.
    """
    return _run(
        request,
        settings,
        engine,
        task="chat",
        question=payload.question,
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
