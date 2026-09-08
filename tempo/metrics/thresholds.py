"""Every threshold, window size and reset rule — in one place.

The frontend holds no numbers of its own. It reads these values from
``GET /api/thresholds`` and fills its texts at runtime, so a change here
propagates to the interface without a second edit somewhere else.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Final, Literal

Unit = Literal["days", "nights", "performances"]


@dataclass(frozen=True, slots=True)
class MinimumHistory:
    """How much unbroken history a metric needs before it has a value.

    Below ``required`` the API answers with ``value: null`` plus ``have``,
    ``required`` and ``available_from`` — never with a provisional number.
    """

    key: str
    required: int
    unit: Unit
    label_de: str


# --- Minimum history ---------------------------------------------------

MIN_NIGHTS_READINESS: Final[int] = 14
MIN_DAYS_HRV_BASELINE: Final[int] = 21
MIN_DAYS_FORM: Final[int] = 42
MIN_PERFORMANCES_CRITICAL_SPEED: Final[int] = 3

MINIMUM_HISTORY: Final[dict[str, MinimumHistory]] = {
    "readiness": MinimumHistory(
        key="readiness",
        required=MIN_NIGHTS_READINESS,
        unit="nights",
        label_de="Bereitschaft",
    ),
    "hrv_baseline": MinimumHistory(
        key="hrv_baseline",
        required=MIN_DAYS_HRV_BASELINE,
        unit="days",
        label_de="HRV-Baseline",
    ),
    "form": MinimumHistory(
        key="form",
        required=MIN_DAYS_FORM,
        unit="days",
        label_de="Formkurve",
    ),
    "critical_speed": MinimumHistory(
        key="critical_speed",
        required=MIN_PERFORMANCES_CRITICAL_SPEED,
        unit="performances",
        label_de="Critical Speed",
    ),
}

# --- Staleness and baseline reset --------------------------------------

# A value whose last data point is older than this is never shown as the
# current state. The API flags it, the tile shows the date instead.
STALE_AFTER_DAYS: Final[int] = 7

# More than this many consecutive days without wellness data discards the
# HRV and resting heart rate baseline. Never average across a gap.
BASELINE_RESET_GAP_DAYS: Final[int] = 14

# --- Load and fitness windows ------------------------------------------

CTL_TIME_CONSTANT_DAYS: Final[int] = 42
ATL_TIME_CONSTANT_DAYS: Final[int] = 7
ACWR_ACUTE_DAYS: Final[int] = 7
ACWR_CHRONIC_DAYS: Final[int] = 28
MONOTONY_WINDOW_DAYS: Final[int] = 7

# --- Wellness baselines ------------------------------------------------

HRV_ROLLING_MEAN_DAYS: Final[int] = 7
HRV_REFERENCE_WINDOW_DAYS: Final[int] = 60
HRV_BAND_SD: Final[float] = 0.5

# --- Performance -------------------------------------------------------

PEAK_DURATIONS_S: Final[tuple[int, ...]] = (60, 120, 300, 600, 1200, 1800, 3600)

# Critical speed regression only accepts efforts in this duration band.
CRITICAL_SPEED_MIN_DURATION_S: Final[int] = 180
CRITICAL_SPEED_MAX_DURATION_S: Final[int] = 1800

RIEGEL_EXPONENT: Final[float] = 1.06

# Decoupling needs a long enough run with uninterrupted heart rate.
DECOUPLING_MIN_DURATION_S: Final[int] = 1800

# Grade adjusted pace discards gradients beyond this fraction as artefacts
# of noisy barometric altitude.
GAP_MAX_GRADE: Final[float] = 0.30


def as_dict() -> dict[str, Any]:
    """Serialisable view of every threshold, for ``GET /api/thresholds``."""
    return {
        "minimum_history": {
            key: {
                "required": item.required,
                "unit": item.unit,
                "label_de": item.label_de,
            }
            for key, item in MINIMUM_HISTORY.items()
        },
        "stale_after_days": STALE_AFTER_DAYS,
        "baseline_reset_gap_days": BASELINE_RESET_GAP_DAYS,
        "load": {
            "ctl_time_constant_days": CTL_TIME_CONSTANT_DAYS,
            "atl_time_constant_days": ATL_TIME_CONSTANT_DAYS,
            "acwr_acute_days": ACWR_ACUTE_DAYS,
            "acwr_chronic_days": ACWR_CHRONIC_DAYS,
            "monotony_window_days": MONOTONY_WINDOW_DAYS,
        },
        "wellness": {
            "hrv_rolling_mean_days": HRV_ROLLING_MEAN_DAYS,
            "hrv_reference_window_days": HRV_REFERENCE_WINDOW_DAYS,
            "hrv_band_sd": HRV_BAND_SD,
        },
        "performance": {
            "peak_durations_s": list(PEAK_DURATIONS_S),
            "critical_speed_min_duration_s": CRITICAL_SPEED_MIN_DURATION_S,
            "critical_speed_max_duration_s": CRITICAL_SPEED_MAX_DURATION_S,
            "riegel_exponent": RIEGEL_EXPONENT,
            "decoupling_min_duration_s": DECOUPLING_MIN_DURATION_S,
            "gap_max_grade": GAP_MAX_GRADE,
        },
    }
