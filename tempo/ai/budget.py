"""The monthly budget, and the hard stop that enforces it.

The stop is checked before a request is built and again before it is sent,
and it refuses rather than truncates: an exhausted budget produces a
defined state that the endpoint reports, not a smaller call. A budget that
silently degrades into cheaper calls would be worse than one that stops,
because the athlete would have no way of telling a thin answer from a
careless one.

Spend is the sum of ``ai_call.cost_eur`` for the calendar month, which is
an estimate — see :mod:`tempo.ai.pricing`. It is deliberately the estimate
that guards the limit rather than a number fetched from Anthropic: the
guard has to work when the network is the thing that is failing.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import dataclass

from sqlalchemy import Engine

from tempo.ai.client import Usage
from tempo.ai.errors import BudgetExceeded
from tempo.ai.pricing import estimate_cost_eur
from tempo.db.models import AiCall
from tempo.db.session import session_scope
from tempo.reports import build_usage_report

log = logging.getLogger(__name__)

# The euro rate used to convert the published dollar prices. A constant,
# not a lookup: fetching an exchange rate would be a live call in the one
# code path that has to keep working when calls are failing, and a few
# percent of drift does not change what a budget stop decides.
USD_PER_EUR: float = 1.08


@dataclass(frozen=True, slots=True)
class BudgetState:
    """Where the month stands."""

    month: str
    spent_eur: float
    budget_eur: float
    calls: int

    @property
    def remaining_eur(self) -> float:
        return max(self.budget_eur - self.spent_eur, 0.0)

    @property
    def exhausted(self) -> bool:
        """True once the month is spent.

        A budget of zero is exhausted from the first day of the month. That
        is the intended reading: nothing was allowed to be spent, so
        nothing may be.
        """
        return self.spent_eur >= self.budget_eur


def budget_state(
    engine: Engine, *, budget_eur: float, as_of: dt.date | None = None
) -> BudgetState:
    """What has been spent this month, against what was allowed."""
    usage = build_usage_report(engine, as_of=as_of)
    return BudgetState(
        month=usage.month,
        spent_eur=usage.cost_eur,
        budget_eur=budget_eur,
        calls=usage.calls,
    )


def require_budget(
    engine: Engine, *, budget_eur: float, as_of: dt.date | None = None
) -> BudgetState:
    """Refuse before anything is sent, if the month is spent."""
    state = budget_state(engine, budget_eur=budget_eur, as_of=as_of)
    if state.exhausted:
        log.info(
            "AI call refused, monthly budget reached",
            extra={"month": state.month, "spent_eur": round(state.spent_eur, 4)},
        )
        raise BudgetExceeded(
            month=state.month,
            spent_eur=state.spent_eur,
            budget_eur=state.budget_eur,
        )
    return state


def record_call(
    engine: Engine,
    *,
    endpoint: str,
    model: str,
    usage: Usage,
    usd_per_eur: float = USD_PER_EUR,
    ts: dt.datetime | None = None,
) -> float:
    """Book one call against the month, and return what it was charged.

    Recorded whatever the answer turns out to be worth: a call that was
    paid for counts, even if what came back is then discarded.
    """
    cost = estimate_cost_eur(
        model=model,
        input_tokens=usage.input_tokens,
        output_tokens=usage.output_tokens,
        cache_read_tokens=usage.cache_read_input_tokens,
        cache_write_tokens=usage.cache_creation_input_tokens,
        usd_per_eur=usd_per_eur,
    )
    with session_scope(engine) as session:
        session.add(
            AiCall(
                ts=ts or dt.datetime.now(tz=dt.UTC),
                endpoint=endpoint,
                model=model,
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_read_tokens=usage.cache_read_input_tokens,
                cache_write_tokens=usage.cache_creation_input_tokens,
                cost_eur=cost,
            )
        )
    return cost
