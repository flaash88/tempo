"""The scenarios the plan makes mandatory, end to end through the database.

Each one is a state the app will actually be in — most of them more often
than the state where everything is available. They go through recompute and
the read model rather than through the pure functions, because the point is
what the athlete would be shown.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from tempo.db.models import (
    Activity,
    ActivityStream,
    AthleteSettings,
    DailyLoad,
    FitnessDay,
    WellnessDay,
)
from tempo.db.session import session_factory
from tempo.metrics.thresholds import (
    BASELINE_RESET_GAP_DAYS,
    DEFAULT_READINESS_WEIGHTS,
    DEFAULT_SLEEP_TARGET_S,
    MIN_DAYS_FORM,
    MIN_DAYS_HRV_BASELINE,
    MIN_NIGHTS_READINESS,
    MIN_PERFORMANCES_CRITICAL_SPEED,
    STALE_AFTER_DAYS,
    ReadinessWeights,
)
from tempo.recompute import recompute_all
from tempo.snapshot import build_snapshot

TODAY = dt.date(2026, 9, 8)
HR_REST = 50
HR_MAX = 190
LTHR = 170
THRESHOLD_PACE = 300.0


@pytest.fixture
def open_session(engine: Engine) -> Callable[[], Session]:
    return session_factory(engine)


def add_settings(session: Session, *, complete: bool = True) -> None:
    session.add(
        AthleteSettings(
            id=1,
            hr_max=HR_MAX if complete else None,
            hr_rest=HR_REST if complete else None,
            lthr=LTHR if complete else None,
            threshold_pace_s_per_km=THRESHOLD_PACE if complete else None,
        )
    )


def add_wellness_block(
    session: Session,
    start: dt.date,
    days: int,
    *,
    hrv: float | None = 45.0,
    resting_hr: int | None = 52,
    sleep_secs: int | None = 25_200,
) -> None:
    for offset in range(days):
        session.add(
            WellnessDay(
                date=start + dt.timedelta(days=offset),
                resting_hr=None if resting_hr is None else resting_hr + (offset % 3),
                hrv=None if hrv is None else hrv + (offset % 5),
                hrv_source_field=None if hrv is None else "hrv",
                sleep_secs=sleep_secs,
                sleep_score=72,
                source="intervals",
            )
        )


def add_run(
    session: Session,
    activity_id: str,
    day: dt.date,
    *,
    seconds: int = 1800,
    speed: float = 3.0,
    hr: int | None = 150,
    with_gps: bool = True,
    with_stream: bool = True,
) -> None:
    session.add(
        Activity(
            id=activity_id,
            start_local=dt.datetime.combine(day, dt.time(7, 30)),
            sport="Run",
            distance_m=speed * seconds,
            moving_s=seconds,
            elapsed_s=seconds,
            avg_hr=hr,
            max_hr=None if hr is None else hr + 15,
            avg_pace_s_per_km=1000.0 / speed,
            source="intervals",
        )
    )
    if not with_stream:
        return
    for offset in range(seconds):
        session.add(
            ActivityStream(
                activity_id=activity_id,
                offset_s=offset,
                hr=hr,
                speed_m_s=speed,
                altitude_m=200.0,
                cadence=82,
                lat=47.07 if with_gps else None,
                lon=15.44 if with_gps else None,
            )
        )


# --- the real data situation -------------------------------------------


def test_the_real_data_situation(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    """Three wellness blocks months apart, the newest three months old.

    This is the athlete's actual history, and the app's most common state.
    Everything has to come back as "not yet, and here is how far along" —
    never as a number carried across a gap.
    """
    with open_session() as session:
        add_settings(session)
        add_wellness_block(session, dt.date(2025, 9, 3), 24)
        add_wellness_block(session, dt.date(2026, 1, 6), 25)
        add_wellness_block(session, dt.date(2026, 5, 1), 16)
        add_run(session, "i1", dt.date(2026, 1, 15))
        session.commit()

    recompute_all(engine, today=TODAY)
    snapshot = build_snapshot(engine, as_of=TODAY)

    last_wellness = dt.date(2026, 5, 16)

    # Readiness: no value, but the progress is spelled out.
    assert snapshot.readiness.value is None
    assert snapshot.readiness.have == 0
    assert snapshot.readiness.required == MIN_NIGHTS_READINESS
    assert snapshot.readiness.available_from == TODAY + dt.timedelta(
        days=MIN_NIGHTS_READINESS
    )
    assert snapshot.readiness.confidence == 0.0

    # The form curve: no value either, despite months of materialised days.
    assert snapshot.form.value is None
    assert snapshot.form.have == 0
    assert snapshot.form.required == MIN_DAYS_FORM
    assert snapshot.acwr.value is None

    # The HRV baseline: discarded, not averaged over the gaps.
    assert snapshot.hrv.value is None
    assert snapshot.hrv.have == 0
    assert snapshot.hrv.required == MIN_DAYS_HRV_BASELINE
    assert snapshot.resting_hr.value is None

    # Everything is marked stale, and dated so the tile can say when.
    for result in (
        snapshot.readiness,
        snapshot.hrv,
        snapshot.resting_hr,
        snapshot.form,
        snapshot.acwr,
        snapshot.critical_speed,
    ):
        assert result.stale is True, result
        assert result.last_data_point is not None
    assert snapshot.readiness.last_data_point == last_wellness
    assert snapshot.form.last_data_point == last_wellness

    # Critical speed is the one thing still on offer, and only because a
    # best effort is a record rather than a current state. It arrives
    # flagged stale and dated to the January run, never as "today".
    assert snapshot.critical_speed.value is not None
    assert snapshot.critical_speed.last_data_point == dt.date(2026, 1, 15)


def test_no_value_is_averaged_across_a_gap(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    """The rows in between exist and are zero; none of them carries a curve."""
    with open_session() as session:
        add_settings(session)
        add_wellness_block(session, dt.date(2025, 9, 3), 24)
        add_wellness_block(session, dt.date(2026, 1, 6), 25)
        session.commit()

    recompute_all(engine, today=TODAY)

    with open_session() as session:
        rows = {row.date: row for row in session.scalars(select(FitnessDay))}
        loads = {row.date: row for row in session.scalars(select(DailyLoad))}

    # The calendar is complete from the first reading to today.
    assert len(loads) == (TODAY - dt.date(2025, 9, 3)).days + 1
    # No day anywhere in it carries a fitness value: the window never
    # reached the minimum before each block ended.
    assert all(row.ctl is None for row in rows.values())
    assert all(row.days_of_history <= 25 for row in rows.values())


# --- cold start and emptiness ------------------------------------------


def test_an_empty_database_produces_no_metrics(engine: Engine) -> None:
    report = recompute_all(engine, today=TODAY)
    snapshot = build_snapshot(engine, as_of=TODAY)

    assert report.days == 0
    assert snapshot.any_value is False
    assert snapshot.readiness.have == 0
    assert snapshot.readiness.last_data_point is None
    # Nothing to be out of date: this is the empty state, not the stale one.
    assert snapshot.readiness.stale is False
    assert snapshot.form.stale is False


def test_a_cold_start_reports_progress_from_the_first_day(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        add_settings(session)
        add_wellness_block(session, TODAY - dt.timedelta(days=2), 3)
        session.commit()

    recompute_all(engine, today=TODAY)
    snapshot = build_snapshot(engine, as_of=TODAY)

    assert snapshot.readiness.value is None
    assert snapshot.readiness.have == 3
    assert snapshot.readiness.required == MIN_NIGHTS_READINESS
    assert snapshot.readiness.available_from == TODAY + dt.timedelta(
        days=MIN_NIGHTS_READINESS - 3
    )
    assert snapshot.readiness.stale is False
    assert snapshot.form.have == 3


def test_a_single_data_point_is_a_window_of_one(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        add_settings(session)
        add_wellness_block(session, TODAY, 1)
        session.commit()

    recompute_all(engine, today=TODAY)
    snapshot = build_snapshot(engine, as_of=TODAY)

    assert snapshot.readiness.have == 1
    assert snapshot.readiness.value is None
    assert snapshot.hrv.have == 1
    assert snapshot.hrv.value is None
    assert snapshot.readiness.last_data_point == TODAY


# --- gaps and the baseline reset ---------------------------------------


def test_a_gap_of_over_fourteen_days_resets_the_baseline(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    """A long block, a gap past the limit, then a short block."""
    recent_start = TODAY - dt.timedelta(days=4)
    older_end = recent_start - dt.timedelta(days=BASELINE_RESET_GAP_DAYS + 2)

    with open_session() as session:
        add_settings(session)
        add_wellness_block(session, older_end - dt.timedelta(days=39), 40)
        add_wellness_block(session, recent_start, 5)
        session.commit()

    snapshot = build_snapshot(engine, as_of=TODAY)

    # Forty days of readings exist, but not in the current window.
    assert snapshot.hrv.value is None
    assert snapshot.hrv.have == 5
    assert snapshot.readiness.have == 5


def test_a_gap_within_the_limit_keeps_the_baseline(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    recent_start = TODAY - dt.timedelta(days=4)
    older_end = recent_start - dt.timedelta(days=BASELINE_RESET_GAP_DAYS + 1)

    with open_session() as session:
        add_settings(session)
        add_wellness_block(session, older_end - dt.timedelta(days=29), 30)
        add_wellness_block(session, recent_start, 5)
        session.commit()

    snapshot = build_snapshot(engine, as_of=TODAY)

    assert snapshot.hrv.value is not None
    assert snapshot.hrv.have == 35
    assert snapshot.hrv.value.days == 35


def test_a_rest_week_with_wellness_data_keeps_the_form_curve(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    """Load zero on a tracked day is a fact, not an absence."""
    start = TODAY - dt.timedelta(days=MIN_DAYS_FORM + 9)

    with open_session() as session:
        add_settings(session)
        add_wellness_block(session, start, MIN_DAYS_FORM + 10)
        for offset in range(0, MIN_DAYS_FORM, 3):
            add_run(
                session,
                f"i{offset}",
                start + dt.timedelta(days=offset),
                seconds=1800,
            )
        session.commit()

    recompute_all(engine, today=TODAY)
    snapshot = build_snapshot(engine, as_of=TODAY)

    assert snapshot.form.value is not None
    assert snapshot.form.have >= MIN_DAYS_FORM
    assert snapshot.form.value.ctl > 0
    assert snapshot.acwr.value is not None


# --- sessions with something missing -----------------------------------


def test_a_run_without_heart_rate_still_gets_a_load_from_pace(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        add_settings(session)
        add_run(session, "no-hr", TODAY, hr=None)
        session.commit()

    recompute_all(engine, today=TODAY)

    with open_session() as session:
        row = session.get(DailyLoad, TODAY)

    assert row is not None
    assert row.trimp is None
    assert row.hr_tss is None
    assert row.r_tss is not None
    assert row.duration_s == 1800


def test_a_run_without_gps_is_scored_from_its_speed_stream(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    """No coordinates is a normal treadmill recording, not a missing session."""
    with open_session() as session:
        add_settings(session)
        add_run(session, "no-gps", TODAY, with_gps=False)
        session.commit()

    recompute_all(engine, today=TODAY)

    with open_session() as session:
        row = session.get(DailyLoad, TODAY)

    assert row is not None
    assert row.r_tss is not None
    assert row.trimp is not None


def test_a_run_with_neither_heart_rate_nor_a_configured_threshold(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    """Duration and distance, and honestly nothing else."""
    with open_session() as session:
        add_settings(session, complete=False)
        add_run(session, "bare", TODAY, hr=None)
        session.commit()

    report = recompute_all(engine, today=TODAY)

    with open_session() as session:
        row = session.get(DailyLoad, TODAY)

    assert row is not None
    assert row.duration_s == 1800
    assert row.distance_m == pytest.approx(5400.0)
    assert row.trimp is None
    assert row.hr_tss is None
    assert row.r_tss is None
    assert report.activities_with_load == 0
    assert any("threshold pace" in note for note in report.notes)


def test_a_session_without_a_stream_still_counts_as_a_day_with_a_session(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        add_settings(session)
        add_run(session, "summary-only", TODAY, with_stream=False)
        session.commit()

    report = recompute_all(engine, today=TODAY)

    with open_session() as session:
        row = session.get(DailyLoad, TODAY)

    assert report.days_with_session == 1
    assert row is not None
    # TRIMP comes from the summary, rTSS needs a stream and has none.
    assert row.trimp is not None
    assert row.r_tss is None


# --- critical speed ----------------------------------------------------


def test_critical_speed_needs_three_qualifying_efforts(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        add_settings(session)
        add_run(session, "short", TODAY, seconds=600, speed=3.0)
        session.commit()

    snapshot = build_snapshot(engine, as_of=TODAY)

    assert snapshot.critical_speed.required == MIN_PERFORMANCES_CRITICAL_SPEED
    assert snapshot.critical_speed.have < MIN_PERFORMANCES_CRITICAL_SPEED
    assert snapshot.critical_speed.value is None


def test_critical_speed_appears_once_the_efforts_are_there(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        add_settings(session)
        # One long run yields best efforts at 5, 10, 20 and 30 minutes.
        add_run(session, "long", TODAY, seconds=1800, speed=3.0)
        session.commit()

    snapshot = build_snapshot(engine, as_of=TODAY)

    assert snapshot.critical_speed.have >= MIN_PERFORMANCES_CRITICAL_SPEED
    assert snapshot.critical_speed.value is not None
    assert snapshot.critical_speed.value.cs_m_s == pytest.approx(3.0, abs=0.01)


# --- idempotency and back-filling --------------------------------------


def test_recompute_is_idempotent(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        add_settings(session)
        add_wellness_block(session, TODAY - dt.timedelta(days=49), 50)
        add_run(session, "i1", TODAY - dt.timedelta(days=10))
        session.commit()

    first = recompute_all(engine, today=TODAY)
    with open_session() as session:
        before = {
            row.date: (row.ctl, row.atl, row.tsb, row.days_of_history)
            for row in session.scalars(select(FitnessDay))
        }

    second = recompute_all(engine, today=TODAY)
    with open_session() as session:
        after = {
            row.date: (row.ctl, row.atl, row.tsb, row.days_of_history)
            for row in session.scalars(select(FitnessDay))
        }

    assert first.summary() == second.summary()
    assert before == after


def test_the_load_columns_are_back_filled_without_a_re_import(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    """A session imported before the settings existed is scored later."""
    with open_session() as session:
        add_run(session, "i1", TODAY)
        session.commit()

    recompute_all(engine, today=TODAY)
    with open_session() as session:
        row = session.get(DailyLoad, TODAY)
        assert row is not None
        assert row.trimp is None

    with open_session() as session:
        add_settings(session)
        session.commit()

    recompute_all(engine, today=TODAY)

    with open_session() as session:
        row = session.get(DailyLoad, TODAY)

    assert row is not None
    assert row.trimp is not None
    assert row.r_tss is not None


def test_deleting_the_only_session_returns_its_day_to_load_zero(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        add_settings(session)
        add_wellness_block(session, TODAY, 1)
        add_run(session, "i1", TODAY)
        session.commit()
    recompute_all(engine, today=TODAY)

    with open_session() as session:
        activity = session.get(Activity, "i1")
        assert activity is not None
        session.delete(activity)
        session.commit()

    recompute_all(engine, today=TODAY)

    with open_session() as session:
        row = session.get(DailyLoad, TODAY)

    assert row is not None
    assert row.duration_s == 0
    assert row.trimp == 0.0


# --- staleness --------------------------------------------------------


def test_a_value_just_inside_the_staleness_horizon_is_current(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    end = TODAY - dt.timedelta(days=STALE_AFTER_DAYS)

    with open_session() as session:
        add_settings(session)
        add_wellness_block(session, end - dt.timedelta(days=29), 30)
        session.commit()

    snapshot = build_snapshot(engine, as_of=TODAY)

    assert snapshot.hrv.stale is False
    assert snapshot.hrv.value is not None
    assert 0.0 < snapshot.hrv.confidence < 1.0


def test_a_day_past_the_horizon_is_stale_but_still_delivered(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    """Stale is a label on a real value, not a reason to withhold it."""
    end = TODAY - dt.timedelta(days=STALE_AFTER_DAYS + 1)

    with open_session() as session:
        add_settings(session)
        add_wellness_block(session, end - dt.timedelta(days=29), 30)
        session.commit()

    snapshot = build_snapshot(engine, as_of=TODAY)

    assert snapshot.hrv.stale is True
    assert snapshot.hrv.value is not None
    assert snapshot.hrv.last_data_point == end


# --- readiness in practice --------------------------------------------


def test_readiness_arrives_with_fourteen_nights(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        add_settings(session)
        add_wellness_block(
            session,
            TODAY - dt.timedelta(days=MIN_NIGHTS_READINESS - 1),
            MIN_NIGHTS_READINESS,
        )
        session.commit()

    recompute_all(engine, today=TODAY)
    snapshot = build_snapshot(engine, as_of=TODAY)

    assert snapshot.readiness.value is not None
    assert 0 <= snapshot.readiness.value <= 100
    assert snapshot.readiness.confidence == pytest.approx(1.0)

    # The published HRV baseline needs twenty-one days and is not in yet,
    # while readiness compares today against its own fourteen night window.
    # Two products of the same arithmetic, two minimums — without the split
    # the readiness minimum could never be reached at all.
    assert snapshot.hrv.value is None
    assert snapshot.hrv.have == MIN_NIGHTS_READINESS
    assert snapshot.readiness_components.hrv is not None
    assert snapshot.readiness_components.present >= 2

    # The form curve needs forty-two days, so it contributes nothing here.
    assert snapshot.readiness_components.tsb is None


def test_readiness_needs_more_than_one_input(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    """Sleep alone would say more about what is missing than about the athlete."""
    with open_session() as session:
        add_settings(session)
        add_wellness_block(
            session,
            TODAY - dt.timedelta(days=MIN_NIGHTS_READINESS - 1),
            MIN_NIGHTS_READINESS,
            hrv=None,
            resting_hr=None,
        )
        session.commit()

    recompute_all(engine, today=TODAY)
    snapshot = build_snapshot(engine, as_of=TODAY)

    assert snapshot.readiness_components.present == 1
    assert snapshot.readiness.value is None
    assert snapshot.readiness.have == MIN_NIGHTS_READINESS


def test_the_hrv_source_field_is_carried_into_the_snapshot(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    """The read model reports which field it was, and interprets nothing."""
    with open_session() as session:
        add_settings(session)
        add_wellness_block(session, TODAY - dt.timedelta(days=29), 30)
        session.commit()

    snapshot = build_snapshot(engine, as_of=TODAY)

    assert snapshot.hrv_source_field == "hrv"
    assert snapshot.hrv.value is not None
    assert snapshot.hrv.value.source_field == "hrv"


# --- configurable weights and the athlete's own sleep target ------------


def test_the_configured_weights_reach_the_score(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    with open_session() as session:
        add_settings(session)
        add_wellness_block(
            session,
            TODAY - dt.timedelta(days=MIN_NIGHTS_READINESS - 1),
            MIN_NIGHTS_READINESS,
        )
        session.commit()

    recompute_all(engine, today=TODAY)
    default = build_snapshot(engine, as_of=TODAY)
    sleep_heavy = build_snapshot(
        engine,
        as_of=TODAY,
        weights=ReadinessWeights(hrv=0.05, resting_hr=0.05, sleep=0.85, tsb=0.05),
    )

    assert default.readiness.value is not None
    assert sleep_heavy.readiness.value is not None
    assert default.readiness.value != sleep_heavy.readiness.value
    assert default.readiness_weights == DEFAULT_READINESS_WEIGHTS
    assert sleep_heavy.readiness_weights.sleep == pytest.approx(0.85)


def test_the_athletes_own_sleep_target_is_used(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    """Seven hours is a full night for someone who targets seven."""
    seven_hours = 7 * 3600

    with open_session() as session:
        add_settings(session)
        add_wellness_block(
            session,
            TODAY - dt.timedelta(days=MIN_NIGHTS_READINESS - 1),
            MIN_NIGHTS_READINESS,
            sleep_secs=seven_hours,
        )
        session.commit()

    against_default = build_snapshot(engine, as_of=TODAY)

    with open_session() as session:
        athlete = session.get(AthleteSettings, 1)
        assert athlete is not None
        athlete.sleep_target_s = seven_hours
        session.commit()

    against_own = build_snapshot(engine, as_of=TODAY)

    assert against_default.sleep_target_s == DEFAULT_SLEEP_TARGET_S
    assert against_own.sleep_target_s == seven_hours
    assert against_default.readiness_components.sleep == pytest.approx(87.5)
    assert against_own.readiness_components.sleep == pytest.approx(100.0)


def test_the_form_term_contributes_nothing_during_the_build_up(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    """Readiness rests on HRV, resting heart rate and sleep for six weeks.

    The form term needs forty-two days of tracked history, so for the whole
    of the build-up its weight is renormalised away. Worth a test rather
    than a note: it means the weight given to form only starts mattering
    once the athlete has been wearing the watch for six weeks.
    """
    with open_session() as session:
        add_settings(session)
        add_wellness_block(
            session,
            TODAY - dt.timedelta(days=MIN_NIGHTS_READINESS - 1),
            MIN_NIGHTS_READINESS,
        )
        session.commit()

    recompute_all(engine, today=TODAY)

    form_heavy = build_snapshot(
        engine,
        as_of=TODAY,
        weights=ReadinessWeights(hrv=0.1, resting_hr=0.1, sleep=0.1, tsb=0.7),
    )
    form_ignored = build_snapshot(
        engine,
        as_of=TODAY,
        weights=ReadinessWeights(hrv=0.1, resting_hr=0.1, sleep=0.1, tsb=0.0),
    )

    assert form_heavy.readiness_components.tsb is None
    assert form_heavy.readiness.value == form_ignored.readiness.value


def test_the_subjective_fields_are_stored_and_read_by_nothing_yet(
    engine: Engine, open_session: Callable[[], Session]
) -> None:
    """On the record from now on, so the weights can be calibrated later."""
    with open_session() as session:
        add_settings(session)
        add_wellness_block(
            session,
            TODAY - dt.timedelta(days=MIN_NIGHTS_READINESS - 1),
            MIN_NIGHTS_READINESS,
        )
        day = session.get(WellnessDay, TODAY)
        assert day is not None
        day.fatigue = 2
        day.soreness = 3
        day.mood = 1
        session.commit()

    without = build_snapshot(engine, as_of=TODAY)

    with open_session() as session:
        stored = session.get(WellnessDay, TODAY)
        assert stored is not None
        assert (stored.fatigue, stored.soreness, stored.mood) == (2, 3, 1)

    # Nothing in the score depends on them yet, by design.
    assert without.readiness_components.present == 3
