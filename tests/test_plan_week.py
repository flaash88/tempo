"""The week plan's shape, and what it refuses.

Nothing here talks to a model. These are the checks that run *on* an
answer, so they are tested against strings — including the strings a model
actually produces when it does not follow instructions: prose, a fenced
block, six days instead of seven, a heart rate it worked out itself.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any

import pytest

from tempo.ai.plan_week import (
    MAX_RATIONALE_SENTENCES,
    PLAN_DAYS,
    PlanFormatProblem,
    check,
    format_contract,
    hr_window,
    parse,
    plan_window,
    resolve,
)
from tempo.db.models import AthleteSettings
from tempo.metrics.zones import hr_zones_from_hr_max, hr_zones_from_lthr

AS_OF = dt.date(2026, 9, 10)
WINDOW = plan_window(AS_OF)


def a_day(day: dt.date, **overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "date": day.isoformat(),
        "kind": "session",
        "title": "Lockerer Dauerlauf",
        "duration_min": 40,
        "zone": "Z2",
        "purpose": "Grundlage aufbauen ohne Ermüdung.",
    }
    values.update(overrides)
    return values


def a_plan(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "rationale": "Erste Woche zurück. Kurze Einheiten, viel Ruhe.",
        "days": [a_day(day) for day in WINDOW],
        "limitations": ["Die Formkurve braucht 42 Tage, vorliegen 8."],
    }
    payload.update(overrides)
    return payload


def as_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False)


# --- the window --------------------------------------------------------


def test_the_window_is_the_seven_days_after_today() -> None:
    assert len(WINDOW) == PLAN_DAYS
    assert WINDOW[0] == AS_OF + dt.timedelta(days=1)
    assert WINDOW[-1] == AS_OF + dt.timedelta(days=7)


def test_the_contract_names_every_date_the_answer_must_carry() -> None:
    contract = format_contract(WINDOW)

    for day in WINDOW:
        assert day.isoformat() in contract
    # And it says in as many words that heart rates are not the model's.
    assert "Zielherzfrequenzen trägst du nicht ein" in contract


# --- what passes -------------------------------------------------------


def test_a_well_formed_answer_passes() -> None:
    assert check(as_text(a_plan()), window=WINDOW) is None


def test_a_fenced_object_is_unwrapped_but_still_checked() -> None:
    fenced = f"```json\n{as_text(a_plan())}\n```"

    assert check(fenced, window=WINDOW) is None


def test_a_rest_day_needs_neither_duration_nor_zone() -> None:
    payload = a_plan(
        days=[
            a_day(
                WINDOW[0], kind="rest", title="Ruhetag", duration_min=None, zone=None
            ),
            *[a_day(day) for day in WINDOW[1:]],
        ]
    )

    assert check(as_text(payload), window=WINDOW) is None


# --- what does not -----------------------------------------------------


def test_prose_is_refused() -> None:
    problem = check("Montag 40 min Zone 2, Dienstag Ruhetag, Mittwoch …", window=WINDOW)

    assert problem is not None
    assert "JSON" in problem


def test_the_dates_have_to_be_the_windows_dates() -> None:
    shifted = [day + dt.timedelta(days=1) for day in WINDOW]
    payload = a_plan(days=[a_day(day) for day in shifted])

    problem = check(as_text(payload), window=WINDOW)

    assert problem is not None
    assert WINDOW[0].isoformat() in problem


def test_six_days_is_not_a_week() -> None:
    payload = a_plan(days=[a_day(day) for day in WINDOW[:-1]])

    assert check(as_text(payload), window=WINDOW) is not None


def test_a_fourth_sentence_in_the_rationale_is_refused() -> None:
    payload = a_plan(rationale="Eins. Zwei. Drei. Vier.")

    problem = check(as_text(payload), window=WINDOW)

    assert problem is not None
    assert str(MAX_RATIONALE_SENTENCES) in problem


def test_a_session_without_a_zone_is_refused() -> None:
    payload = a_plan(days=[a_day(day, zone=None) for day in WINDOW])

    problem = check(as_text(payload), window=WINDOW)

    assert problem is not None
    assert "zone" in problem


def test_a_rest_day_that_carries_a_session_is_refused() -> None:
    payload = a_plan(
        days=[
            a_day(WINDOW[0], kind="rest", title="Ruhetag"),
            *[a_day(day) for day in WINDOW[1:]],
        ]
    )

    assert check(as_text(payload), window=WINDOW) is not None


def test_a_purpose_of_three_sentences_is_refused() -> None:
    payload = a_plan(days=[a_day(day, purpose="Eins. Zwei. Drei.") for day in WINDOW])

    assert check(as_text(payload), window=WINDOW) is not None


def test_a_heart_rate_the_model_invented_has_nowhere_to_go() -> None:
    """The field does not exist, so an answer that adds one is refused."""
    payload = a_plan(days=[a_day(day, target_hr="132-145") for day in WINDOW])

    problem = check(as_text(payload), window=WINDOW)

    assert problem is not None
    assert "target_hr" in problem


def test_parse_raises_where_check_reports() -> None:
    with pytest.raises(PlanFormatProblem):
        parse("nicht mal ansatzweise JSON", window=WINDOW)


# --- the heart rates the server adds -----------------------------------


def test_the_zones_heart_rates_come_from_the_athletes_threshold() -> None:
    athlete = AthleteSettings(id=1, lthr=168, hr_max=188)

    plan = resolve(
        parse(as_text(a_plan()), window=WINDOW), window=WINDOW, athlete=athlete
    )

    monday = plan.days[0]
    assert monday.zone == 2
    # Friel zone 2 runs from 85 % to 90 % of threshold heart rate.
    assert monday.target_hr_low == round(0.85 * 168)
    assert monday.target_hr_high == round(0.90 * 168) - 1
    assert plan.hr_source == "friel_run_lthr"
    assert plan.hr_note is None


def test_without_thresholds_there_are_no_heart_rates_and_the_plan_says_so() -> None:
    plan = resolve(parse(as_text(a_plan()), window=WINDOW), window=WINDOW, athlete=None)

    assert all(day.target_hr_low is None for day in plan.days)
    assert plan.hr_source is None
    assert plan.hr_note is not None


def test_the_open_ends_of_a_zone_stay_open() -> None:
    friel = hr_zones_from_lthr(168)
    # Friel's zone 1 starts at zero, which is not a target.
    assert hr_window(1, zones=friel, hr_max=None) == (None, round(0.85 * 168) - 1)
    # Zone 5 has no ceiling unless HFmax is known.
    assert hr_window(5, zones=friel, hr_max=None) == (168, None)
    assert hr_window(5, zones=friel, hr_max=188) == (168, 188)


def test_the_percent_hr_max_model_gives_zone_1_a_lower_bound() -> None:
    zones = hr_zones_from_hr_max(190)

    assert hr_window(1, zones=zones, hr_max=190) == (95, round(0.60 * 190) - 1)


def test_weekdays_are_derived_from_the_dates_not_taken_from_the_answer() -> None:
    plan = resolve(parse(as_text(a_plan()), window=WINDOW), window=WINDOW, athlete=None)

    assert [day.weekday for day in plan.days] == [
        "Fr",
        "Sa",
        "So",
        "Mo",
        "Di",
        "Mi",
        "Do",
    ]
    assert plan.days[0].weekday_long == "Freitag"


def test_the_limitations_stay_their_own_list() -> None:
    plan = resolve(parse(as_text(a_plan()), window=WINDOW), window=WINDOW, athlete=None)

    assert plan.limitations == ("Die Formkurve braucht 42 Tage, vorliegen 8.",)
    for day in plan.days:
        assert "42 Tage" not in day.purpose
