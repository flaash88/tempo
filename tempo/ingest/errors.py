"""Ingestion errors.

Every message that can reach a log line or the ``sync_log`` table goes
through here, so no credential is ever part of an error string.
"""

from __future__ import annotations


class IngestError(Exception):
    """Base class for everything that can go wrong while importing."""


class FitFileError(IngestError):
    """A FIT file could not be read or carries no usable activity."""


class IntervalsApiError(IngestError):
    """intervals.icu answered with something unusable."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class IntervalsAuthError(IntervalsApiError):
    """Credentials were rejected. Basic auth with username API_KEY is
    required — a bearer token yields 403."""


class RateLimited(IntervalsApiError):
    """Rate limited and out of attempts."""


class GarminUnavailable(IngestError):
    """The optional Garmin connector refused and disabled itself.

    Repeated failed attempts lead to account level blocks, so the module
    stands down rather than retrying.
    """
