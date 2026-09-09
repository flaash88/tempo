"""Anthropic Messages API client.

Written against the HTTP API with ``httpx`` rather than the official SDK,
because the stack in CLAUDE.md names httpx for outgoing calls and adding a
dependency it does not list would be a deviation. The surface used here is
small — one endpoint, one response shape — and the transport is injectable,
which is what keeps the test suite from ever reaching the network.

Nothing in this module decides what to say. It sends a system prompt and a
user message, and hands back the text and the token counts.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Final, Self

import httpx

from tempo.ai.errors import AiApiError, AiAuthError, AiRateLimited

log = logging.getLogger(__name__)

DEFAULT_BASE_URL: Final = "https://api.anthropic.com"
API_VERSION: Final = "2023-06-01"
MESSAGES_PATH: Final = "/v1/messages"

# Long enough for a considered answer, short enough that a stuck request
# does not hold a scheduler slot all night.
DEFAULT_TIMEOUT_S: Final = 120.0

# Room for a full answer without inviting an essay; the prompts ask for
# short, concrete text.
DEFAULT_MAX_TOKENS: Final = 2_000


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    """Backoff for rate limiting and transient failures."""

    max_attempts: int = 3
    initial_backoff_s: float = 2.0
    max_backoff_s: float = 60.0

    def backoff_for(self, attempt: int) -> float:
        return min(self.initial_backoff_s * 2.0 ** (attempt - 1), self.max_backoff_s)


@dataclass(frozen=True, slots=True)
class Usage:
    """What one call cost, in tokens.

    Cache reads and writes are kept apart from plain input tokens because
    they are billed differently, and because a cache read of zero across
    repeated calls is the signal that the cached prefix is not being hit.
    """

    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

    @property
    def total_input(self) -> int:
        return (
            self.input_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )


@dataclass(frozen=True, slots=True)
class Completion:
    """One answer, with the accounting that goes with it."""

    text: str
    model: str
    usage: Usage
    stop_reason: str | None = None


class AnthropicClient:
    """The slice of the Messages API Tempo uses."""

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        transport: httpx.BaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_S,
        retry: RetryPolicy | None = None,
        sleeper: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key:
            raise AiAuthError("no Anthropic API key configured")
        self.retry = retry or RetryPolicy()
        self._sleep = sleeper
        self._client = httpx.Client(
            base_url=base_url,
            transport=transport,
            timeout=timeout,
            headers={
                "x-api-key": api_key,
                "anthropic-version": API_VERSION,
                "content-type": "application/json",
                "accept": "application/json",
            },
        )

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def _retry_after(self, response: httpx.Response, attempt: int) -> float:
        header = response.headers.get("retry-after", "").strip()
        if header.isdigit():
            return min(float(header), self.retry.max_backoff_s)
        return self.retry.backoff_for(attempt)

    def complete(
        self,
        *,
        model: str,
        system: Sequence[dict[str, Any]],
        messages: Sequence[dict[str, Any]],
        max_tokens: int = DEFAULT_MAX_TOKENS,
    ) -> Completion:
        """One request, one answer.

        ``system`` is a list of blocks so a stable prefix can carry
        ``cache_control`` while the volatile part follows it.
        """
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": list(system),
            "messages": list(messages),
        }

        last_error: Exception | None = None
        for attempt in range(1, self.retry.max_attempts + 1):
            try:
                response = self._client.post(MESSAGES_PATH, json=payload)
            except httpx.TransportError as exc:
                # The message never carries the key, which lives in a header.
                last_error = AiApiError(f"request failed: {type(exc).__name__}")
                if attempt == self.retry.max_attempts:
                    break
                self._sleep(self.retry.backoff_for(attempt))
                continue

            status = response.status_code
            if status in (401, 403):
                raise AiAuthError(
                    "Anthropic rejected the credentials", status_code=status
                )
            if status == 429:
                if attempt == self.retry.max_attempts:
                    raise AiRateLimited(
                        f"rate limited after {attempt} attempts", status_code=429
                    )
                self._sleep(self._retry_after(response, attempt))
                continue
            if status >= 500:
                last_error = AiApiError(
                    f"Anthropic returned {status}", status_code=status
                )
                if attempt == self.retry.max_attempts:
                    break
                self._sleep(self.retry.backoff_for(attempt))
                continue
            if status >= 400:
                raise AiApiError(f"Anthropic returned {status}", status_code=status)

            return _parse(response, model)

        raise last_error or AiApiError("request failed")


def _parse(response: httpx.Response, requested_model: str) -> Completion:
    try:
        body = response.json()
    except ValueError as exc:
        raise AiApiError("Anthropic did not return JSON") from exc
    if not isinstance(body, dict):
        raise AiApiError("Anthropic did not return an object")

    blocks = body.get("content")
    text = ""
    if isinstance(blocks, list):
        text = "\n".join(
            block["text"]
            for block in blocks
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        ).strip()

    raw_usage = body.get("usage")
    usage = Usage()
    if isinstance(raw_usage, dict):
        usage = Usage(
            input_tokens=_as_int(raw_usage.get("input_tokens")),
            output_tokens=_as_int(raw_usage.get("output_tokens")),
            cache_creation_input_tokens=_as_int(
                raw_usage.get("cache_creation_input_tokens")
            ),
            cache_read_input_tokens=_as_int(raw_usage.get("cache_read_input_tokens")),
        )

    stop_reason = body.get("stop_reason")
    if not text and stop_reason == "refusal":
        raise AiApiError("the model declined to answer")
    if not text:
        raise AiApiError("the answer carried no text")

    served = body.get("model")
    return Completion(
        text=text,
        model=served if isinstance(served, str) and served else requested_model,
        usage=usage,
        stop_reason=stop_reason if isinstance(stop_reason, str) else None,
    )


def _as_int(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    return int(value)
