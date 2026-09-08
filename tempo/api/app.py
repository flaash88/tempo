"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tempo import __version__
from tempo.api.routes import (
    activities,
    auth,
    health,
    performance,
    plan,
    sync,
    thresholds,
    today,
    trends,
)
from tempo.api.routes import (
    settings as settings_routes,
)
from tempo.config import Settings, get_settings
from tempo.db.session import engine_for
from tempo.ingest.sync import ClientFactory, default_client_factory
from tempo.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    app.state.engine = engine_for(settings)
    try:
        yield
    finally:
        app.state.engine.dispose()


def create_app(
    settings: Settings | None = None,
    *,
    client_factory: ClientFactory | None = None,
) -> FastAPI:
    """Build the application.

    ``client_factory`` is how the outbound intervals.icu client gets in. It
    is a parameter rather than a hard-wired import so a test can substitute
    a mocked transport — a test that reaches the network is a broken test,
    and the only reliable way to keep one from doing so is to leave it no
    way to.
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
    return app


app = create_app()
