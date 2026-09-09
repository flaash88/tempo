"""The fitness series and the ratios built on it."""

from __future__ import annotations

import datetime as dt
from collections.abc import Sequence
from statistics import pstdev

import pytest

from tempo.metrics.fitness import acwr, fitness_series, monotony, strain
from tempo.metrics.thresholds import (
    ACWR_CHRONIC_DAYS,
    ATL_TIME_CONSTANT_DAYS,
    CTL_TIME_CONSTANT_DAYS,
    MONOTONY_WINDOW_DAYS,
)

START = dt.date(2026, 1, 1)


def days(loads: Sequence[float | None]) -> list[tuple[dt.date, float | None]]:
    return [
        (START + dt.timedelta(days=offset), load) for offset, load in enumerate(loads)
    ]


# --- the series --------------------------------------------------------


def test_an_empty_series_has_no_points() -> None:
    assert fitness_series([]) == ()


def test_the_first_day_steps_from_the_seed() -> None:
    points = fitness_series(days([100.0]))

    assert len(points) == 1
    assert points[0].ctl == pytest.approx(100.0 / CTL_TIME_CONSTANT_DAYS)
    assert points[0].atl == pytest.approx(100.0 / ATL_TIME_CONSTANT_DAYS)
    # Form is yesterday's balance, and yesterday was nothing.
    assert points[0].tsb == pytest.approx(0.0)


def test_the_second_day_matches_a_hand_evaluation() -> None:
    points = fitness_series(days([100.0, 100.0]))

    ctl1 = 100.0 / CTL_TIME_CONSTANT_DAYS
    atl1 = 100.0 / ATL_TIME_CONSTANT_DAYS
    ctl2 = ctl1 + (100.0 - ctl1) / CTL_TIME_CONSTANT_DAYS
    atl2 = atl1 + (100.0 - atl1) / ATL_TIME_CONSTANT_DAYS

    assert points[1].ctl == pytest.approx(ctl2)
    assert points[1].atl == pytest.approx(atl2)
    assert points[1].tsb == pytest.approx(ctl1 - atl1)


def test_form_is_yesterdays_fitness_minus_yesterdays_fatigue() -> None:
    """Today's session is not absorbed by the time the athlete decides."""
    points = fitness_series(days([0.0, 0.0, 400.0, 0.0]))

    assert points[2].tsb == pytest.approx(points[1].ctl - points[1].atl)
    assert points[3].tsb == pytest.approx(points[2].ctl - points[2].atl)


def test_fatigue_rises_faster_than_fitness_and_falls_faster_too() -> None:
    building = fitness_series(days([100.0] * 20))
    assert building[-1].atl > building[-1].ctl

    resting = fitness_series(days([100.0] * 20 + [0.0] * 20))
    assert resting[-1].atl < resting[-1].ctl


def test_a_rest_day_decays_the_curves_instead_of_freezing_them() -> None:
    """Days without a session count as load zero. That is the point."""
    trained = fitness_series(days([100.0] * 10))
    then_rested = fitness_series(days([100.0] * 10 + [0.0] * 10))

    assert then_rested[-1].ctl < trained[-1].ctl
    assert then_rested[-1].atl < trained[-1].atl


def test_form_turns_positive_after_a_taper() -> None:
    points = fitness_series(days([100.0] * 42 + [0.0] * 10))

    assert points[-1].tsb > 0.0


def test_a_long_steady_block_converges_on_the_daily_load() -> None:
    points = fitness_series(days([100.0] * 400))

    assert points[-1].ctl == pytest.approx(100.0, abs=0.5)
    assert points[-1].atl == pytest.approx(100.0, abs=0.5)


def test_an_unknown_load_advances_the_series_rather_than_stopping_it() -> None:
    points = fitness_series(days([100.0, None, 100.0]))

    assert len(points) == 3
    assert points[1].load == pytest.approx(0.0)


def test_the_dates_come_back_untouched() -> None:
    points = fitness_series(days([1.0, 2.0, 3.0]))

    assert [point.date for point in points] == [
        START,
        START + dt.timedelta(days=1),
        START + dt.timedelta(days=2),
    ]


# --- ACWR -------------------------------------------------------------


def test_a_steady_block_has_an_acwr_of_one() -> None:
    assert acwr([50.0] * ACWR_CHRONIC_DAYS) == pytest.approx(1.0)


def test_a_hard_week_on_top_of_an_easy_block_raises_the_acwr() -> None:
    loads = [20.0] * 21 + [80.0] * 7

    result = acwr(loads)

    assert result is not None
    assert result > 1.0
    # Acute 560, chronic (420 + 560) / 4 = 245.
    assert result == pytest.approx(560 / 245)


def test_a_short_history_has_no_acwr() -> None:
    """A denominator over a shorter span than it claims is worse than none."""
    assert acwr([50.0] * (ACWR_CHRONIC_DAYS - 1)) is None


def test_a_chronic_load_of_nothing_leaves_the_acwr_undefined() -> None:
    assert acwr([0.0] * ACWR_CHRONIC_DAYS) is None


def test_acwr_reads_only_the_trailing_window() -> None:
    padded = [999.0] * 60 + [50.0] * ACWR_CHRONIC_DAYS

    assert acwr(padded) == pytest.approx(1.0)


def test_unknown_days_count_as_zero_in_the_acwr() -> None:
    loads: list[float | None] = [50.0] * 21 + [None] * 7

    result = acwr(loads)

    assert result is not None
    assert result == pytest.approx(0.0)


# --- monotony and strain ----------------------------------------------


def test_monotony_is_the_mean_over_the_spread() -> None:
    week: list[float | None] = [100.0, 50.0, 100.0, 50.0, 100.0, 50.0, 100.0]
    # Mean 550/7 = 78.5714; population variance
    # (4 * 21.4286^2 + 3 * 28.5714^2) / 7 = 612.24, so the spread is 24.744.
    expected = (550 / 7) / pstdev([100.0, 50.0, 100.0, 50.0, 100.0, 50.0, 100.0])

    result = monotony(week)

    assert result is not None
    assert result == pytest.approx(expected)
    assert result == pytest.approx(3.175426, abs=1e-6)


def test_an_identical_week_has_no_monotony() -> None:
    """The ratio would be infinite, and infinity is not an insight."""
    assert monotony([50.0] * MONOTONY_WINDOW_DAYS) is None


def test_a_varied_week_is_less_monotonous_than_an_even_one() -> None:
    even: list[float | None] = [60.0, 55.0, 60.0, 55.0, 60.0, 55.0, 60.0]
    varied: list[float | None] = [150.0, 0.0, 40.0, 0.0, 120.0, 0.0, 30.0]

    even_result = monotony(even)
    varied_result = monotony(varied)

    assert even_result is not None
    assert varied_result is not None
    assert even_result > varied_result


def test_monotony_needs_exactly_one_week() -> None:
    with pytest.raises(ValueError, match="daily loads"):
        monotony([50.0] * 6)


def test_strain_is_the_weekly_load_times_the_monotony() -> None:
    week: list[float | None] = [100.0, 50.0, 100.0, 50.0, 100.0, 50.0, 100.0]

    ratio = monotony(week)
    result = strain(week)

    assert ratio is not None
    assert result is not None
    total = sum(load for load in week if load is not None)
    assert result == pytest.approx(total * ratio)


def test_a_week_without_monotony_has_no_strain() -> None:
    assert strain([50.0] * MONOTONY_WINDOW_DAYS) is None


def test_a_rest_week_has_no_strain() -> None:
    assert strain([0.0] * MONOTONY_WINDOW_DAYS) is None
