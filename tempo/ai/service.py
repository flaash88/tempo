"""Putting one AI answer together.

The order is fixed and each step is a gate for the next: check the budget,
build the feature document, look for a stored answer, and only then send
anything. A call is made when, and only when, all four of those say so —
which is what makes "no budget" and "already answered" cost nothing rather
than costing a request that is thrown away.

Nothing in here decides what a number means. The metrics arrive finished
from :mod:`tempo.ai.features`, the rules arrive from
:mod:`tempo.ai.prompts`, and this module carries them to the model and the
answer back.
"""

from __future__ import annotations

import datetime as dt
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final

from sqlalchemy import Engine

from tempo.ai import cache
from tempo.ai.budget import BudgetState, record_call, require_budget
from tempo.ai.client import AnthropicClient
from tempo.ai.errors import AiAuthError
from tempo.ai.features import FeatureDocument, activity_block, build_features
from tempo.ai.prompts import system_blocks, user_message
from tempo.config import Settings

log = logging.getLogger(__name__)

AiClientFactory = Callable[[Settings], AnthropicClient]

# Which model each task uses. Both come from the configuration; the mapping
# only says which of the two configured models a task belongs to.
PLANNING_TASKS: Final = frozenset({"plan_week"})

# A chat question longer than this is not a question about the numbers.
MAX_QUESTION_CHARS: Final = 2_000


def default_ai_client_factory(settings: Settings) -> AnthropicClient:
    return AnthropicClient(settings.anthropic_api_key.get_secret_value())


def model_for(task: str, settings: Settings) -> str:
    """The configured model for a task. Never a literal."""
    if task in PLANNING_TASKS:
        return settings.anthropic_model_planning
    return settings.anthropic_model_daily


@dataclass(frozen=True, slots=True)
class AiAnswer:
    """One answer, and everything that is known about how it came to be."""

    task: str
    text: str
    model: str
    created_at: dt.datetime
    cached: bool
    budget: BudgetState
    features_bytes: int
    features_trimmed: tuple[str, ...] = ()
    cost_eur: float = 0.0
    cache_read_tokens: int = 0


def _document(
    engine: Engine,
    *,
    as_of: dt.date,
    activity_id: str | None,
) -> FeatureDocument | None:
    """The feature document for a task, or ``None`` if its subject is gone."""
    extra = None
    if activity_id is not None:
        extra = activity_block(engine, activity_id, as_of=as_of)
        if extra is None:
            return None
    return build_features(engine, as_of=as_of, extra=extra)


def answer(
    engine: Engine,
    settings: Settings,
    *,
    task: str,
    client_factory: AiClientFactory,
    as_of: dt.date | None = None,
    activity_id: str | None = None,
    question: str | None = None,
    refresh: bool = False,
) -> AiAnswer | None:
    """Produce an answer, from the cache if one is stored and from the model if not.

    Returns ``None`` when the request is about an activity that does not
    exist. Raises :class:`~tempo.ai.errors.BudgetExceeded` when the month is
    spent — before the document is built, because a refused call should not
    cost a database sweep either.
    """
    as_of = as_of or dt.datetime.now(tz=dt.UTC).date()
    model = model_for(task, settings)
    budget = require_budget(engine, budget_eur=settings.monthly_budget_eur, as_of=as_of)

    document = _document(engine, as_of=as_of, activity_id=activity_id)
    if document is None:
        return None

    system = system_blocks(task, athlete_profile=document.payload.get("athlete"))
    key = cache.cache_key(
        endpoint=task,
        model=model,
        system=system,
        features_json=document.json_text,
        question=question,
    )

    if not refresh:
        stored = cache.lookup(engine, key)
        if stored is not None:
            log.info("AI answer served from cache", extra={"task": task})
            return AiAnswer(
                task=task,
                text=stored.answer,
                model=stored.model,
                created_at=stored.created_at,
                cached=True,
                budget=budget,
                features_bytes=document.size_bytes,
                features_trimmed=document.trimmed,
            )

    if not settings.has_anthropic_credentials:
        raise AiAuthError("ANTHROPIC_API_KEY ist nicht gesetzt")

    with client_factory(settings) as client:
        completion = client.complete(
            model=model,
            system=system,
            messages=[user_message(document.json_text, question)],
        )

    cost = record_call(
        engine,
        endpoint=task,
        model=completion.model,
        usage=completion.usage,
    )
    cache.store(
        engine,
        key=key,
        endpoint=task,
        model=completion.model,
        answer=completion.text,
        features_json=document.json_text,
        as_of=as_of,
    )
    log.info(
        "AI answer produced",
        extra={
            "task": task,
            "model": completion.model,
            "features_bytes": document.size_bytes,
            "cost_eur": round(cost, 6),
        },
    )

    return AiAnswer(
        task=task,
        text=completion.text,
        model=completion.model,
        created_at=dt.datetime.now(tz=dt.UTC),
        cached=False,
        budget=budget,
        features_bytes=document.size_bytes,
        features_trimmed=document.trimmed,
        cost_eur=cost,
        cache_read_tokens=completion.usage.cache_read_input_tokens,
    )
