"""Starting a sync, and reporting where the last one got to."""

from __future__ import annotations

import datetime as dt
import logging

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from sqlalchemy import Engine, select

from tempo.api.deps import EngineDep, SessionDep, SettingsDep
from tempo.api.schemas import (
    SyncSourceStatus,
    SyncStartedResponse,
    SyncStatusResponse,
)
from tempo.config import Settings
from tempo.db.models import SyncLog, SyncState, SyncStatus
from tempo.db.session import session_scope
from tempo.ingest.garmin_optional import sync_garmin
from tempo.ingest.sync import MIN_SYNC_INTERVAL, ClientFactory, sync_intervals

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["sync"])


def collect_status(engine: Engine) -> SyncStatusResponse:
    """The state of each source, from the watermark and the audit log."""
    with session_scope(engine) as session:
        states = {state.source: state for state in session.scalars(select(SyncState))}
        latest: dict[str, SyncLog] = {}
        for entry in session.scalars(
            select(SyncLog).order_by(SyncLog.started_at.desc()).limit(50)
        ):
            latest.setdefault(entry.source, entry)

    sources: list[SyncSourceStatus] = []
    next_allowed: dt.datetime | None = None
    for name in sorted(set(states) | set(latest)):
        state = states.get(name)
        last_run = latest.get(name)
        last_success = state.last_success_at if state else None
        if last_success is not None:
            if last_success.tzinfo is None:
                last_success = last_success.replace(tzinfo=dt.UTC)
            due = last_success + MIN_SYNC_INTERVAL
            next_allowed = due if next_allowed is None else max(next_allowed, due)
        sources.append(
            SyncSourceStatus(
                source=name,
                status=last_run.status if last_run else None,
                last_success_at=last_success,
                last_activity_start=state.last_activity_start if state else None,
                last_wellness_date=state.last_wellness_date if state else None,
                started_at=last_run.started_at if last_run else None,
                finished_at=last_run.finished_at if last_run else None,
                detail=last_run.detail if last_run else None,
                running=bool(last_run and last_run.status == SyncStatus.RUNNING),
            )
        )
    return SyncStatusResponse(sources=sources, next_allowed_at=next_allowed)


def _run_sync(
    engine: Engine,
    settings: Settings,
    client_factory: ClientFactory,
    *,
    full: bool,
    force: bool,
) -> None:
    """The background job. Failures are recorded, never raised into nothing."""
    try:
        report = sync_intervals(
            engine,
            settings,
            full=full,
            force=force,
            client_factory=client_factory,
        )
        log.info(
            "sync finished", extra={"status": report.status, "source": report.source}
        )
        if settings.garmin_direct_enabled:
            garmin = sync_garmin(engine, settings)
            log.info("garmin finished", extra={"status": garmin.status})
    except Exception:
        # sync_log already carries the reason; this keeps the task from
        # dying silently in the background.
        log.exception("sync failed")


@router.post(
    "/sync",
    response_model=SyncStartedResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Sync anstoßen",
)
def start_sync(
    request: Request,
    _session: SessionDep,
    settings: SettingsDep,
    engine: EngineDep,
    background: BackgroundTasks,
    full: bool = False,
    force: bool = False,
) -> SyncStartedResponse:
    """Start a sync in the background.

    A full history sync takes minutes, so the request returns as soon as the
    run has been handed off and ``GET /api/sync/status`` reports the rest.
    """
    if not settings.has_intervals_credentials:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="INTERVALS_API_KEY ist nicht gesetzt",
        )
    current = collect_status(engine)
    if any(source.running for source in current.sources):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Ein Sync läuft bereits",
        )

    background.add_task(
        _run_sync,
        engine,
        settings,
        request.app.state.client_factory,
        full=full,
        force=force,
    )
    return SyncStartedResponse(started=True, detail="Sync gestartet")


@router.get("/sync/status", response_model=SyncStatusResponse, summary="Sync-Status")
def sync_status(_session: SessionDep, engine: EngineDep) -> SyncStatusResponse:
    return collect_status(engine)
