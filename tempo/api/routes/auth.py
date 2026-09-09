"""Login, logout and session state.

The cookie is httpOnly, Secure and SameSite=Lax, and carries nothing but an
expiry and a signature — no identity, no claims, nothing worth stealing on
its own. It is signed with a key derived from the stored password hash, so
changing the password ends every session, which is what changing a password
is for.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Annotated

from fastapi import APIRouter, Cookie, HTTPException, Response, status

from tempo.api.auth import (
    SESSION_COOKIE,
    SESSION_LIFETIME,
    hash_problem,
    issue_session,
    verify_password,
    verify_session,
)
from tempo.api.deps import SettingsDep
from tempo.api.schemas import LoginRequest, SessionResponse

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


@router.post("/login", response_model=SessionResponse, summary="Anmelden")
def login(
    payload: LoginRequest, response: Response, settings: SettingsDep
) -> SessionResponse:
    if not settings.has_password:
        # An unfinished setup is not an open door, and the message says which
        # of the two problems it is without revealing anything else.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Keine Anmeldung konfiguriert — TEMPO_PASSWORD_HASH fehlt",
        )

    stored = settings.password_hash.get_secret_value()
    problem = hash_problem(stored)
    if problem is not None:
        # A hash that arrived truncated cannot match anything, and calling
        # that "Passwort falsch" sends the athlete looking for the mistake
        # in the one place it is not.
        log.error("password hash unusable", extra={"reason": problem})
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=problem
        )
    if not verify_password(payload.password, stored):
        log.info("login refused")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Passwort falsch",
        )

    _set_cookie(response, issue_session(stored, issued_at=dt.datetime.now(tz=dt.UTC)))
    log.info("login accepted")
    return SessionResponse(authenticated=True, configured=True)


@router.post("/logout", response_model=SessionResponse, summary="Abmelden")
def logout(response: Response, settings: SettingsDep) -> SessionResponse:
    response.delete_cookie(SESSION_COOKIE, path="/")
    return SessionResponse(authenticated=False, configured=settings.has_password)


@router.get("/session", response_model=SessionResponse, summary="Sitzung")
def session_state(
    settings: SettingsDep,
    tempo_session: Annotated[str | None, Cookie(alias=SESSION_COOKIE)] = None,
) -> SessionResponse:
    """Whether the caller is signed in. Open on purpose, so the interface can
    ask before deciding whether to show a login screen."""
    authenticated = settings.has_password and verify_session(
        tempo_session,
        settings.password_hash.get_secret_value(),
        now=dt.datetime.now(tz=dt.UTC),
    )
    return SessionResponse(
        authenticated=authenticated, configured=settings.has_password
    )
