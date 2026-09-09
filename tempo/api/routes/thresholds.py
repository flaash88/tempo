"""Every threshold the interface needs, so it holds no number of its own.

The mock-ups write sentences like "mindestens 7 Nächte" and "34 / 42 Tagen".
None of those numbers is in a response text: the API sends the values, the
frontend fills the sentence, and a change to a minimum history propagates
without a second edit anywhere.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from tempo.api.deps import EngineDep, SessionDep, SettingsDep
from tempo.db.models import AthleteSettings
from tempo.db.session import session_scope
from tempo.metrics import thresholds as metric_thresholds

router = APIRouter(prefix="/api", tags=["thresholds"])


@router.get("/thresholds", summary="Schwellen und Mindesthistorien")
def get_thresholds(
    _session: SessionDep, settings: SettingsDep, engine: EngineDep
) -> dict[str, Any]:
    """The thresholds in force, alongside the documented defaults.

    The configurable ones — the readiness weights and the sleep target —
    are reported as they actually stand, so the interface never has to
    guess which of the two it is looking at.
    """
    with session_scope(engine) as db_session:
        athlete = db_session.get(AthleteSettings, 1)
        sleep_target_s = athlete.sleep_target_s if athlete else None

    return metric_thresholds.as_dict(
        readiness_weights=settings.readiness_weights,
        sleep_target_s=sleep_target_s,
    )
