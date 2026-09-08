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

# More than this many consecutive days without data discards the baseline
# built on it, and closes the current window. Never average across a gap.
#
# It is also the horizon over which confidence decays with age: a value
# whose newest data point is this old is one the reset rule would throw
# away anyway, so its recency contribution is zero. Tying the decay to this
# threshold avoids inventing a second number for the same idea.
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

# --- Zone models -------------------------------------------------------

# Friel's five run zones, as a fraction of lactate threshold heart rate.
# The default model, per the plan.
FRIEL_LTHR_ZONE_LOWER_BOUNDS: Final[tuple[float, ...]] = (
    0.00,
    0.85,
    0.90,
    0.95,
    1.00,
)

# The common five-zone split as a fraction of maximum heart rate, for
# athletes who know their HRmax but not their threshold.
PERCENT_HR_MAX_ZONE_LOWER_BOUNDS: Final[tuple[float, ...]] = (
    0.50,
    0.60,
    0.70,
    0.80,
    0.90,
)

# Friel's run pace zones, as a fraction of threshold *speed* — so a higher
# fraction is a faster pace, and the bounds ascend like the heart rate ones.
FRIEL_PACE_ZONE_LOWER_BOUNDS: Final[tuple[float, ...]] = (
    0.00,
    0.78,
    0.88,
    0.94,
    1.00,
)

ZONE_COUNT: Final[int] = 5

# --- Load --------------------------------------------------------------

# Banister TRIMP: t · HRr · a · e^(b · HRr), male coefficients.
TRIMP_BANISTER_FACTOR: Final[float] = 0.64
TRIMP_BANISTER_EXPONENT: Final[float] = 1.92

# Edwards TRIMP: minutes in zone 1..5, weighted 1..5.
EDWARDS_ZONE_WEIGHTS: Final[tuple[float, ...]] = (1.0, 2.0, 3.0, 4.0, 5.0)

# Minetti et al. 2002, metabolic cost of running in J/kg/m as a polynomial
# in the gradient, highest power first.
MINETTI_RUN_COST_COEFFICIENTS: Final[tuple[float, ...]] = (
    155.4,
    -30.4,
    -43.3,
    46.3,
    19.5,
    3.6,
)

# The gradient is taken from median smoothed altitude over this window.
# Barometric altitude is noisy at one second resolution; a short median
# removes the spikes without flattening a real hill.
ALTITUDE_MEDIAN_WINDOW_S: Final[int] = 15

# Normalised graded speed: a rolling mean of the grade adjusted speed,
# then the fourth-power mean of those, which is what weights hard efforts
# the way the body pays for them.
NGP_ROLLING_WINDOW_S: Final[int] = 30
NGP_NORMALISATION_POWER: Final[int] = 4

# One hour at threshold is 100 points, for both rTSS and hrTSS.
TSS_AT_THRESHOLD_HOUR: Final[float] = 100.0
SECONDS_PER_HOUR: Final[int] = 3600

# --- Readiness ---------------------------------------------------------

# Weights of the four inputs. They sum to one; when an input is missing,
# the weights of the ones that are present are renormalised rather than the
# score being dragged down by a component nobody measured.
READINESS_WEIGHT_HRV: Final[float] = 0.40
READINESS_WEIGHT_RESTING_HR: Final[float] = 0.20
READINESS_WEIGHT_SLEEP: Final[float] = 0.20
READINESS_WEIGHT_TSB: Final[float] = 0.20

# Below this many inputs the number would say more about what is missing
# than about the athlete, so no value is delivered.
READINESS_MIN_COMPONENTS: Final[int] = 2

# A deviation of this many standard deviations from the baseline is the
# end of the scale in either direction.
READINESS_DEVIATION_CLAMP_SD: Final[float] = 2.0

# Sleep scores linearly up to this duration and no further.
READINESS_SLEEP_TARGET_S: Final[int] = 8 * 3600

# The training stress balance range mapped onto 0..100.
READINESS_TSB_RANGE: Final[tuple[float, float]] = (-30.0, 15.0)

# --- Wellness baselines ------------------------------------------------

# The band drawn around the baseline, in standard deviations.
HRV_BAND_SD_MULTIPLIER: Final[float] = 0.5

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
        "zones": {
            "count": ZONE_COUNT,
            "friel_lthr_lower_bounds": list(FRIEL_LTHR_ZONE_LOWER_BOUNDS),
            "percent_hr_max_lower_bounds": list(PERCENT_HR_MAX_ZONE_LOWER_BOUNDS),
            "friel_pace_lower_bounds": list(FRIEL_PACE_ZONE_LOWER_BOUNDS),
        },
        "readiness": {
            "weight_hrv": READINESS_WEIGHT_HRV,
            "weight_resting_hr": READINESS_WEIGHT_RESTING_HR,
            "weight_sleep": READINESS_WEIGHT_SLEEP,
            "weight_tsb": READINESS_WEIGHT_TSB,
            "min_components": READINESS_MIN_COMPONENTS,
            "deviation_clamp_sd": READINESS_DEVIATION_CLAMP_SD,
            "sleep_target_s": READINESS_SLEEP_TARGET_S,
            "tsb_range": list(READINESS_TSB_RANGE),
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
