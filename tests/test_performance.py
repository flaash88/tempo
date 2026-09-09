"""Best efforts, critical speed, VDOT and predictions.

The VDOT numbers are checked against Daniels' published table: VDOT 50 is
5000 m in 19:57, 10000 m in about 41:21 and a marathon in about 3:10:49.
"""

from __future__ import annotations

import pytest

from tempo.metrics.performance import (
    BestEffort,
    best_effort,
    best_efforts,
    critical_speed,
    oxygen_cost,
    percent_vo2max,
    race_predictions,
    riegel_prediction,
    vdot,
    vdot_prediction,
)
from tempo.metrics.thresholds import (
    CRITICAL_SPEED_MAX_DURATION_S,
    CRITICAL_SPEED_MIN_DURATION_S,
    MIN_PERFORMANCES_CRITICAL_SPEED,
    PEAK_DURATIONS_S,
    RIEGEL_EXPONENT,
)


def series(value: float | None, count: int) -> list[float | None]:
    return [value for _ in range(count)]


# --- best efforts ------------------------------------------------------


def test_a_steady_run_covers_speed_times_duration() -> None:
    effort = best_effort(series(3.0, 300), 60)

    assert effort is not None
    assert effort.distance_m == pytest.approx(180.0)
    assert effort.speed_m_s == pytest.approx(3.0)
    assert effort.pace_s_per_km == pytest.approx(1000 / 3.0)


def test_the_best_window_is_the_fastest_stretch() -> None:
    speeds = series(3.0, 100) + series(5.0, 60) + series(3.0, 100)

    effort = best_effort(speeds, 60)

    assert effort is not None
    assert effort.distance_m == pytest.approx(300.0)


def test_a_recording_shorter_than_the_window_has_no_effort() -> None:
    assert best_effort(series(3.0, 59), 60) is None


def test_a_window_overlapping_a_pause_is_skipped_not_closed_up() -> None:
    """A best five minutes stitched across a traffic light never happened."""
    speeds = series(3.0, 30) + series(None, 5) + series(9.0, 30)

    assert best_effort(speeds, 40) is None


def test_the_fastest_complete_window_wins_over_a_faster_broken_one() -> None:
    speeds = (
        series(4.0, 60)
        + series(None, 1)
        + series(9.0, 30)
        + series(None, 1)
        + series(9.0, 29)
    )

    effort = best_effort(speeds, 60)

    assert effort is not None
    assert effort.distance_m == pytest.approx(240.0)


def test_a_duration_of_zero_is_refused() -> None:
    with pytest.raises(ValueError, match="positive"):
        best_effort(series(3.0, 60), 0)


def test_best_efforts_covers_the_durations_that_fit() -> None:
    speeds = series(3.0, 400)

    found = best_efforts(speeds)

    assert set(found) == {60, 120, 300}
    assert all(duration in PEAK_DURATIONS_S for duration in found)


def test_best_efforts_of_an_empty_recording_is_empty() -> None:
    assert best_efforts([]) == {}


# --- critical speed ----------------------------------------------------


def _line(cs: float, d_prime: float, durations: tuple[int, ...]) -> list[BestEffort]:
    return [
        BestEffort(duration_s=duration, distance_m=cs * duration + d_prime)
        for duration in durations
    ]


def test_a_clean_line_recovers_its_slope_and_intercept_exactly() -> None:
    efforts = _line(4.0, 200.0, (300, 600, 1200, 1800))

    result = critical_speed(efforts)

    assert result is not None
    assert result.cs_m_s == pytest.approx(4.0)
    assert result.d_prime_m == pytest.approx(200.0)
    assert result.r_squared == pytest.approx(1.0)
    assert result.points == 4


def test_three_points_are_enough() -> None:
    efforts = _line(4.0, 200.0, (300, 900, 1800))

    result = critical_speed(efforts)

    assert result is not None
    assert result.points == MIN_PERFORMANCES_CRITICAL_SPEED


def test_two_points_are_not() -> None:
    """A line through two dots is not a measurement."""
    assert critical_speed(_line(4.0, 200.0, (300, 1800))) is None


def test_efforts_outside_the_duration_band_do_not_count() -> None:
    too_short = CRITICAL_SPEED_MIN_DURATION_S - 1
    too_long = CRITICAL_SPEED_MAX_DURATION_S + 1
    efforts = _line(4.0, 200.0, (too_short, 600, too_long))

    assert critical_speed(efforts) is None


def test_the_band_edges_are_inclusive() -> None:
    efforts = _line(
        4.0,
        200.0,
        (CRITICAL_SPEED_MIN_DURATION_S, 900, CRITICAL_SPEED_MAX_DURATION_S),
    )

    result = critical_speed(efforts)

    assert result is not None
    assert result.points == 3


def test_a_repeated_duration_is_not_weighted_twice() -> None:
    efforts = [
        BestEffort(duration_s=600, distance_m=2600.0),
        BestEffort(duration_s=600, distance_m=2600.0),
        BestEffort(duration_s=1200, distance_m=5000.0),
    ]

    assert critical_speed(efforts) is None


def test_no_efforts_at_all_gives_nothing() -> None:
    assert critical_speed([]) is None


def test_a_fit_with_a_negative_asymptote_is_refused() -> None:
    """A negative critical speed is not a speed."""
    efforts = [
        BestEffort(duration_s=300, distance_m=3000.0),
        BestEffort(duration_s=900, distance_m=2000.0),
        BestEffort(duration_s=1800, distance_m=1000.0),
    ]

    assert critical_speed(efforts) is None


