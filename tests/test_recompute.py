"""The daily load calendar: no day skipped, no gap averaged over."""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

import pytest
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session

from tempo.db.models import Activity, DailyLoad
from tempo.db.session import session_factory
from tempo.recompute import recompute_all
from tests.fixtures.synthetic import make_activity, make_wellness_block

TODAY = dt.date(2026, 9, 8)


@pytest.fixture
def open_session(engine: Engine) -> Callable[[], Session]:
    return session_factory(engine)


def _at(day: dt.date, hour: int = 7) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(hour, 30))


def test_an_empty_database_produces_nothing(engine: Engine) -> None:
    report = recompute_all(engine, today=TODAY)

    assert report.days == 0
    assert report.first_day is None
    assert report.summary() == "no data to recompute"


def test_wellness_alone_is_enough_to_anchor_the_calendar(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        session.add_all(make_wellness_block(dt.date(2026, 9, 1), 3))
        session.commit()

    report = recompute_all(engine, today=TODAY)

    assert report.first_day == dt.date(2026, 9, 1)
    assert report.days == 8
    assert report.days_with_session == 0
    with open_session() as session:
        assert session.scalar(select(func.count()).select_from(DailyLoad)) == 8


def test_the_calendar_spans_a_gap_of_months_without_skipping_a_day(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    """The real data shape: one run in January, wellness blocks, then nothing."""
    with open_session() as session:
        session.add(make_activity("i1", start_local=_at(dt.date(2026, 1, 15))))
        session.add_all(make_wellness_block(dt.date(2026, 5, 1), 16))
        session.commit()

    report = recompute_all(engine, today=TODAY)

    assert report.first_day == dt.date(2026, 1, 15)
    assert report.last_day == TODAY
    assert report.days == (TODAY - dt.date(2026, 1, 15)).days + 1

    with open_session() as session:
        rows = {row.date: row for row in session.scalars(select(DailyLoad))}

    assert len(rows) == report.days
    # Every single day between the two blocks exists, at load zero.
    day = dt.date(2026, 1, 16)
    while day < dt.date(2026, 5, 1):
        assert rows[day].duration_s == 0
        assert rows[day].trimp == 0.0
        day += dt.timedelta(days=1)


def test_days_without_a_session_carry_load_zero_not_null(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    """This is what lets CTL and ATL decay instead of freezing."""
    with open_session() as session:
        session.add(make_activity("i1", start_local=_at(dt.date(2026, 9, 1))))
        session.commit()

    recompute_all(engine, today=TODAY)

    with open_session() as session:
        empty = session.get(DailyLoad, dt.date(2026, 9, 4))

    assert empty is not None
    assert empty.duration_s == 0
    assert empty.distance_m == 0.0
    assert empty.trimp == 0.0
    assert empty.hr_tss == 0.0
    assert empty.r_tss == 0.0


def test_a_day_with_a_session_leaves_the_load_metrics_uncomputed(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    """Phase 3 owns the formulas; until then None says "not yet"."""
    with open_session() as session:
        session.add(make_activity("i1", start_local=_at(dt.date(2026, 9, 1))))
        session.commit()

    recompute_all(engine, today=TODAY)

    with open_session() as session:
        row = session.get(DailyLoad, dt.date(2026, 9, 1))

    assert row is not None
    assert row.duration_s == 1260
    assert row.trimp is None
    assert row.hr_tss is None
    assert row.r_tss is None


def test_two_sessions_on_one_day_are_summed(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        session.add(make_activity("i1", start_local=_at(dt.date(2026, 9, 1), hour=7)))
        session.add(make_activity("i2", start_local=_at(dt.date(2026, 9, 1), hour=18)))
        session.commit()

    recompute_all(engine, today=TODAY)

    with open_session() as session:
        row = session.get(DailyLoad, dt.date(2026, 9, 1))

    assert row is not None
    assert row.duration_s == 2520
    assert row.distance_m == pytest.approx(7000.0)


def test_a_session_with_unknown_duration_still_marks_the_day(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    """An activity without a duration contributes nothing but is not ignored."""
    with open_session() as session:
        session.add(
            Activity(
                id="i-bare",
                start_local=_at(dt.date(2026, 9, 2)),
                sport="Run",
                source="intervals",
            )
        )
        session.commit()

    report = recompute_all(engine, today=TODAY)

    assert report.days_with_session == 1
    with open_session() as session:
        row = session.get(DailyLoad, dt.date(2026, 9, 2))

    assert row is not None
    assert row.duration_s == 0
    assert row.distance_m == 0.0
    assert row.trimp is None


def test_recompute_is_idempotent(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        session.add(make_activity("i1", start_local=_at(dt.date(2026, 9, 1))))
        session.commit()

    first = recompute_all(engine, today=TODAY)
    second = recompute_all(engine, today=TODAY)

    assert first.days == second.days
    with open_session() as session:
        assert session.scalar(select(func.count()).select_from(DailyLoad)) == first.days


def test_a_day_that_lost_its_session_falls_back_to_load_zero(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    """Deleting an activity must not leave its day looking like a session."""
    with open_session() as session:
        session.add(make_activity("i1", start_local=_at(dt.date(2026, 9, 1))))
        session.commit()
    recompute_all(engine, today=TODAY)

    with open_session() as session:
        activity = session.get(Activity, "i1")
        assert activity is not None
        session.delete(activity)
        session.commit()
        session.add_all(make_wellness_block(dt.date(2026, 9, 1), 1))
        session.commit()

    recompute_all(engine, today=TODAY)

    with open_session() as session:
        row = session.get(DailyLoad, dt.date(2026, 9, 1))

    assert row is not None
    assert row.duration_s == 0
    assert row.trimp == 0.0


def test_an_activity_dated_after_today_still_gets_a_calendar(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    """A clock skew on the watch must not produce an empty calendar."""
    with open_session() as session:
        session.add(make_activity("i1", start_local=_at(dt.date(2026, 9, 1))))
        session.commit()

    report = recompute_all(engine, today=dt.date(2026, 8, 1))

    assert report.first_day == dt.date(2026, 9, 1)
    assert report.last_day == dt.date(2026, 9, 1)
    assert report.days == 1
