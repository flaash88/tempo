"""Load formulas, each against a hand-computed expected value."""

from __future__ import annotations

import math

import pytest

from tempo.metrics.load import (
    FLAT_RUN_COST_J_PER_KG_M,
    decoupling,
    efficiency_factor,
    gap_speeds,
    grade_adjusted_speed,
    grades,
    heart_rate_reserve,
    hr_tss,
    intensity_factor,
    minetti_run_cost,
    normalised_graded_speed,
    r_tss,
    rolling_mean,
    smooth_altitude,
    trimp_at_threshold_hour,
    trimp_banister,
    trimp_edwards,
)
from tempo.metrics.thresholds import (
    DECOUPLING_MIN_DURATION_S,
    GAP_MAX_GRADE,
    NGP_ROLLING_WINDOW_S,
    TRIMP_BANISTER_EXPONENT,
    TRIMP_BANISTER_FACTOR,
)

HR_REST = 50
HR_MAX = 190
LTHR = 170


def series(value: float | None, count: int) -> list[float | None]:
    """A constant series of the optional element type the metrics take."""
    return [value for _ in range(count)]


# --- heart rate reserve ------------------------------------------------


def test_heart_rate_reserve_is_the_fraction_of_the_span_used() -> None:
    assert heart_rate_reserve(150, HR_REST, HR_MAX) == pytest.approx(100 / 140)


def test_heart_rate_reserve_clamps_at_both_ends() -> None:
    """A sample above the recorded maximum means a stale maximum."""
    assert heart_rate_reserve(200, HR_REST, HR_MAX) == pytest.approx(1.0)
    assert heart_rate_reserve(40, HR_REST, HR_MAX) == pytest.approx(0.0)


@pytest.mark.parametrize(
    ("hr", "rest", "hr_max"),
    [(None, HR_REST, HR_MAX), (150, None, HR_MAX), (150, HR_REST, None)],
)
def test_heart_rate_reserve_needs_all_three_numbers(
    hr: float | None, rest: int | None, hr_max: int | None
) -> None:
    assert heart_rate_reserve(hr, rest, hr_max) is None


def test_a_reserve_that_is_not_a_span_has_no_fraction() -> None:
    assert heart_rate_reserve(150, 190, 190) is None
    assert heart_rate_reserve(150, 200, 190) is None


# --- TRIMP -------------------------------------------------------------


def test_banister_trimp_matches_the_formula() -> None:
    hrr = 100 / 140

    expected = (
        60.0 * hrr * TRIMP_BANISTER_FACTOR * math.exp(TRIMP_BANISTER_EXPONENT * hrr)
    )

    assert trimp_banister(3600, 150, HR_REST, HR_MAX) == pytest.approx(expected)


def test_banister_trimp_scales_with_duration() -> None:
    one_hour = trimp_banister(3600, 150, HR_REST, HR_MAX)
    half_hour = trimp_banister(1800, 150, HR_REST, HR_MAX)

    assert one_hour is not None
    assert half_hour is not None
    assert one_hour == pytest.approx(2 * half_hour)


def test_a_run_without_heart_rate_has_no_trimp() -> None:
    """Not zero — a session that happened must not look like a rest day."""
    assert trimp_banister(3600, None, HR_REST, HR_MAX) is None


def test_a_session_with_no_duration_has_no_trimp() -> None:
    assert trimp_banister(0, 150, HR_REST, HR_MAX) is None
    assert trimp_banister(None, 150, HR_REST, HR_MAX) is None


def test_edwards_trimp_weights_the_zones_one_to_five() -> None:
    # Ten minutes in zone 1 and ten in zone 5: 10·1 + 10·5.
    assert trimp_edwards([600, 0, 0, 0, 600]) == pytest.approx(60.0)


def test_edwards_trimp_of_one_hour_in_zone_three() -> None:
    assert trimp_edwards([0, 0, 3600, 0, 0]) == pytest.approx(180.0)


def test_edwards_trimp_of_no_time_at_all_is_none() -> None:
    assert trimp_edwards([0, 0, 0, 0, 0]) is None


def test_edwards_trimp_needs_one_duration_per_zone() -> None:
    with pytest.raises(ValueError, match="zone durations"):
        trimp_edwards([600, 600])


# --- Minetti and grade adjustment --------------------------------------


def test_the_flat_cost_is_the_polynomials_constant_term() -> None:
    assert minetti_run_cost(0.0) == pytest.approx(FLAT_RUN_COST_J_PER_KG_M)
    assert minetti_run_cost(0.0) == pytest.approx(3.6)


def test_uphill_costs_more_and_downhill_less() -> None:
    assert minetti_run_cost(0.10) > minetti_run_cost(0.0)
    assert minetti_run_cost(-0.10) < minetti_run_cost(0.0)