def test_a_noisy_line_reports_a_lower_fit_quality() -> None:
    clean = critical_speed(_line(4.0, 200.0, (300, 600, 1200, 1800)))
    noisy = critical_speed(
        [
            BestEffort(duration_s=300, distance_m=1500.0),
            BestEffort(duration_s=600, distance_m=2500.0),
            BestEffort(duration_s=1200, distance_m=5200.0),
            BestEffort(duration_s=1800, distance_m=7100.0),
        ]
    )

    assert clean is not None
    assert noisy is not None
    assert noisy.r_squared < clean.r_squared


# --- Daniels' relations ------------------------------------------------


def test_the_sustainable_fraction_falls_with_duration() -> None:
    assert percent_vo2max(5.0) > percent_vo2max(30.0) > percent_vo2max(180.0)


def test_the_sustainable_fraction_tends_to_its_floor() -> None:
    assert percent_vo2max(10_000.0) == pytest.approx(0.8, abs=1e-6)


def test_the_oxygen_cost_rises_with_velocity() -> None:
    assert oxygen_cost(300.0) > oxygen_cost(200.0) > oxygen_cost(100.0)


def test_vdot_fifty_is_five_thousand_metres_in_nineteen_fifty_seven() -> None:
    """The anchor from Daniels' table."""
    result = vdot(5000.0, 19 * 60 + 57)

    assert result is not None
    assert result == pytest.approx(50.0, abs=0.1)


def test_a_faster_time_over_the_same_distance_is_a_higher_vdot() -> None:
    slower = vdot(5000.0, 20 * 60)
    faster = vdot(5000.0, 18 * 60)

    assert slower is not None
    assert faster is not None
    assert faster > slower


@pytest.mark.parametrize(
    ("distance", "seconds"), [(0.0, 1200.0), (5000.0, 0.0), (-1.0, 1200.0)]
)
def test_vdot_needs_a_real_performance(distance: float, seconds: float) -> None:
    assert vdot(distance, seconds) is None


# --- predictions -------------------------------------------------------


def test_a_vdot_prediction_round_trips_its_own_performance() -> None:
    seconds = 20 * 60
    value = vdot(5000.0, seconds)

    assert value is not None
    predicted = vdot_prediction(value, 5000.0)
    assert predicted is not None
    assert predicted == pytest.approx(seconds, abs=1.0)


def test_vdot_fifty_predicts_the_published_ten_thousand_and_marathon() -> None:
    value = vdot(5000.0, 19 * 60 + 57)

    assert value is not None
    ten_k = vdot_prediction(value, 10_000.0)
    marathon = vdot_prediction(value, 42_195.0)

    assert ten_k is not None
    assert marathon is not None
    # Daniels: 41:21 for the 10k, 3:10:49 for the marathon.
    assert ten_k == pytest.approx(41 * 60 + 21, abs=30)
    assert marathon == pytest.approx(3 * 3600 + 10 * 60 + 49, abs=90)


def test_a_longer_race_is_predicted_slower() -> None:
    value = vdot(5000.0, 20 * 60)

    assert value is not None
    five = vdot_prediction(value, 5000.0)
    ten = vdot_prediction(value, 10_000.0)

    assert five is not None
    assert ten is not None
    assert ten > 2 * five


def test_an_impossible_vdot_has_no_prediction() -> None:
    assert vdot_prediction(500.0, 5000.0) is None
    assert vdot_prediction(1.0, 5000.0) is None
    assert vdot_prediction(-1.0, 5000.0) is None
    assert vdot_prediction(50.0, 0.0) is None


def test_riegel_matches_the_formula() -> None:
    expected = 1200.0 * (10_000.0 / 5_000.0) ** RIEGEL_EXPONENT

    assert riegel_prediction(5000.0, 1200.0, 10_000.0) == pytest.approx(expected)


def test_riegel_over_the_same_distance_returns_the_same_time() -> None:
    assert riegel_prediction(5000.0, 1200.0, 5000.0) == pytest.approx(1200.0)


@pytest.mark.parametrize(
    ("known", "seconds", "target"),
    [(0.0, 1200.0, 10_000.0), (5000.0, 0.0, 10_000.0), (5000.0, 1200.0, 0.0)],
)
def test_riegel_needs_three_real_numbers(
    known: float, seconds: float, target: float
) -> None:
    assert riegel_prediction(known, seconds, target) is None


def test_both_methods_are_reported_and_never_averaged() -> None:
    predictions = race_predictions(5000.0, 20 * 60)

    assert set(predictions) == {"5k", "10k", "half_marathon", "marathon"}
    for entries in predictions.values():
        methods = {entry.method for entry in entries}
        assert methods == {"riegel", "vdot"}
    # The two disagree, and the disagreement is what is on offer.
    marathon = {entry.method: entry.seconds for entry in predictions["marathon"]}
    assert marathon["riegel"] != marathon["vdot"]


def test_predictions_from_an_unusable_performance_are_empty() -> None:
    assert race_predictions(0.0, 1200.0) == {}


def test_a_custom_set_of_distances_is_honoured() -> None:
    predictions = race_predictions(5000.0, 20 * 60, {"3k": 3000.0})

    assert set(predictions) == {"3k"}
