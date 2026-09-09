"""The Einstellungen screen, and the only writable endpoint.

**No credential ever comes back, not even masked.** The mock-up shows
``sk-ant-•••• 7f2c``; what the API sends is ``{"valid": true,
"last4": "7f2c"}`` and the interface draws the dots. A masked key is still
a prefix of a key, and there is no reason for one to leave the server.

Nor can a credential be set here: keys live in the environment, so
rotating one is a deployment action and never something a session cookie
can do.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path

from fastapi import APIRouter

from tempo.api.auth import last4
from tempo.api.deps import EngineDep, SessionDep, SettingsDep
from tempo.api.routes.sync import collect_status
from tempo.api.schemas import (
    AiUsage,
    CredentialStatus,
    SettingsResponse,
    SettingsUpdate,
    ZoneBounds,
)
from tempo.config import Settings
from tempo.db.models import AthleteSettings
from tempo.db.session import session_scope
from tempo.metrics.zones import pace_zones_from_threshold, zones_for_athlete
from tempo.reports import build_usage_report

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["settings"])


def _zone_bounds(
    athlete: AthleteSettings | None,
) -> tuple[ZoneBounds | None, ZoneBounds | None]:
    hr_zones = zones_for_athlete(
        lthr=athlete.lthr if athlete else None,
        hr_max=athlete.hr_max if athlete else None,
        zone_model=athlete.zone_model if athlete else None,
    )
    pace_zones = None
    if athlete and athlete.threshold_pace_s_per_km:
        pace_zones = pace_zones_from_threshold(athlete.threshold_pace_s_per_km)

    return (
        None
        if hr_zones is None
        else ZoneBounds(
            kind=hr_zones.kind,
            model=hr_zones.model,
            lower_bounds=list(hr_zones.lower_bounds),
        ),
        None
        if pace_zones is None
        else ZoneBounds(
            kind=pace_zones.kind,
            model=pace_zones.model,
            lower_bounds=list(pace_zones.lower_bounds),
        ),
    )


def _pending_fit_files(settings: Settings) -> int:
    """How many raw files are waiting in the data volume's inbox."""
    inbox: Path = settings.fit_dir
    if not inbox.is_dir():
        return 0
    return sum(1 for path in inbox.glob("*.fit") if path.is_file())


def _build_response(
    settings: Settings, engine: EngineDep, athlete: AthleteSettings | None
) -> SettingsResponse:
    hr_zones, pace_zones = _zone_bounds(athlete)
    usage = build_usage_report(engine)

    return SettingsResponse(
        hr_max=athlete.hr_max if athlete else None,
        hr_rest=athlete.hr_rest if athlete else None,
        lthr=athlete.lthr if athlete else None,
        threshold_pace_s_per_km=(athlete.threshold_pace_s_per_km if athlete else None),
        sleep_target_s=athlete.sleep_target_s if athlete else None,
        zone_model=athlete.zone_model if athlete else "friel_run_lthr",
        hr_zones=hr_zones,
        pace_zones=pace_zones,
        updated_at=athlete.updated_at if athlete else None,
        intervals=CredentialStatus(
            valid=settings.has_intervals_credentials,
            last4=last4(settings.intervals_api_key.get_secret_value()),
        ),
        anthropic=CredentialStatus(
            valid=settings.has_anthropic_credentials,
            last4=last4(settings.anthropic_api_key.get_secret_value()),
        ),
        intervals_athlete_id=settings.intervals_athlete_id,
        ai_model_daily=settings.anthropic_model_daily,
        ai_model_planning=settings.anthropic_model_planning,
        ai_usage=AiUsage(
            month=usage.month,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cost_eur=usage.cost_eur,
            budget_eur=settings.monthly_budget_eur,
            calls=usage.calls,
        ),
        garmin_direct_enabled=settings.garmin_direct_enabled,
        readiness_weights=settings.readiness_weights.as_dict(),
        fit_files_pending=_pending_fit_files(settings),
        sync=collect_status(engine),
    )


@router.get("/settings", response_model=SettingsResponse, summary="Einstellungen")
def get_settings(
    _session: SessionDep, settings: SettingsDep, engine: EngineDep
) -> SettingsResponse:
    with session_scope(engine) as db_session:
        athlete = db_session.get(AthleteSettings, 1)
        if athlete is not None:
            db_session.expunge(athlete)
    return _build_response(settings, engine, athlete)


@router.put(
    "/settings", response_model=SettingsResponse, summary="Einstellungen ändern"
)
def put_settings(
    payload: SettingsUpdate,
    _session: SessionDep,
    settings: SettingsDep,
    engine: EngineDep,
) -> SettingsResponse:
    """Change the athlete's own numbers. Credentials are not among them."""
    changes = payload.model_dump(exclude_unset=True)
    with session_scope(engine) as db_session:
        athlete = db_session.get(AthleteSettings, 1)
        if athlete is None:
            athlete = AthleteSettings(id=1)
            db_session.add(athlete)
        for name, value in changes.items():
            setattr(athlete, name, value)
        athlete.updated_at = dt.datetime.now(tz=dt.UTC)
        db_session.flush()
        db_session.expunge(athlete)

    log.info("athlete settings updated", extra={"fields": ",".join(sorted(changes))})
    return _build_response(settings, engine, athlete)
