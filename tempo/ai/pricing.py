"""What a call costs, in euros.

Two things here are estimates and are labelled as such wherever the number
surfaces: the list prices below are Anthropic's published US dollar rates
at the time of writing, and the exchange rate is a configured constant.
Neither is authoritative — the Anthropic console is — so the budget stop is
a guard rail, not an accountant.

The prices live here rather than in ``thresholds`` because they are not a
property of the athlete or of any metric; they are a property of a vendor's
price list, and they change without anything in the training data changing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

TOKENS_PER_MILLION: Final = 1_000_000

# Cache reads are billed at roughly a tenth of the input rate, cache writes
# at roughly a quarter above it.
CACHE_READ_FACTOR: Final = 0.1
CACHE_WRITE_FACTOR: Final = 1.25


@dataclass(frozen=True, slots=True)
class ModelPrice:
    """US dollars per million tokens."""

    input_usd: float
    output_usd: float


# Published list prices. A model that is not in this table is priced with
# DEFAULT_PRICE and the estimate is flagged, rather than being counted as
# free — an unknown model must not quietly spend an unlimited budget.
LIST_PRICES_USD: Final[dict[str, ModelPrice]] = {
    "claude-opus-5": ModelPrice(input_usd=5.00, output_usd=25.00),
    "claude-sonnet-5": ModelPrice(input_usd=2.00, output_usd=10.00),
    "claude-haiku-4-5": ModelPrice(input_usd=1.00, output_usd=5.00),
}

# The most expensive rate in the table, so an unrecognised model is
# over-estimated rather than under-estimated.
DEFAULT_PRICE: Final = ModelPrice(input_usd=5.00, output_usd=25.00)


def price_for(model: str) -> tuple[ModelPrice, bool]:
    """The price of a model, and whether it was actually known."""
    price = LIST_PRICES_USD.get(model)
    if price is None:
        return DEFAULT_PRICE, False
    return price, True


def estimate_cost_eur(
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
    usd_per_eur: float,
) -> float:
    """Estimate what one call cost.

    Rounded to six decimals: a single call can cost a fraction of a cent,
    and truncating that to two would make a month of small calls look free.
    """
    if usd_per_eur <= 0:
        raise ValueError("the exchange rate must be positive")
    price, _known = price_for(model)

    billable_input = (
        input_tokens
        + cache_read_tokens * CACHE_READ_FACTOR
        + cache_write_tokens * CACHE_WRITE_FACTOR
    )
    usd = (
        billable_input * price.input_usd + output_tokens * price.output_usd
    ) / TOKENS_PER_MILLION
    return round(usd / usd_per_eur, 6)
