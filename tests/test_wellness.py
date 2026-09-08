"""Wellness baselines and readiness.

The baseline never averages across a gap, and it never pools two different
HRV measures. Nothing here — and nothing in the module — states which
measure the value is.
"""

from __future__ import annotations

import datetime as dt
import math

import pytest

from tempo.metrics.thresholds import (
    DEFAULT_READINESS_WEIGHTS,
    DEFAULT_SLEEP_TARGET_S,
    HRV_ROLLING_MEAN_DAYS,
    MIN_DAYS_HRV_BASELINE,
    MIN_NIGHTS_READINESS,
    READINESS_DEVIATION_CLAMP_SD,
    READINESS_MIN_COMPONENTS,
    READINESS_TSB_RANGE,
    ReadinessWeights,
)
from tempo.metrics.wellness import (
    Baseline,
    ReadinessComponents,
    Reading,
    homogeneous_tail,
    hrv_baseline,
    readiness,
    readiness_components,
    readiness_score,
    resting_hr_baseline,
)

TODAY = dt.date(2026, 9, 8)


def readings(
    *,
    days: int,
    end: dt.date = TODAY,
    value: float = 45.0,
    field: str | None = "hrv",
) -> list[Reading]:
    """A contiguous run of identical readings ending on ``end``."""
    return [
        Reading(
            date=end - dt.timedelta(days=days - 1 - offset),
            value=value,
            source_field=field,
        )
        for offset in range(days)
    ]


# --- one measure at a time ---------------------------------------------


def test_a_single_source_field_keeps_the_whole_run() -> None:
    series = readings(days=10)

    assert len(homogeneous_tail(series)) == 10


def test_a_change_of_source_field_cuts_the_older_part_off() -> None:
    """Two measures in one baseline would be a blend of two quantities."""
    older = readings(days=10, end=TODAY - dt.timedelta(days=5), field="hrvSDNN")
    newer = readings(days=5, field="hrv")

    tail = homogeneous_tail([*older, *newer])

    assert len(tail) == 5
    assert {reading.source_field for reading in tail} == {"hrv"}


def test_an_empty_series_has_no_tail() -> None:
    assert homogeneous_tail([]) == ()


def test_a_baseline_records_which_field_it_was_built_from() -> None:
    result = hrv_baseline(readings(days=30, field="hrvSDNN"), as_of=TODAY)

    assert result.value is not None
    assert result.value.source_field == "hrvSDNN"


def test_a_switch_of_measure_resets_the_baseline_like_a_gap_does() -> None:
    older = readings(days=40, end=TODAY - dt.timedelta(days=5), field="hrv")
    newer = readings(days=5, field="hrvSDNN")

    result = hrv_baseline([*older, *newer], as_of=TODAY)

    assert result.value is None
    assert result.have == 5
    assert result.required == MIN_DAYS_HRV_BASELINE


# --- the HRV baseline --------------------------------------------------


def test_a_steady_series_has_the_log_of_its_value_as_the_mean() -> None:
    result = hrv_baseline(readings(days=30, value=45.0), as_of=TODAY)

    assert result.value is not None
    assert result.value.log_transformed is True
    assert result.value.mean == pytest.approx(math.log(45.0))
    assert result.value.sd == pytest.approx(0.0)


def test_the_baseline_mean_matches_a_hand_computed_rolling_mean() -> None:
    values = [40.0 + index for index in range(MIN_DAYS_HRV_BASELINE)]
    series = [
        Reading(
            date=TODAY - dt.timedelta(days=len(values) - 1 - index),
            value=value,
            source_field="hrv",
        )
        for index, value in enumerate(values)
    ]
    logs = [math.log(value) for value in values]
    expected_last_mean = sum(logs[-HRV_ROLLING_MEAN_DAYS:]) / HRV_ROLLING_MEAN_DAYS

    result = hrv_baseline(series, as_of=TODAY)

    assert result.value is not None
    assert result.value.mean == pytest.approx(expected_last_mean)


