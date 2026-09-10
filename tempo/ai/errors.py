"""Errors from the AI layer.

Kept apart from the ingestion errors because the two fail for different
reasons and the API answers them differently: an exhausted budget is a
defined state of the application, not a fault.
"""

from __future__ import annotations


class AiError(Exception):
    """Base class for everything the AI layer can refuse over."""


class AiApiError(AiError):
    """Anthropic answered with something unusable."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class AiAuthError(AiApiError):
    """Credentials were rejected, or none were configured."""


class AiRateLimited(AiApiError):
    """Rate limited and out of attempts."""


class BudgetExceeded(AiError):
    """The month's budget is spent.

    A defined state, not a failure: the caller reports it as such and makes
    no call. Carries the numbers so the interface can say how much of what
    was used rather than only that something stopped.
    """

    def __init__(self, *, month: str, spent_eur: float, budget_eur: float) -> None:
        super().__init__(
            f"monthly AI budget of {budget_eur:.2f} EUR reached "
            f"({spent_eur:.2f} EUR spent in {month})"
        )
        self.month = month
        self.spent_eur = spent_eur
        self.budget_eur = budget_eur


class FeaturesTooLarge(AiError):
    """The feature document did not fit its size limit even after trimming."""


class AnswerFormatError(AiError):
    """The answer did not have the shape its endpoint requires.

    Raised only after the one repair attempt has also failed. Carries a
    German sentence naming what was wrong, because that sentence reaches
    the screen.
    """
