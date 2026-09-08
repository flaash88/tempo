"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from tempo import __version__
from tempo.api.routes import health
from tempo.config import Settings, get_settings
from tempo.db.session import engine_for
from tempo.logging import configure_logging


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings: Settings = app.state.settings
    app.state.engine = engine_for(settings)
    try:
        yield
    finally:
        app.state.engine.dispose()


def create_app(settings: Settings | None = None) -> FastAPI:
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
    app.include_router(health.router)
    return app


app = create_app()