def test_the_band_is_half_a_standard_deviation_either_side() -> None:
    # A period of five against a seven day window, so the rolling means vary.
    values = [40.0 + (index % 5) * 2 for index in range(40)]
    series = [
        Reading(
            date=TODAY - dt.timedelta(days=len(values) - 1 - index),
            value=value,
            source_field="hrv",
        )
        for index, value in enumerate(values)
    ]

    result = hrv_baseline(series, as_of=TODAY)

    assert result.value is not None
    baseline = result.value
    assert baseline.sd > 0
    assert baseline.upper - baseline.mean == pytest.approx(0.5 * baseline.sd)
    assert baseline.mean - baseline.lower == pytest.approx(0.5 * baseline.sd)


def test_below_the_minimum_history_there_is_no_baseline() -> None:
    short = MIN_DAYS_HRV_BASELINE - 1

    result = hrv_baseline(readings(days=short), as_of=TODAY)

    assert result.value is None
    assert result.have == short
    assert result.required == MIN_DAYS_HRV_BASELINE
    assert result.available_from == TODAY + dt.timedelta(days=1)


def test_a_single_data_point_yields_no_baseline() -> None:
    result = hrv_baseline(readings(days=1), as_of=TODAY)

    assert result.value is None
    assert result.have == 1
    assert result.last_data_point == TODAY


def test_no_readings_at_all_yields_nothing_and_no_staleness() -> None:
    result = hrv_baseline([], as_of=TODAY)

    assert result.value is None
    assert result.have == 0
    assert result.last_data_point is None
    assert result.stale is False


def test_a_gap_of_over_fourteen_days_discards_the_older_block() -> None:
    """The reset rule: never average across a gap."""
    older = readings(days=30, end=TODAY - dt.timedelta(days=20))
    newer = readings(days=5)

    result = hrv_baseline([*older, *newer], as_of=TODAY)

    assert result.value is None
    assert result.have == 5


def test_a_block_three_months_old_is_history_not_a_baseline() -> None:
    old_block = readings(days=16, end=dt.date(2026, 5, 16))

    result = hrv_baseline(old_block, as_of=TODAY)

    assert result.value is None
    assert result.have == 0
    assert result.last_data_point == dt.date(2026, 5, 16)
    assert result.stale is True


def test_a_non_positive_reading_cannot_be_log_transformed_and_is_dropped() -> None:
    series = readings(days=MIN_DAYS_HRV_BASELINE)
    series[5] = Reading(date=series[5].date, value=0.0, source_field="hrv")

    result = hrv_baseline(series, as_of=TODAY)

    # One reading short of the minimum once the unusable one is dropped.
    assert result.value is None


# --- the resting heart rate baseline -----------------------------------


def test_the_resting_heart_rate_baseline_is_not_log_transformed() -> None:
    result = resting_hr_baseline(readings(days=30, value=52.0, field=None), as_of=TODAY)

    assert result.value is not None
    assert result.value.log_transformed is False
    assert result.value.mean == pytest.approx(52.0)


def test_the_resting_heart_rate_baseline_uses_the_same_reset_rule() -> None:
    older = readings(days=30, end=TODAY - dt.timedelta(days=30), value=52.0)
    newer = readings(days=4, value=52.0)

    result = resting_hr_baseline([*older, *newer], as_of=TODAY)

    assert result.value is None
    assert result.have == 4


# --- deviations --------------------------------------------------------


def test_a_deviation_is_measured_in_the_space_the_baseline_was_built_in() -> None:
    baseline = Baseline(
        mean=math.log(45.0),
        sd=0.1,
        days=30,
        last_data_point=TODAY,
        log_transformed=True,
    )

    deviation = baseline.deviation_sd(45.0 * math.e**0.1)

    assert deviation == pytest.approx(1.0)


