"""Storing answers, and finding them again.

The key is a hash of everything that produced the answer: the endpoint, the
model, the whole system prompt and the feature document. That has two
consequences worth stating, because both are load-bearing.

A number changing changes the feature document, so the cache cannot serve
an answer about yesterday's data as though it were about today's. And a
rule changing changes the system prompt, so editing the prompt invalidates
every stored answer it produced rather than leaving old text in
circulation under new rules. Nothing here expires on a timer, because
there is nothing left for a timer to protect against.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from dataclasses import dataclass
from hashlib import blake2b
from typing import Any

from sqlalchemy import Engine, select

from tempo.db.models import AiResponse
from tempo.db.session import session_scope


@dataclass(frozen=True, slots=True)
class CachedAnswer:
    """A stored answer, detached from the session that read it."""

    answer: str
    model: str
    created_at: dt.datetime
    features_json: str


def cache_key(
    *,
    endpoint: str,
    model: str,
    system: list[dict[str, Any]],
    features_json: str,
    messages: Sequence[dict[str, Any]] = (),
) -> str:
    """A stable digest of the whole request.

    The messages are hashed as they will be sent, history included, so two
    questions that differ only in what came before them are two different
    answers rather than one served twice.
    """
    digest = blake2b(digest_size=32)
    for part in (endpoint, model):
        digest.update(part.encode("utf-8"))
        digest.update(b"\x00")
    for block in system:
        text = block.get("text")
        if isinstance(text, str):
            digest.update(text.encode("utf-8"))
            digest.update(b"\x00")
    for message in messages:
        digest.update(str(message.get("role", "")).encode("utf-8"))
        digest.update(b"\x00")
        digest.update(str(message.get("content", "")).encode("utf-8"))
        digest.update(b"\x00")
    digest.update(features_json.encode("utf-8"))
    return digest.hexdigest()


def lookup(engine: Engine, key: str) -> CachedAnswer | None:
    with session_scope(engine) as session:
        row = session.scalar(select(AiResponse).where(AiResponse.cache_key == key))
        if row is None:
            return None
        created = row.created_at
        if created.tzinfo is None:
            created = created.replace(tzinfo=dt.UTC)
        return CachedAnswer(
            answer=row.answer,
            model=row.model,
            created_at=created,
            features_json=row.features_json,
        )


def store(
    engine: Engine,
    *,
    key: str,
    endpoint: str,
    model: str,
    answer: str,
    features_json: str,
    as_of: dt.date | None = None,
) -> None:
    """Keep the answer, replacing any earlier one under the same key.

    A replacement only happens when an answer was explicitly re-requested,
    since an identical request otherwise reads the stored one instead of
    producing a second.
    """
    with session_scope(engine) as session:
        existing = session.scalar(select(AiResponse).where(AiResponse.cache_key == key))
        if existing is not None:
            existing.answer = answer
            existing.model = model
            existing.features_json = features_json
            existing.as_of = as_of
            existing.created_at = dt.datetime.now(tz=dt.UTC)
            return
        session.add(
            AiResponse(
                cache_key=key,
                endpoint=endpoint,
                model=model,
                as_of=as_of,
                features_json=features_json,
                answer=answer,
            )
        )
