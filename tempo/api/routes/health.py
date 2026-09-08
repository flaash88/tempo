"""Liveness endpoint.

Answers without authentication and without touching athlete data, so it is
safe to expose to the tunnel's health check.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from tempo import __version__
from tempo.api.schemas import HealthResponse

log = logging.getLogger(__name__)

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse, summary="Liveness")
def health(request: Request) -> HealthResponse:
    engine = getattr(request.app.state, "engine", None)
    if engine is None:
        return HealthResponse(version=__version__, database="error")

    try:
        with engine.connect() as connection:
            revision = connection.execute(
                text("SELECT version_num FROM alembic_version")
            ).scalar_one_or_none()
    except SQLAlchemyError:
        # An unmigrated database is an expected state right after install,
        # not an incident — the answer says so instead of raising.
        log.info("health check found no schema", extra={"database": "missing"})
        return HealthResponse(version=__version__, database="missing")

    return HealthResponse(version=__version__, database="ok", schema_revision=revision)