def test_a_deviation_without_a_spread_is_undefined() -> None:
    flat = Baseline(mean=1.0, sd=0.0, days=30, last_data_point=TODAY)

    assert flat.deviation_sd(2.0) is None


def test_a_non_positive_reading_has_no_deviation_from_a_log_baseline() -> None:
    baseline = Baseline(
        mean=1.0, sd=0.1, days=30, last_data_point=TODAY, log_transformed=True
    )

    assert baseline.deviation_sd(0.0) is None
    assert baseline.deviation_sd(None) is None


# --- readiness components ---------------------------------------------


def test_a_reading_on_the_baseline_scores_fifty() -> None:
    components = readiness_components(
        hrv_deviation_sd=0.0,
        resting_hr_deviation_sd=0.0,
        sleep_secs=None,
        tsb=None,
    )

    assert components.hrv == pytest.approx(50.0)
    assert components.resting_hr == pytest.approx(50.0)


def test_higher_variability_scores_above_the_baseline() -> None:
    components = readiness_components(
        hrv_deviation_sd=READINESS_DEVIATION_CLAMP_SD,
        resting_hr_deviation_sd=None,
        sleep_secs=None,
        tsb=None,
    )

    assert components.hrv == pytest.approx(100.0)


def test_a_raised_resting_heart_rate_scores_below_the_baseline() -> None:
    """Higher is the unwelcome direction for this one."""
    components = readiness_components(
        hrv_deviation_sd=None,
        resting_hr_deviation_sd=READINESS_DEVIATION_CLAMP_SD,
        sleep_secs=None,
        tsb=None,
    )

    assert components.resting_hr == pytest.approx(0.0)


def test_a_deviation_beyond_the_clamp_does_not_run_off_the_scale() -> None:
    components = readiness_components(
        hrv_deviation_sd=READINESS_DEVIATION_CLAMP_SD * 10,
        resting_hr_deviation_sd=None,
        sleep_secs=None,
        tsb=None,
    )

    assert components.hrv == pytest.approx(100.0)


def test_sleep_scores_linearly_up_to_the_target() -> None:
    half = readiness_components(
        hrv_deviation_sd=None,
        resting_hr_deviation_sd=None,
        sleep_secs=DEFAULT_SLEEP_TARGET_S // 2,
        tsb=None,
    )
    full = readiness_components(
        hrv_deviation_sd=None,
        resting_hr_deviation_sd=None,
        sleep_secs=DEFAULT_SLEEP_TARGET_S * 2,
        tsb=None,
    )

    assert half.sleep == pytest.approx(50.0)
    assert full.sleep == pytest.approx(100.0)


def test_the_sleep_target_can_be_the_athletes_own() -> None:
    """Seven hours of sleep is a full night for someone who targets seven."""
    seven_hours = 7 * 3600

    scored = readiness_components(
        hrv_deviation_sd=None,
        resting_hr_deviation_sd=None,
        sleep_secs=seven_hours,
        tsb=None,
        sleep_target_s=seven_hours,
    )
    against_default = readiness_components(
        hrv_deviation_sd=None,
        resting_hr_deviation_sd=None,
        sleep_secs=seven_hours,
        tsb=None,
    )

    assert scored.sleep == pytest.approx(100.0)
    assert against_default.sleep == pytest.approx(87.5)


def test_a_target_of_nothing_leaves_the_sleep_term_absent() -> None:
    scored = readiness_components(
        hrv_deviation_sd=None,
        resting_hr_deviation_sd=None,
        sleep_secs=25_200,
        tsb=None,
        sleep_target_s=0,
    )

    assert scored.sleep is None


