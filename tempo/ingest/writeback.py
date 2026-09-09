"""The way back to the watch.

A session Tempo generates is put in the athlete's intervals.icu calendar,
and Garmin syncs it from there onto the watch. That is the whole of the
channel: one event per session, written with ``POST /athlete/0/events`` and
changed with ``PUT``, idempotent over ``external_id``.

Two rules shape everything in this module, and both are about not doing
things on the athlete's behalf.

**Nothing goes out unconfirmed.** A proposal is a proposal until the
athlete says otherwise, and a session that changes after being confirmed
loses its confirmation — what was agreed to was a particular session, not
a slot in the week.

**Nothing confirmed is silently replaced.** A day that already carries a
confirmed session refuses a second one; replacing it is a separate,
explicit act. A session the source itself owns is never touched at all:
it is already where it came from, and Tempo has no business rewriting
what the athlete wrote in another interface.
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tempo.config import Settings
from tempo.db.models import DataSource, PlannedWorkout, WorkoutSyncStatus, utcnow
from tempo.db.session import session_scope
from tempo.ingest.errors import IntervalsApiError
from tempo.ingest.sync import ClientFactory

log = logging.getLogger(__name__)

# The category intervals.icu uses for a training session, as opposed to a
# race or a note.
WORKOUT_CATEGORY = "WORKOUT"

# When a session with no time of its own lands in the calendar. Garmin
# shows it as that day's workout either way; the time only decides where
# it sits in the day's list.
DEFAULT_START_LOCAL_TIME = dt.time(6, 0)

# The prefix of every identifier Tempo generates, so a row's origin is
# visible in the database without joining anything.
ID_PREFIX = "tempo-"


class WritebackError(Exception):
    """Something the athlete has to resolve, not a fault."""


class NotOurWorkout(WritebackError):
    """The session belongs to the source, and is not Tempo's to send."""


class NotConfirmed(WritebackError):
    """Nothing goes to the calendar before it is confirmed."""


class AlreadyConfirmed(WritebackError):
    """A confirmed session is not overwritten without being asked."""


class TransferInFlight(WritebackError):
    """A transfer is already running for this session."""


@dataclass(frozen=True, slots=True)
class WorkoutProposal:
    """A session Tempo suggests. Not yet an appointment."""

    date: dt.date
    sport: str = "Run"
    name: str | None = None
    description: str | None = None
    target_time_s: int | None = None
    target_dist_m: float | None = None
    target_load: float | None = None
    workout_doc: dict[str, Any] | None = None


def new_workout_id() -> str:
    return f"{ID_PREFIX}{uuid.uuid4().hex[:16]}"


def _apply(entry: PlannedWorkout, proposal: WorkoutProposal) -> None:
    entry.date = proposal.date
    entry.category = WORKOUT_CATEGORY
    entry.sport = proposal.sport
    entry.name = proposal.name
    entry.description = proposal.description
    entry.target_time_s = proposal.target_time_s
    entry.target_dist_m = proposal.target_dist_m
    entry.target_load = proposal.target_load
    entry.workout_doc = proposal.workout_doc


def _blocking_entry(
    session: Session, *, day: dt.date, sport: str, exclude_id: str | None = None
) -> PlannedWorkout | None:
    """An existing session on the same day that must not be walked over."""
    candidates = session.scalars(
        select(PlannedWorkout)
        .where(PlannedWorkout.date == day)
        .where(PlannedWorkout.category == WORKOUT_CATEGORY)
    ).all()
    for entry in candidates:
        if entry.id == exclude_id:
            continue
        if entry.sport and sport and entry.sport != sport:
            continue
        if entry.source != DataSource.TEMPO:
            # The athlete's own calendar entry. Never replaced from here,
            # not even on request: it was not written here.
            return entry
        if entry.confirmed_at is not None:
            return entry
    return None


def propose_workout(
    engine: Engine, proposal: WorkoutProposal, *, replace: bool = False
) -> PlannedWorkout:
    """Store a suggested session, unconfirmed and unsent.

    Refuses a day that already carries a confirmed session unless
    ``replace`` says so, and refuses one the source owns in any case. A
    replaced session drops back to unconfirmed: the athlete agreed to what
    was there before, not to whatever takes its place.
    """
    with session_scope(engine) as session:
        blocking = _blocking_entry(session, day=proposal.date, sport=proposal.sport)
        if blocking is not None:
            if not replace or blocking.source != DataSource.TEMPO:
                raise AlreadyConfirmed(
                    f"{proposal.date.isoformat()} carries a confirmed session"
                )
            entry = blocking
            _apply(entry, proposal)
            _withdraw_confirmation(entry)
        else:
            entry = PlannedWorkout(
                id=new_workout_id(),
                source=DataSource.TEMPO,
                sync_status=WorkoutSyncStatus.NOT_SENT,
            )
            entry.external_id = entry.id
            _apply(entry, proposal)
            session.add(entry)
        session.flush()
        session.expunge(entry)
    log.info("workout proposed", extra={"workout": entry.id})
    return entry


def _withdraw_confirmation(entry: PlannedWorkout) -> None:
    """A changed session has to be confirmed again.

    If it is already on the watch it becomes outdated rather than unsent:
    the old version is still on the watch, and saying otherwise would hide
    that from the athlete.
    """
    entry.confirmed_at = None
    entry.sync_error = None
    if entry.sync_status in (WorkoutSyncStatus.ON_WATCH, WorkoutSyncStatus.OUTDATED):
        entry.sync_status = WorkoutSyncStatus.OUTDATED
    else:
        entry.sync_status = WorkoutSyncStatus.NOT_SENT