def test_the_minetti_polynomial_matches_a_hand_evaluation() -> None:
    grade = 0.10
    expected = (
        155.4 * grade**5
        - 30.4 * grade**4
        - 43.3 * grade**3
        + 46.3 * grade**2
        + 19.5 * grade
        + 3.6
    )

    assert minetti_run_cost(grade) == pytest.approx(expected)


def test_grade_adjustment_scales_speed_by_the_cost_ratio() -> None:
    ratio = minetti_run_cost(0.05) / FLAT_RUN_COST_J_PER_KG_M

    assert grade_adjusted_speed(3.0, 0.05) == pytest.approx(3.0 * ratio)


def test_flat_running_is_unadjusted() -> None:
    assert grade_adjusted_speed(3.0, 0.0) == pytest.approx(3.0)


def test_an_outlier_gradient_is_discarded_not_trusted() -> None:
    """A barometric spike must not turn into an imaginary hill."""
    beyond = GAP_MAX_GRADE + 0.01

    assert grade_adjusted_speed(3.0, beyond) == pytest.approx(3.0)
    assert grade_adjusted_speed(3.0, -beyond) == pytest.approx(3.0)


# --- altitude smoothing and gradient -----------------------------------


def test_a_single_spike_is_smoothed_away() -> None:
    altitudes = series(200.0, 20)
    altitudes[10] = 260.0

    smoothed = smooth_altitude(altitudes, window_s=5)

    assert smoothed[10] == pytest.approx(200.0)


def test_smoothing_leaves_a_stretch_with_no_altitude_unknown() -> None:
    series: list[float | None] = [None] * 10

    assert smooth_altitude(series, window_s=3) == (None,) * 10


def test_a_window_of_less_than_a_second_is_refused() -> None:
    with pytest.raises(ValueError, match="at least one second"):
        smooth_altitude([200.0], window_s=0)


def test_the_gradient_is_rise_over_horizontal_distance() -> None:
    # Climbing 0.2 m per second at 2 m/s is a 10 % gradient.
    altitudes: list[float | None] = [200.0 + 0.2 * index for index in range(40)]
    speeds = series(2.0, 40)

    gradient = grades(altitudes, speeds)

    assert gradient[20] == pytest.approx(0.10, abs=1e-6)


def test_the_first_second_has_no_gradient() -> None:
    gradient = grades([200.0, 200.2], [2.0, 2.0])

    assert gradient[0] is None


def test_barely_moving_has_no_gradient() -> None:
    """Dividing a real climb by a near-zero distance manufactures a cliff."""
    gradient = grades([200.0, 205.0], [2.0, 0.1])

    assert gradient[1] is None


def test_a_missing_altitude_leaves_the_gradient_unknown() -> None:
    gradient = grades([None, None, None], [2.0, 2.0, 2.0])

    assert gradient == (None, None, None)


def test_mismatched_series_lengths_are_refused() -> None:
    with pytest.raises(ValueError, match="same length"):
        grades([200.0], [2.0, 2.0])


def test_a_second_without_speed_has_no_grade_adjusted_speed() -> None:
    adjusted = gap_speeds([2.0, None, 2.0], [200.0, 200.0, 200.0])

    assert adjusted[1] is None
    assert adjusted[0] == pytest.approx(2.0)


# --- rolling mean and NGP ----------------------------------------------


def test_a_rolling_mean_over_a_constant_series_is_that_constant() -> None:
    means = rolling_mean([3.0] * 10, 5)

    assert len(means) == 6
    assert all(value == pytest.approx(3.0) for value in means)


def test_a_window_overlapping_a_gap_is_skipped_not_shortened() -> None:
    values: list[float | None] = [3.0, 3.0, None, 3.0, 3.0]

    means = rolling_mean(values, 3)

    # Only the windows clear of the gap survive; there are none here.
    assert means == ()


def test_ngp_of_a_steady_flat_run_is_that_speed() -> None:
    seconds = NGP_ROLLING_WINDOW_S * 3
    speeds = series(3.0, seconds)
    altitudes = series(200.0, seconds)

    assert normalised_graded_speed(speeds, altitudes) == pytest.approx(3.0)