def test_form_maps_onto_the_configured_range() -> None:
    low, high = READINESS_TSB_RANGE
    bottom = readiness_components(
        hrv_deviation_sd=None,
        resting_hr_deviation_sd=None,
        sleep_secs=None,
        tsb=low - 10,
    )
    top = readiness_components(
        hrv_deviation_sd=None,
        resting_hr_deviation_sd=None,
        sleep_secs=None,
        tsb=high + 10,
    )

    assert bottom.tsb == pytest.approx(0.0)
    assert top.tsb == pytest.approx(100.0)


def test_missing_inputs_stay_missing() -> None:
    components = readiness_components(
        hrv_deviation_sd=None,
        resting_hr_deviation_sd=None,
        sleep_secs=None,
        tsb=None,
    )

    assert components.present == 0


# --- the readiness score ----------------------------------------------


def test_all_components_at_fifty_score_fifty() -> None:
    components = ReadinessComponents(hrv=50.0, resting_hr=50.0, sleep=50.0, tsb=50.0)

    assert readiness_score(components) == 50


def test_the_weights_of_the_present_components_are_renormalised() -> None:
    """A component nobody measured must not drag the score down."""
    components = ReadinessComponents(hrv=80.0, sleep=60.0)

    # 0.40 and 0.20 renormalise to 2/3 and 1/3.
    assert readiness_score(components) == round(80.0 * 2 / 3 + 60.0 * 1 / 3)


def test_the_default_weights_are_the_documented_ones() -> None:
    assert DEFAULT_READINESS_WEIGHTS.hrv == pytest.approx(0.40)
    assert DEFAULT_READINESS_WEIGHTS.resting_hr == pytest.approx(0.20)
    assert DEFAULT_READINESS_WEIGHTS.sleep == pytest.approx(0.20)
    assert DEFAULT_READINESS_WEIGHTS.tsb == pytest.approx(0.20)
    assert sum(DEFAULT_READINESS_WEIGHTS.as_dict().values()) == pytest.approx(1.0)


def test_configured_weights_change_the_score() -> None:
    components = ReadinessComponents(hrv=100.0, sleep=0.0)
    sleep_heavy = ReadinessWeights(hrv=0.1, resting_hr=0.2, sleep=0.6, tsb=0.1)

    assert readiness_score(components, DEFAULT_READINESS_WEIGHTS) == round(
        100.0 * 0.4 / 0.6
    )
    assert readiness_score(components, sleep_heavy) == round(100.0 * 0.1 / 0.7)


def test_only_the_ratios_of_the_weights_matter() -> None:
    """A set scaled by ten behaves exactly as the original."""
    components = ReadinessComponents(hrv=80.0, sleep=40.0, tsb=60.0)
    scaled = ReadinessWeights(hrv=4.0, resting_hr=2.0, sleep=2.0, tsb=2.0)

    assert readiness_score(components, scaled) == readiness_score(
        components, DEFAULT_READINESS_WEIGHTS
    )


@pytest.mark.parametrize(
    "weights",
    [
        {"hrv": -0.1, "resting_hr": 0.2, "sleep": 0.2, "tsb": 0.2},
        {"hrv": 0.0, "resting_hr": 0.0, "sleep": 0.0, "tsb": 0.0},
    ],
)
def test_unusable_weights_are_refused(weights: dict[str, float]) -> None:
    with pytest.raises(ValueError):
        ReadinessWeights(**weights)


def test_one_component_alone_is_not_a_readiness_score() -> None:
    assert readiness_score(ReadinessComponents(sleep=90.0)) is None
    assert READINESS_MIN_COMPONENTS == 2


def test_no_components_at_all_score_nothing() -> None:
    assert readiness_score(ReadinessComponents()) is None


def test_the_score_stays_inside_zero_to_a_hundred() -> None:
    assert readiness_score(ReadinessComponents(hrv=100.0, sleep=100.0)) == 100
    assert readiness_score(ReadinessComponents(hrv=0.0, sleep=0.0)) == 0


# --- readiness with its evidence --------------------------------------


