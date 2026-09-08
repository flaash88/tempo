"""Recording how a day felt.

The three values are stored exactly as given and read by nothing. They
exist so that the readiness weights can one day be calibrated against real
perception rather than against each other, and calibration needs the felt
part on record from now on.

Nothing here interprets them. No scale is asserted, no direction is
implied, and the bounds are a plausibility check that keeps a typo out of
the column — not a claim about what the numbers mean.
"""

from __future__ import annotations

import datetime as dt
import logging

from fastapi import APIRouter, HTTPException, status

from tempo.api.deps import EngineDep, SessionDep
from tempo.api.schemas import SubjectiveDay, SubjectiveUpdate
from tempo.reports import set_subjective

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/wellness", tags=["wellness"])

# Far enough back to fill in a forgotten week, not so far that a typo in a
# date silently creates a row in 1970.
EARLIEST_ENTRY = dt.date(2000, 1, 1)


@router.put("/{day}", response_model=SubjectiveDay, summary="Tagesform eintragen")
def put_wellness(
    day: dt.date,
    payload: SubjectiveUpdate,
    _session: SessionDep,
    engine: EngineDep,
) -> SubjectiveDay:
    """Record fatigue, soreness and mood for one day.

    A field left out of the body stays as it is; a field sent as ``null``
    is cleared. The wellness row is created when the day has none, because
    the athlete can say how a day felt on a day the watch has nothing to
    say about.
    """
    today = dt.datetime.now(tz=dt.UTC).date()
    if day > today:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Ein Tag in der Zukunft lässt sich nicht bewerten",
        )
    if day < EARLIEST_ENTRY:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Datum vor {EARLIEST_ENTRY.isoformat()}",
        )

    named = payload.model_fields_set
    if not named:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Kein Feld angegeben",
        )

    report = set_subjective(
        engine,
        day,
        fatigue=payload.fatigue,
        soreness=payload.soreness,
        mood=payload.mood,
        fields=sorted(named),
    )
    log.info(
        "subjective day recorded",
        extra={"day": day.isoformat(), "fields": ",".join(sorted(named))},
    )
    return SubjectiveDay(
        date=report.date,
        fatigue=report.fatigue,
        soreness=report.soreness,
        mood=report.mood,
        source=report.source,
    )