def test_ngp_weights_the_hard_patches_above_the_plain_mean() -> None:
    """The fourth-power mean is what makes surges cost what they cost."""
    seconds = NGP_ROLLING_WINDOW_S * 4
    speeds = series(2.0, seconds // 2) + series(4.0, seconds // 2)
    altitudes = series(200.0, seconds)

    ngp = normalised_graded_speed(speeds, altitudes)

    assert ngp is not None
    assert ngp > 3.0


def test_a_recording_shorter_than_the_window_has_no_ngp() -> None:
    short = NGP_ROLLING_WINDOW_S - 1
    speeds = series(3.0, short)

    assert normalised_graded_speed(speeds, [200.0] * short) is None


def test_ngp_rises_on_a_climb() -> None:
    seconds = NGP_ROLLING_WINDOW_S * 3
    speeds = series(3.0, seconds)
    climbing: list[float | None] = [200.0 + 0.15 * i for i in range(seconds)]

    flat = normalised_graded_speed(speeds, [200.0] * seconds)
    uphill = normalised_graded_speed(speeds, climbing)

    assert flat is not None
    assert uphill is not None
    assert uphill > flat


# --- intensity, rTSS, hrTSS --------------------------------------------


def test_the_intensity_factor_is_ngp_over_threshold_speed() -> None:
    assert intensity_factor(3.0, 3.3333333) == pytest.approx(0.9, abs=1e-6)


@pytest.mark.parametrize(
    ("ngp", "threshold"), [(None, 3.0), (3.0, None), (3.0, 0.0), (3.0, -1.0)]
)
def test_the_intensity_factor_needs_both_numbers(
    ngp: float | None, threshold: float | None
) -> None:
    assert intensity_factor(ngp, threshold) is None


def test_an_hour_at_threshold_is_a_hundred_rtss() -> None:
    assert r_tss(3600, 1.0) == pytest.approx(100.0)


def test_rtss_scales_with_the_square_of_the_intensity() -> None:
    assert r_tss(1800, 0.9) == pytest.approx(1800 * 0.81 / 3600 * 100)


def test_rtss_without_an_intensity_factor_is_none() -> None:
    assert r_tss(1800, None) is None
    assert r_tss(0, 0.9) is None


def test_an_hour_at_threshold_is_a_hundred_hrtss() -> None:
    reference = trimp_at_threshold_hour(LTHR, HR_REST, HR_MAX)

    assert reference is not None
    assert hr_tss(reference, reference) == pytest.approx(100.0)


def test_half_the_trimp_is_half_the_hrtss() -> None:
    reference = trimp_at_threshold_hour(LTHR, HR_REST, HR_MAX)

    assert reference is not None
    assert hr_tss(reference / 2, reference) == pytest.approx(50.0)


def test_hrtss_without_a_reference_is_none() -> None:
    """No threshold heart rate configured means no normalisation."""
    assert hr_tss(120.0, None) is None
    assert hr_tss(120.0, 0.0) is None
    assert trimp_at_threshold_hour(None, HR_REST, HR_MAX) is None


# --- efficiency factor and decoupling ----------------------------------


def test_the_efficiency_factor_is_speed_per_heartbeat() -> None:
    assert efficiency_factor(3.0, 150.0) == pytest.approx(0.02)


def test_the_efficiency_factor_needs_a_heart_rate() -> None:
    assert efficiency_factor(3.0, None) is None
    assert efficiency_factor(3.0, 0.0) is None
    assert efficiency_factor(None, 150.0) is None


def _steady_run(
    seconds: int, speed: float, hr: float
) -> tuple[list[float | None], list[float | None], list[float | None]]:
    return series(speed, seconds), series(200.0, seconds), series(hr, seconds)


def test_a_run_with_no_drift_has_no_decoupling() -> None:
    speeds, altitudes, rates = _steady_run(DECOUPLING_MIN_DURATION_S, 3.0, 150.0)

    assert decoupling(speeds, altitudes, rates) == pytest.approx(0.0)


def test_a_rising_heart_rate_at_constant_pace_decouples_positively() -> None:
    half = DECOUPLING_MIN_DURATION_S // 2
    speeds = series(3.0, half * 2)
    altitudes = series(200.0, half * 2)
    rates = series(150.0, half) + series(165.0, half)

    result = decoupling(speeds, altitudes, rates)

    assert result is not None
    assert result > 0.0
    # EF falls from 3/150 to 3/165, a drop of 1 - 150/165.
    assert result == pytest.approx(1 - 150 / 165, abs=1e-3)


def test_a_run_shorter_than_the_minimum_has_no_decoupling() -> None:
    short = DECOUPLING_MIN_DURATION_S - 1
    speeds, altitudes, rates = _steady_run(short, 3.0, 150.0)

    assert decoupling(speeds, altitudes, rates) is None


def test_a_heart_rate_dropout_disqualifies_decoupling() -> None:
    """Drift and a missing strap reading must not be confused."""
    speeds, altitudes, rates = _steady_run(DECOUPLING_MIN_DURATION_S, 3.0, 150.0)
    rates[100] = None

    assert decoupling(speeds, altitudes, rates) is None


def test_a_pause_disqualifies_decoupling() -> None:
    speeds, altitudes, rates = _steady_run(DECOUPLING_MIN_DURATION_S, 3.0, 150.0)
    speeds[100] = None

    assert decoupling(speeds, altitudes, rates) is None


def test_decoupling_refuses_mismatched_series() -> None:
    with pytest.raises(ValueError, match="same length"):
        decoupling([3.0], [200.0, 200.0], [150.0])
