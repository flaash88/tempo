"""Shared dependencies: settings, engine and the session gate.

One user, one password. Everything under ``/api`` except the login itself
requires a session; ``/health`` sits outside ``/api`` and stays open so the
tunnel and the container health check can reach it.

Cloudflare Access is not assumed. Whoever puts it in front gets a second
layer, and this one does not lean on it: an unauthenticated request is
refused here regardless of what any proxy header claims.
"""

from __future__ import annotations

import datetime as dt
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException, Request, status
from sqlalchemy import Engine

from tempo.api.auth import SESSION_COOKIE, verify_session
from tempo.config import Settings


def get_settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_engine(request: Request) -> Engine:
    engine = getattr(request.app.state, "engine", None)
    if engine is None:  # pragma: no cover - the lifespan always sets it
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Datenbank nicht verfügbar",
        )
    return engine  # type: ignore[no-any-return]


SettingsDep = Annotated[Settings, Depends(get_settings_dep)]
EngineDep = Annotated[Engine, Depends(get_engine)]


def require_session(
    settings: SettingsDep,
    tempo_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> None:
    """Refuse anything without a valid session cookie.

    A deployment with no password configured refuses everything rather than
    letting everything through — an unfinished setup must not be an open
    door.
    """
    if not settings.has_password:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Keine Anmeldung konfiguriert — TEMPO_PASSWORD_HASH fehlt",
        )
    if not verify_session(
        tempo_session,
        settings.password_hash.get_secret_value(),
        now=dt.datetime.now(tz=dt.UTC),
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Nicht angemeldet",
        )


SessionDep = Annotated[None, Depends(require_session)]
