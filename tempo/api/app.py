"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tempo import __version__
from tempo.ai.service import AiClientFactory, default_ai_client_factory
from tempo.api.auth import hash_problem
from tempo.api.routes import (
    activities,
    ai,
    auth,
    health,
    performance,
    plan,
    sync,
    thresholds,
    today,
    trends,
    wellness,
    workouts,
)
from tempo.api.routes import (
    settings as settings_routes,
)
from tempo.api.static import mount_frontend
from tempo.config import Settings, get_settings
from tempo.db.session import engine_for
from tempo.ingest.sync import ClientFactory, default_client_factory
from tempo.logging import configure_logging

log = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    _report_password_hash(settings)
    app.state.engine = engine_for(settings)
    try:
        yield
    finally:
        app.state.engine.dispose()


def _report_password_hash(settings: Settings) -> None:
    """Say at start-up what would otherwise only show up as a wrong password.

    A hash truncated by an unescaped ``$`` in .env looks exactly like a
    mistyped password at the login, which is the most misleading place for
    it to surface. Logged once, on the way up, where a deployment is
    actually watching.
    """
    problem = hash_problem(settings.password_hash.get_secret_value())
    if problem is not None:
        log.error("configuration problem", extra={"reason": problem})


def create_app(
    settings: Settings | None = None,
    *,
    client_factory: ClientFactory | None = None,
    ai_client_factory: AiClientFactory | None = None,
) -> FastAPI:
    """Build the application.

    ``client_factory`` is how the outbound intervals.icu client gets in,
    and ``ai_client_factory`` the same for Anthropic. They are parameters
    rather than hard-wired imports so a test can substitute a mocked
    transport — a test that reaches the network is a broken test, and the
    only reliable way to keep one from doing so is to leave it no way to.
    """
    configure_logging()
    app = FastAPI(
        title="Tempo",
        version=__version__,
        summary="Selbstgehostete Trainingsanalyse",
        lifespan=lifespan,
        # No public deployment — the docs stay available behind the tunnel.
        docs_url="/docs",
        redoc_url=None,
    )
    app.state.settings = settings or get_settings()
    app.state.client_factory = client_factory or default_client_factory
    app.state.ai_client_factory = ai_client_factory or default_ai_client_factory
    # /health stays outside /api and outside the session gate; everything
    # else requires one, including the endpoints that only read.
    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(today.router)
    app.include_router(activities.router)
    app.include_router(trends.router)
    app.include_router(performance.router)
    app.include_router(plan.router)
    app.include_router(settings_routes.router)
    app.include_router(thresholds.router)
    app.include_router(sync.router)
    app.include_router(wellness.router)
    app.include_router(workouts.router)
    app.include_router(ai.router)
    # Last, because it claims "/": the API routes above have to match
    # first, and everything left over is a route in the app itself.
    app.state.frontend = mount_frontend(app, app.state.settings.web_dir)
    return app


app = create_app()
