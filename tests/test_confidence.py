"""Confidence, windows and staleness.

These rules decide whether any number in the app is shown at all, so each
one is pinned against a hand-written case.
"""

from __future__ import annotations

import datetime as dt

import pytest

from tempo.metrics import confidence as conf
from tempo.metrics.thresholds import (
    BASELINE_RESET_GAP_DAYS,
    MIN_DAYS_FORM,
    MIN_NIGHTS_READINESS,
    STALE_AFTER_DAYS,
)

TODAY = dt.date(2026, 9, 8)


def block(start: dt.date, days: int) -> list[dt.date]:
    return [start + dt.timedelta(days=offset) for offset in range(days)]


# --- gaps and ages -----------------------------------------------------


def test_consecutive_days_have_no_gap() -> None:
    assert conf.gap_days(dt.date(2026, 9, 1), dt.date(2026, 9, 2)) == 0


def test_a_gap_counts_the_days_with_no_data() -> None:
    assert conf.gap_days(dt.date(2026, 9, 1), dt.date(2026, 9, 5)) == 3


def test_the_same_day_has_no_gap() -> None:
    assert conf.gap_days(TODAY, TODAY) == 0


def test_a_reversed_span_clamps_to_zero() -> None:
    assert conf.gap_days(dt.date(2026, 9, 5), dt.date(2026, 9, 1)) == 0


def test_age_is_zero_today_and_never_negative() -> None:
    assert conf.days_since(TODAY, TODAY) == 0
    assert conf.days_since(TODAY + dt.timedelta(days=3), TODAY) == 0
    assert conf.days_since(TODAY - dt.timedelta(days=3), TODAY) == 3


# --- staleness ---------------------------------------------------------


def test_data_within_the_staleness_horizon_is_not_stale() -> None:
    edge = TODAY - dt.timedelta(days=STALE_AFTER_DAYS)

    assert conf.is_stale(edge, TODAY) is False


def test_data_one_day_beyond_the_horizon_is_stale() -> None:
    beyond = TODAY - dt.timedelta(days=STALE_AFTER_DAYS + 1)

    assert conf.is_stale(beyond, TODAY) is True


def test_no_data_at_all_is_not_stale() -> None:
    """Nothing to be out of date — that is the empty state, not staleness."""
    assert conf.is_stale(None, TODAY) is False


# --- recency and fill --------------------------------------------------


def test_todays_data_is_fully_recent() -> None:
    assert conf.recency(TODAY, TODAY) == pytest.approx(1.0)


def test_recency_reaches_zero_at_the_baseline_reset_gap() -> None:
    old = TODAY - dt.timedelta(days=BASELINE_RESET_GAP_DAYS)

    assert conf.recency(old, TODAY) == 0.0


