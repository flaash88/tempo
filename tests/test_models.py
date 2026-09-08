"""The data model holds what the metric engine will need from it."""

from __future__ import annotations

import datetime as dt

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from tempo.db.models import (
    Activity,
    ActivityStream,
    AthleteSettings,
    DailyLoad,
    FitnessDay,
    WellnessDay,
    ZoneModel,
)
from tempo.db.session import session_factory
from tests.fixtures.synthetic import (
    REFERENCE_DATE,
    make_activity,
    make_lap,
    make_stream,
    make_wellness_block,
)


def test_activity_with_streams_and_laps_round_trips(session: Session) -> None:
    session.add(make_activity())
    session.add_all(make_stream())
    session.add(make_lap())
    session.commit()

    stored = session.get(Activity, "syn-1")

    assert stored is not None
    assert len(stored.streams) == 5
    assert len(stored.laps) == 1
    assert stored.avg_pace_s_per_km == pytest.approx(360.0)


def test_a_dropout_in_the_stream_stays_none(session: Session) -> None:
    """Gaps are marked, never interpolated."""
    session.add(make_activity())
    session.add_all(make_stream())
    session.commit()

    gap = session.get(ActivityStream, ("syn-1", 2))

    assert gap is not None
    assert gap.hr is None
    assert gap.speed_m_s is not None


def test_deleting_an_activity_removes_its_streams_and_laps(
    engine: Engine,
) -> None:
    factory = session_factory(engine)
    with factory() as session:
        session.add(make_activity())
        session.add_all(make_stream())
        session.add(make_lap())
        session.commit()

    with factory() as session:
        activity = session.get(Activity, "syn-1")
        assert activity is not None
        session.delete(activity)
        session.commit()

    with factory() as session:
        remaining = session.scalar(select(func.count()).select_from(ActivityStream))

    assert remaining == 0


def test_a_run_without_heart_rate_is_storable(session: Session) -> None:
    """An activity recorded without a chest strap is data, not an error."""
    session.add(make_activity("syn-nohr", avg_hr=None))
    session.add_all(make_stream("syn-nohr", with_hr=False))
    session.commit()

    stored = session.get(Activity, "syn-nohr")

    assert stored is not None
    assert stored.avg_hr is None
    assert all(sample.hr is None for sample in stored.streams)


def test_wellness_blocks_may_have_gaps_between_them(session: Session) -> None:
    """Three separate blocks with months in between is the real data shape."""
    session.add_all(make_wellness_block(dt.date(2025, 9, 3), 24))
    session.add_all(make_wellness_block(dt.date(2026, 1, 6), 25))
    session.commit()

    days = session.scalars(select(WellnessDay.date).order_by(WellnessDay.date))
    dates = list(days)

    assert len(dates) == 49
    assert dt.date(2025, 11, 1) not in dates


def test_wellness_day_may_carry_a_resting_hr_without_hrv(
    session: Session,
) -> None:
    session.add_all(make_wellness_block(REFERENCE_DATE, 1, with_hrv=False))
    session.commit()

    day = session.get(WellnessDay, REFERENCE_DATE)

    assert day is not None
    assert day.resting_hr is not None
    assert day.hrv_rmssd is None


def test_a_day_without_a_session_is_stored_as_load_zero(
    session: Session,
) -> None:
    """Empty days are materialised so CTL and ATL decay correctly."""
    session.add(DailyLoad(date=REFERENCE_DATE))
    session.commit()

    day = session.get(DailyLoad, REFERENCE_DATE)

    assert day is not None
    assert day.duration_s == 0
    assert day.distance_m == 0.0
    # Unknown is not zero: no session means no TRIMP to speak of.
    assert day.trimp is None


def test_fitness_day_without_enough_history_has_no_values(
    session: Session,
) -> None:
    session.add(FitnessDay(date=REFERENCE_DATE, confidence=0.0, days_of_history=3))
    session.commit()

    day = session.get(FitnessDay, REFERENCE_DATE)

    assert day is not None
    assert day.ctl is None
    assert day.acwr is None
    assert day.days_of_history == 3


def test_confidence_outside_zero_to_one_is_rejected(session: Session) -> None:
    session.add(FitnessDay(date=REFERENCE_DATE, confidence=1.5))

    with pytest.raises(IntegrityError):
        session.commit()


def test_athlete_settings_is_a_singleton(session: Session) -> None:
    session.add(AthleteSettings(id=1, hr_max=188, hr_rest=48, lthr=168))
    session.commit()

    stored = session.get(AthleteSettings, 1)
    assert stored is not None
    assert stored.zone_model == ZoneModel.FRIEL_RUN_LTHR

    session.add(AthleteSettings(id=2))
    with pytest.raises(IntegrityError):
        session.commit()


def test_activity_import_is_idempotent_on_the_intervals_id(
    engine: Engine,
) -> None:
    factory = session_factory(engine)
    with factory() as session:
        session.add(make_activity("i12345"))
        session.commit()

    with factory() as session:
        stored = session.get(Activity, "i12345")
        assert stored is not None
        stored.distance_m = 4000.0
        session.commit()

    with factory() as session:
        count = session.scalar(select(func.count()).select_from(Activity))
        stored = session.get(Activity, "i12345")

    assert count == 1
    assert stored is not None
    assert stored.distance_m == 4000.0