def test_readiness_needs_the_minimum_number_of_nights() -> None:
    nights = [
        TODAY - dt.timedelta(days=offset) for offset in range(MIN_NIGHTS_READINESS)
    ]
    components = ReadinessComponents(hrv=60.0, sleep=70.0)

    result = readiness(nights=nights, components=components, as_of=TODAY)

    assert result.value is not None
    assert result.have == MIN_NIGHTS_READINESS
    assert result.stale is False


def test_readiness_below_the_minimum_reports_progress_instead() -> None:
    nights = [TODAY - dt.timedelta(days=offset) for offset in range(3)]
    components = ReadinessComponents(hrv=60.0, sleep=70.0)

    result = readiness(nights=nights, components=components, as_of=TODAY)

    assert result.value is None
    assert result.have == 3
    assert result.required == MIN_NIGHTS_READINESS
    assert result.available_from == TODAY + dt.timedelta(days=MIN_NIGHTS_READINESS - 3)


def test_readiness_works_before_the_hrv_baseline_exists() -> None:
    """Fourteen nights is the gate; the HRV baseline needs twenty-one."""
    nights = [
        TODAY - dt.timedelta(days=offset) for offset in range(MIN_NIGHTS_READINESS)
    ]
    components = readiness_components(
        hrv_deviation_sd=None,
        resting_hr_deviation_sd=0.5,
        sleep_secs=DEFAULT_SLEEP_TARGET_S,
        tsb=0.0,
    )

    result = readiness(nights=nights, components=components, as_of=TODAY)

    assert result.value is not None
    assert components.hrv is None
    assert components.present == 3


def test_readiness_from_an_old_block_is_withheld_but_dated() -> None:
    old_nights = [
        dt.date(2026, 5, 16) - dt.timedelta(days=offset) for offset in range(16)
    ]
    components = ReadinessComponents(hrv=60.0, sleep=70.0)

    result = readiness(nights=old_nights, components=components, as_of=TODAY)

    assert result.value is None
    assert result.have == 0
    assert result.last_data_point == dt.date(2026, 5, 16)
    assert result.stale is True
    assert result.confidence == 0.0


# --- no interpretation of the measure ---------------------------------


def test_the_baseline_makes_no_assumption_about_the_scale_of_the_values() -> None:
    """rMSSD sits around 45, SDNN around 90 — neither is privileged."""
    small = hrv_baseline(readings(days=30, value=45.0, field="hrv"), as_of=TODAY)
    large = hrv_baseline(readings(days=30, value=90.0, field="hrvSDNN"), as_of=TODAY)

    assert small.value is not None
    assert large.value is not None
    # Both are usable baselines; neither is rejected or rescaled to the other.
    assert small.value.mean == pytest.approx(math.log(45.0))
    assert large.value.mean == pytest.approx(math.log(90.0))
    assert small.value.sd == pytest.approx(large.value.sd)


def test_a_deviation_is_relative_so_the_scale_cancels_out() -> None:
    """The same relative swing reads the same whatever the measure is."""
    factor = 1.10
    small = [
        Reading(
            date=TODAY - dt.timedelta(days=29 - index),
            value=45.0 * (factor if index % 2 else 1.0),
            source_field="hrv",
        )
        for index in range(30)
    ]
    large = [
        Reading(
            date=reading.date,
            value=reading.value * 2.0,
            source_field="hrvSDNN",
        )
        for reading in small
    ]

    small_result = hrv_baseline(small, as_of=TODAY)
    large_result = hrv_baseline(large, as_of=TODAY)

    assert small_result.value is not None
    assert large_result.value is not None
    assert small_result.value.sd == pytest.approx(large_result.value.sd)


def test_a_reading_with_no_source_field_still_builds_a_baseline() -> None:
    """An unnamed source is not a reason to refuse the arithmetic."""
    result = hrv_baseline(readings(days=30, field=None), as_of=TODAY)

    assert result.value is not None
    assert result.value.source_field is None