def test_recency_falls_off_linearly_in_between() -> None:
    halfway = TODAY - dt.timedelta(days=BASELINE_RESET_GAP_DAYS // 2)

    assert conf.recency(halfway, TODAY) == pytest.approx(0.5)


def test_recency_of_nothing_is_zero() -> None:
    assert conf.recency(None, TODAY) == 0.0


@pytest.mark.parametrize(
    ("have", "required", "expected"),
    [(0, 42, 0.0), (21, 42, 0.5), (42, 42, 1.0), (60, 42, 1.0), (-1, 42, 0.0)],
)
def test_window_fill(have: int, required: int, expected: float) -> None:
    assert conf.window_fill(have, required) == pytest.approx(expected)


def test_confidence_is_fill_times_recency() -> None:
    seven_days_ago = TODAY - dt.timedelta(days=7)

    result = conf.confidence_of(21, 42, seven_days_ago, TODAY)

    assert result == pytest.approx(0.5 * 0.5)


# --- available_from ----------------------------------------------------


def test_available_from_counts_the_missing_days_forward() -> None:
    assert conf.available_from(34, 42, TODAY) == TODAY + dt.timedelta(days=8)


def test_available_from_is_none_once_the_requirement_is_met() -> None:
    assert conf.available_from(42, 42, TODAY) is None
    assert conf.available_from(50, 42, TODAY) is None


# --- the current window ------------------------------------------------


def test_an_unbroken_run_up_to_today_is_the_window() -> None:
    window = conf.current_window(block(TODAY - dt.timedelta(days=9), 10), as_of=TODAY)

    assert len(window) == 10
    assert window[-1] == TODAY


def test_no_data_gives_an_empty_window() -> None:
    assert conf.current_window([], as_of=TODAY) == ()


def test_a_single_data_point_today_is_a_window_of_one() -> None:
    assert conf.current_window([TODAY], as_of=TODAY) == (TODAY,)


def _two_blocks(missing_between: int) -> tuple[list[dt.date], list[dt.date]]:
    """A recent block reaching today, and an older one a chosen gap behind."""
    recent = block(TODAY - dt.timedelta(days=4), 5)
    older_end = recent[0] - dt.timedelta(days=missing_between + 1)
    older = block(older_end - dt.timedelta(days=4), 5)
    return older, recent


def test_a_gap_at_the_limit_does_not_break_the_window() -> None:
    older, recent = _two_blocks(BASELINE_RESET_GAP_DAYS)

    window = conf.current_window([*older, *recent], as_of=TODAY)

    assert older[0] in window
    assert len(window) == 10


def test_one_day_more_of_gap_does_break_the_window() -> None:
    older, recent = _two_blocks(BASELINE_RESET_GAP_DAYS + 1)

    window = conf.current_window([*older, *recent], as_of=TODAY)

    assert window == tuple(recent)
    assert older[-1] not in window


def test_a_months_long_gap_breaks_the_window() -> None:
    older = block(dt.date(2026, 1, 1), 24)
    recent = block(TODAY - dt.timedelta(days=4), 5)

    window = conf.current_window([*older, *recent], as_of=TODAY)

    assert window == tuple(recent)


def test_a_window_whose_newest_day_is_long_past_is_empty() -> None:
    """A block from three months ago is history, not a current window."""
    stale_block = block(dt.date(2026, 5, 1), 16)

    assert conf.current_window(stale_block, as_of=TODAY) == ()


def test_only_the_most_recent_run_counts() -> None:
    first = block(dt.date(2025, 9, 3), 24)
    second = block(dt.date(2026, 1, 6), 25)
    third = block(TODAY - dt.timedelta(days=15), 16)

    window = conf.current_window([*first, *second, *third], as_of=TODAY)

    assert window == tuple(third)


def test_days_after_today_are_ignored() -> None:
    window = conf.current_window(
        [*block(TODAY - dt.timedelta(days=2), 3), TODAY + dt.timedelta(days=5)],
        as_of=TODAY,
    )

    assert window[-1] == TODAY
    assert len(window) == 3


def test_duplicate_days_count_once() -> None:
    assert len(conf.current_window([TODAY, TODAY, TODAY], as_of=TODAY)) == 1


# --- the tracked window ------------------------------------------------


def test_a_rest_week_with_wellness_data_does_not_break_the_window() -> None:
    """Load zero on a tracked day is a fact, not an absence."""
    wellness = block(TODAY - dt.timedelta(days=49), 50)
    activities = [TODAY - dt.timedelta(days=40)]

    window = conf.tracked_window(wellness, activities, as_of=TODAY)

    assert len(window) == 50


def test_an_untracked_stretch_does_break_the_window() -> None:
    wellness = block(dt.date(2026, 1, 6), 25)
    activities = [dt.date(2026, 1, 15)]

    window = conf.tracked_window(wellness, activities, as_of=TODAY)

    assert window == ()


def test_activities_alone_can_carry_the_window() -> None:
    activities = [TODAY - dt.timedelta(days=offset) for offset in (0, 3, 10, 20)]

    window = conf.tracked_window([], activities, as_of=TODAY)

    assert len(window) == 4


# --- evaluate ----------------------------------------------------------


def test_a_full_window_delivers_the_value() -> None:
    result = conf.evaluate(
        61, have=20, required=MIN_NIGHTS_READINESS, as_of=TODAY, last_data_point=TODAY
    )

    assert result.value == 61
    assert result.is_available is True
    assert result.confidence == pytest.approx(1.0)
    assert result.available_from is None
    assert result.stale is False
    assert result.missing == 0


def test_a_short_window_withholds_the_value_but_keeps_the_progress() -> None:
    result = conf.evaluate(
        61,
        have=3,
        required=MIN_NIGHTS_READINESS,
        as_of=TODAY,
        last_data_point=TODAY,
    )

    assert result.value is None
    assert result.confidence == 0.0
    assert result.have == 3
    assert result.required == MIN_NIGHTS_READINESS
    assert result.missing == 11
    assert result.available_from == TODAY + dt.timedelta(days=11)


def test_a_value_that_could_not_be_computed_has_no_confidence() -> None:
    """Enough history, but the formula could not produce a number."""
    result: conf.MetricResult[float] = conf.evaluate(
        None, have=50, required=MIN_DAYS_FORM, as_of=TODAY, last_data_point=TODAY
    )

    assert result.value is None
    assert result.confidence == 0.0
    assert result.have == 50


def test_an_old_data_point_lowers_confidence_and_marks_stale() -> None:
    ten_days_ago = TODAY - dt.timedelta(days=10)

    result = conf.evaluate(
        61,
        have=MIN_NIGHTS_READINESS,
        required=MIN_NIGHTS_READINESS,
        as_of=TODAY,
        last_data_point=ten_days_ago,
    )

    assert result.value == 61
    assert result.stale is True
    assert 0.0 < result.confidence < 0.5


# --- from_window -------------------------------------------------------


def test_from_window_reads_have_and_the_last_point_off_the_window() -> None:
    window = tuple(block(TODAY - dt.timedelta(days=13), 14))

    result = conf.from_window(
        61, window=window, required=MIN_NIGHTS_READINESS, as_of=TODAY
    )

    assert result.value == 61
    assert result.have == 14
    assert result.last_data_point == TODAY
    assert result.days_of_history == 14


def test_an_empty_window_still_reports_the_last_data_there_was() -> None:
    """The tile shows "Stand 03.06." rather than going blank."""
    three_months_ago = dt.date(2026, 6, 3)

    result = conf.from_window(
        61,
        window=(),
        required=MIN_NIGHTS_READINESS,
        as_of=TODAY,
        newest_data_point=three_months_ago,
    )

    assert result.value is None
    assert result.have == 0
    assert result.last_data_point == three_months_ago
    assert result.stale is True
    assert result.available_from == TODAY + dt.timedelta(days=MIN_NIGHTS_READINESS)


def test_unavailable_spells_out_the_progress() -> None:
    result = conf.unavailable(float, have=0, required=MIN_DAYS_FORM, as_of=TODAY)

    assert result.value is None
    assert result.required == MIN_DAYS_FORM
    assert result.confidence == 0.0
    assert result.stale is False


# --- window length per day ---------------------------------------------


def calendar(first: dt.date, last: dt.date) -> list[dt.date]:
    span = (last - first).days
    return [first + dt.timedelta(days=offset) for offset in range(span + 1)]


def test_the_window_grows_while_data_keeps_arriving() -> None:
    days = calendar(dt.date(2026, 9, 1), dt.date(2026, 9, 5))

    states = conf.window_lengths_by_day(days, days)

    assert [state.length for state in states] == [1, 2, 3, 4, 5]


def test_a_day_without_data_holds_the_window_rather_than_ending_it() -> None:
    """A rest day inside a tracked stretch is not a break."""
    days = calendar(dt.date(2026, 9, 1), dt.date(2026, 9, 5))
    tracked = [day for day in days if day != dt.date(2026, 9, 3)]

    states = conf.window_lengths_by_day(tracked, days)

    assert [state.length for state in states] == [1, 2, 2, 3, 4]


def test_the_window_drops_to_zero_once_the_gap_is_too_long() -> None:
    days = calendar(dt.date(2026, 9, 1), dt.date(2026, 10, 15))
    tracked = calendar(dt.date(2026, 9, 1), dt.date(2026, 9, 10))

    states = {state.date: state for state in conf.window_lengths_by_day(tracked, days)}

    assert states[dt.date(2026, 9, 10)].length == 10
    # Fourteen days with no data between the last reading and this one: the
    # window is at the limit and still open.
    assert states[dt.date(2026, 9, 25)].length == 10
    # Fifteen, and it is over.
    assert states[dt.date(2026, 9, 26)].length == 0


def test_data_after_a_long_gap_starts_a_new_window() -> None:
    days = calendar(dt.date(2026, 9, 1), dt.date(2026, 10, 15))
    tracked = calendar(dt.date(2026, 9, 1), dt.date(2026, 9, 5)) + calendar(
        dt.date(2026, 10, 1), dt.date(2026, 10, 3)
    )

    states = {state.date: state for state in conf.window_lengths_by_day(tracked, days)}

    assert states[dt.date(2026, 9, 5)].length == 5
    assert states[dt.date(2026, 10, 1)].length == 1
    assert states[dt.date(2026, 10, 3)].length == 3


def test_each_day_carries_the_last_data_point_as_of_then() -> None:
    days = calendar(dt.date(2026, 9, 1), dt.date(2026, 9, 5))
    tracked = [dt.date(2026, 9, 1), dt.date(2026, 9, 2)]

    states = {state.date: state for state in conf.window_lengths_by_day(tracked, days)}

    assert states[dt.date(2026, 9, 5)].last_data_point == dt.date(2026, 9, 2)
    assert states[dt.date(2026, 9, 1)].last_data_point == dt.date(2026, 9, 1)


def test_a_calendar_with_no_tracked_days_never_opens_a_window() -> None:
    days = calendar(dt.date(2026, 9, 1), dt.date(2026, 9, 5))

    states = conf.window_lengths_by_day([], days)

    assert all(state.length == 0 for state in states)
    assert all(state.last_data_point is None for state in states)
