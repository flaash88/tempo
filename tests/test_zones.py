"""Zones: one shape for heart rate and pace, and no guessing."""

from __future__ import annotations

import pytest

from tempo.metrics.thresholds import (
    FRIEL_LTHR_ZONE_LOWER_BOUNDS,
    ZONE_COUNT,
)
from tempo.metrics.zones import (
    ZoneSet,
    hr_zones_from_hr_max,
    hr_zones_from_lthr,
    pace_zones_from_threshold,
    speed_to_pace_s_per_km,
    time_in_zones,
    zone_of,
    zones_for_athlete,
)


def test_friel_zones_are_fractions_of_threshold_heart_rate() -> None:
    zones = hr_zones_from_lthr(170)

    assert zones.model == "friel_run_lthr"
    assert zones.kind == "hr"
    assert zones.lower_bounds == pytest.approx(
        tuple(fraction * 170 for fraction in FRIEL_LTHR_ZONE_LOWER_BOUNDS)
    )


@pytest.mark.parametrize(
    ("hr", "expected"),
    [
        (100, 1),  # below 85% of 170 = 144.5
        (144, 1),
        (145, 2),  # 85%
        (153, 3),  # 90%
        (162, 4),  # 95%
        (170, 5),  # 100%
        (185, 5),
    ],
)
def test_a_heart_rate_lands_in_the_right_friel_zone(hr: int, expected: int) -> None:
    assert zone_of(hr, hr_zones_from_lthr(170)) == expected


def test_percent_hr_max_zones_start_above_resting() -> None:
    zones = hr_zones_from_hr_max(190)

    assert zone_of(60, zones) is None
    assert zone_of(95, zones) == 1
    assert zone_of(190, zones) == 5


def test_pace_zones_are_held_as_speeds_so_faster_is_higher() -> None:
    # Threshold pace 300 s/km = 3.333 m/s.
    zones = pace_zones_from_threshold(300.0)

    assert zones.kind == "speed"
    assert zone_of(3.4, zones) == 5
    assert zone_of(3.2, zones) == 4
    assert zone_of(2.0, zones) == 1


def test_a_pace_converts_back_from_a_speed() -> None:
    assert speed_to_pace_s_per_km(3.3333333) == pytest.approx(300.0, abs=1e-3)


def test_standing_still_has_no_pace() -> None:
    assert speed_to_pace_s_per_km(0.0) is None


def test_an_unknown_value_has_no_zone() -> None:
    assert zone_of(None, hr_zones_from_lthr(170)) is None


@pytest.mark.parametrize("bad", [0, -1])
def test_zones_need_a_positive_reference(bad: int) -> None:
    with pytest.raises(ValueError, match="positive"):
        hr_zones_from_lthr(bad)
    with pytest.raises(ValueError, match="positive"):
        hr_zones_from_hr_max(bad)
    with pytest.raises(ValueError, match="positive"):
        pace_zones_from_threshold(float(bad))


def test_a_zone_set_must_have_five_ascending_bounds() -> None:
    with pytest.raises(ValueError, match="5 zone bounds"):
        ZoneSet(kind="hr", model="friel_run_lthr", lower_bounds=(1.0, 2.0))
    with pytest.raises(ValueError, match="ascend"):
        ZoneSet(
            kind="hr",
            model="friel_run_lthr",
            lower_bounds=(5.0, 4.0, 3.0, 2.0, 1.0),
        )


# --- time in zone ------------------------------------------------------


def test_each_sample_counts_as_one_second() -> None:
    zones = hr_zones_from_lthr(170)
    series = [150.0] * 60 + [165.0] * 30

    times = time_in_zones(series, zones)

    assert times.seconds[1] == 60  # zone 2
    assert times.seconds[3] == 30  # zone 4
    assert times.total_in_zones == 90
    assert times.minutes[1] == pytest.approx(1.0)


def test_a_dropout_counts_as_unknown_not_as_a_shorter_session() -> None:
    zones = hr_zones_from_lthr(170)
    series: list[float | None] = [150.0, None, None, 150.0]

    times = time_in_zones(series, zones)

    assert times.unknown == 2
    assert times.total_in_zones == 2
    assert times.below == 0


def test_time_under_zone_one_is_reported_separately() -> None:
    """Measured but too easy to be training is not the same as unmeasured."""
    zones = hr_zones_from_hr_max(190)
    series: list[float | None] = [60.0, 60.0, 100.0, None]

    times = time_in_zones(series, zones)

    assert times.below == 2
    assert times.unknown == 1
    assert times.seconds[0] == 1


def test_an_empty_series_has_no_time_anywhere() -> None:
    times = time_in_zones([], hr_zones_from_lthr(170))

    assert times.seconds == (0,) * ZONE_COUNT
    assert times.below == 0
    assert times.unknown == 0


# --- picking a model ---------------------------------------------------


def test_threshold_heart_rate_is_preferred_when_known() -> None:
    zones = zones_for_athlete(lthr=170, hr_max=190)

    assert zones is not None
    assert zones.model == "friel_run_lthr"


def test_hr_max_is_the_fallback() -> None:
    zones = zones_for_athlete(lthr=None, hr_max=190)

    assert zones is not None
    assert zones.model == "percent_hr_max"


def test_the_hr_max_model_can_be_chosen_explicitly() -> None:
    zones = zones_for_athlete(lthr=170, hr_max=190, zone_model="percent_hr_max")

    assert zones is not None
    assert zones.model == "percent_hr_max"


def test_with_neither_number_there_are_no_zones() -> None:
    """Nothing is estimated from age — an unknown stays unknown."""
    assert zones_for_athlete(lthr=None, hr_max=None) is None