def update_workout(
    engine: Engine, workout_id: str, proposal: WorkoutProposal
) -> PlannedWorkout | None:
    """Change a proposed session. Confirmation does not survive it."""
    with session_scope(engine) as session:
        entry = session.get(PlannedWorkout, workout_id)
        if entry is None:
            return None
        _require_ours(entry)
        if entry.sync_status == WorkoutSyncStatus.SENDING:
            raise TransferInFlight(f"{workout_id} is being transferred")
        blocking = _blocking_entry(
            session, day=proposal.date, sport=proposal.sport, exclude_id=entry.id
        )
        if blocking is not None:
            raise AlreadyConfirmed(
                f"{proposal.date.isoformat()} carries a confirmed session"
            )
        _apply(entry, proposal)
        _withdraw_confirmation(entry)
        session.flush()
        session.expunge(entry)
    return entry


def confirm_workout(engine: Engine, workout_id: str) -> PlannedWorkout | None:
    """The athlete's yes. Without it nothing is sent anywhere."""
    with session_scope(engine) as session:
        entry = session.get(PlannedWorkout, workout_id)
        if entry is None:
            return None
        _require_ours(entry)
        if entry.confirmed_at is None:
            entry.confirmed_at = utcnow()
        session.flush()
        session.expunge(entry)
    log.info("workout confirmed", extra={"workout": entry.id})
    return entry


def _require_ours(entry: PlannedWorkout) -> None:
    if entry.source != DataSource.TEMPO:
        raise NotOurWorkout(
            f"{entry.id} came from {entry.source} and is not Tempo's to change"
        )


def event_payload(entry: PlannedWorkout) -> dict[str, Any]:
    """The session as an intervals.icu calendar event.

    Only what is actually set travels: an event with ``"distance": null``
    is not the same request as one without the key, and the second is the
    one that means "no distance target".
    """
    start = dt.datetime.combine(entry.date, DEFAULT_START_LOCAL_TIME)
    payload: dict[str, Any] = {
        "start_date_local": start.isoformat(),
        "category": WORKOUT_CATEGORY,
        "external_id": entry.external_id or entry.id,
    }
    optional: dict[str, Any] = {
        "type": entry.sport,
        "name": entry.name,
        "description": entry.description,
        "moving_time": entry.target_time_s,
        "distance": entry.target_dist_m,
        "icu_training_load": entry.target_load,
        "workout_doc": entry.workout_doc,
    }
    payload.update({key: value for key, value in optional.items() if value is not None})
    return payload


@dataclass(frozen=True, slots=True)
class PushReport:
    """What one transfer did."""

    workout_id: str
    status: str
    sent: bool
    remote_event_id: str | None = None
    detail: str | None = None


def push_workout(
    engine: Engine,
    settings: Settings,
    workout_id: str,
    *,
    client_factory: ClientFactory,
) -> PushReport | None:
    """Send one confirmed session to the calendar.

    The status is written before the request goes out and again after it
    comes back, each in its own transaction. A crash in between therefore
    leaves ``sending`` on the row, which is the truth: nobody knows whether
    the event arrived, and the interface says so instead of claiming one
    of the two answers.
    """
    with session_scope(engine) as session:
        entry = session.get(PlannedWorkout, workout_id)
        if entry is None:
            return None
        _require_ours(entry)
        if entry.confirmed_at is None:
            raise NotConfirmed(f"{workout_id} has not been confirmed")
        if entry.sync_status == WorkoutSyncStatus.SENDING:
            raise TransferInFlight(f"{workout_id} is being transferred")
        if entry.sync_status == WorkoutSyncStatus.ON_WATCH:
            # Already there, and unchanged since. Sending it again would
            # be one more request for no difference.
            return PushReport(
                workout_id=workout_id,
                status=entry.sync_status,
                sent=False,
                remote_event_id=entry.remote_event_id,
                detail="unverändert bereits auf der Uhr",
            )
        payload = event_payload(entry)
        remote_id = entry.remote_event_id
        entry.sync_status = WorkoutSyncStatus.SENDING
        entry.sync_error = None

    try:
        with client_factory(settings) as client:
            body = (
                client.update_event(remote_id, payload)
                if remote_id
                else client.create_event(payload)
            )
    except IntervalsApiError as exc:
        # The client redacts credentials before raising; this is safe to
        # store and to show.
        detail = str(exc)
        with session_scope(engine) as session:
            failed = session.get(PlannedWorkout, workout_id)
            if failed is not None:
                failed.sync_status = WorkoutSyncStatus.FAILED
                failed.sync_error = detail
        log.warning("workout transfer failed", extra={"workout": workout_id})
        return PushReport(
            workout_id=workout_id,
            status=WorkoutSyncStatus.FAILED,
            sent=False,
            remote_event_id=remote_id,
            detail=detail,
        )

    created = body.get("id")
    with session_scope(engine) as session:
        stored = session.get(PlannedWorkout, workout_id)
        if stored is None:  # pragma: no cover - deleted mid-flight
            return None
        stored.sync_status = WorkoutSyncStatus.ON_WATCH
        stored.synced_at = utcnow()
        stored.sync_error = None
        if created is not None:
            stored.remote_event_id = str(created)
        final_remote = stored.remote_event_id
    log.info("workout transferred", extra={"workout": workout_id})
    return PushReport(
        workout_id=workout_id,
        status=WorkoutSyncStatus.ON_WATCH,
        sent=True,
        remote_event_id=final_remote,
    )
