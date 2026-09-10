"""The week plan, as structure rather than prose.

A week of training is a list of days, and a list of days rendered as one
block of prose is unreadable on a phone: the Tuesday disappears into the
Monday. So the model answers with JSON, and the answer is *checked* rather
than hoped for — a prompt that asks nicely produces the right shape most
of the time, and "most of the time" is not a contract an interface can be
built on.

Two things are deliberately kept away from the model.

**The dates.** The seven days of the coming week are computed here and
handed over; an answer whose dates are not exactly those seven, in order,
is a format error. The model chooses what happens on a day, never which
days there are.

**The heart rates.** The model picks a zone; the target heart rate for
that zone comes from :mod:`tempo.metrics.zones` and the athlete's own
threshold values. That is the rule from CLAUDE.md — the AI computes
nothing — and it is also the only way the numbers on the screen agree
with the numbers everywhere else in the app.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Final, Literal

from pydantic import BaseModel, Field, ValidationError

from tempo.db.models import AthleteSettings
from tempo.metrics.zones import ZoneSet, zones_for_athlete

log = logging.getLogger(__name__)

# A week is seven days. The plan starts the day after the day it is asked
# for: "die kommende Woche" is the next seven days, not the remainder of
# this one.
PLAN_DAYS: Final = 7

# The rationale is a paragraph, not an essay. Three sentences is what the
# design's block has room for, so three is what is enforced.
MAX_RATIONALE_SENTENCES: Final = 3

# What a day may say about itself, in characters.
MAX_TITLE_CHARS: Final = 60
MAX_PURPOSE_CHARS: Final = 300
MAX_LIMITATION_CHARS: Final = 300
MAX_LIMITATIONS: Final = 6

WEEKDAYS_SHORT: Final[tuple[str, ...]] = (
    "Mo",
    "Di",
    "Mi",
    "Do",
    "Fr",
    "Sa",
    "So",
)
WEEKDAYS_LONG: Final[tuple[str, ...]] = (
    "Montag",
    "Dienstag",
    "Mittwoch",
    "Donnerstag",
    "Freitag",
    "Samstag",
    "Sonntag",
)

ZONE_LABELS: Final[tuple[str, ...]] = ("Z1", "Z2", "Z3", "Z4", "Z5")

# A sentence ends at one of these. Crude on purpose: it is a length check,
# not a parser, and it only has to stop an essay from arriving in a field
# the design gives three lines to.
_SENTENCE_END = re.compile(r"[.!?](?:\s|$)")

# The model sometimes wraps JSON in a fence despite being asked not to.
# Stripping one is not the same as accepting free prose: what is inside
# still has to validate.
_FENCE = re.compile(r"^```(?:json)?\s*(.*?)\s*```$", re.DOTALL)


class PlanFormatProblem(Exception):
    """The answer did not have the shape the plan endpoint requires.

    Carries a German sentence, because it is shown to the athlete when
    even the second attempt fails, and an English stack trace on a German
    screen is a bug of its own.
    """


# --- what the model is allowed to say ----------------------------------


class ModelPlanDay(BaseModel):
    """One day, exactly as the model may write it."""

    model_config = {"extra": "forbid"}

    date: dt.date
    kind: Literal["session", "rest"]
    title: str = Field(min_length=1, max_length=MAX_TITLE_CHARS)
    duration_min: int | None = Field(default=None, ge=10, le=360)
    zone: Literal["Z1", "Z2", "Z3", "Z4", "Z5"] | None = None
    purpose: str = Field(min_length=1, max_length=MAX_PURPOSE_CHARS)


class ModelWeekPlan(BaseModel):
    """The whole answer, as the model may write it."""

    model_config = {"extra": "forbid"}

    rationale: str = Field(min_length=1, max_length=800)
    days: list[ModelPlanDay] = Field(min_length=PLAN_DAYS, max_length=PLAN_DAYS)
    limitations: list[str] = Field(default_factory=list, max_length=MAX_LIMITATIONS)


# --- what the API hands on ---------------------------------------------


@dataclass(frozen=True, slots=True)
class PlanDay:
    """One day of the plan, with what the server derived added to it."""

    date: dt.date
    weekday: str
    weekday_long: str
    kind: str
    title: str
    duration_s: int | None
    zone: int | None
    zone_label: str | None
    # The zone's heart rate window. Either end may be missing: Friel's
    # zone 1 has no lower bound worth showing, and zone 5 has no upper one
    # unless the athlete's HFmax is known.
    target_hr_low: int | None
    target_hr_high: int | None
    purpose: str


@dataclass(frozen=True, slots=True)
class WeekPlan:
    """A checked week plan."""

    from_date: dt.date
    to_date: dt.date
    rationale: str
    days: tuple[PlanDay, ...]
    limitations: tuple[str, ...]
    # Which zone model the heart rates came from, or None when the
    # athlete's thresholds are not configured and there are none.
    hr_source: str | None
    hr_note: str | None


def plan_window(as_of: dt.date) -> tuple[dt.date, ...]:
    """The next Monday-to-Sunday week that can still be trained in full.

    A calendar week, not a rolling seven days, and the reason is that the
    rest of the application already counts in Monday-anchored weeks —
    ``GET /api/plan`` defaults to one, the weekly volume in Trends is one.
    A plan that straddled two of them could never be held against
    "Wochenlast im Rahmen der bisherigen Belastung", because no week the
    app computes would have the plan's boundaries.

    Which week: the coming one, except on a Monday, when the week the
    athlete is standing in is still entirely ahead and skipping it would
    mean planning eight days out. Nothing in the window is ever in the
    past, which matters because a day in the past cannot be adopted.
    """
    # 0 on a Monday, otherwise the days remaining until the next one.
    ahead = -as_of.weekday() % 7
    start = as_of + dt.timedelta(days=ahead)
    return tuple(start + dt.timedelta(days=offset) for offset in range(PLAN_DAYS))


# --- parsing and checking ----------------------------------------------


def parse(text: str, *, window: tuple[dt.date, ...]) -> ModelWeekPlan:
    """Read one answer, or say in German what is wrong with it.

    Everything :class:`ModelWeekPlan` cannot express is checked after it:
    the dates against the window, the rationale's length in sentences, and
    the agreement between a day's kind and the fields it filled in.
    """
    payload = _json_object(text)
    try:
        plan = ModelWeekPlan.model_validate(payload)
    except ValidationError as exc:
        raise PlanFormatProblem(_first_error(exc)) from exc

    _check_rationale(plan.rationale)
    _check_dates(plan.days, window)
    for day in plan.days:
        _check_day(day)
    for limitation in plan.limitations:
        if len(limitation) > MAX_LIMITATION_CHARS:
            raise PlanFormatProblem(
                'Ein Eintrag in "limitations" ist länger als '
                f"{MAX_LIMITATION_CHARS} Zeichen."
            )
    return plan


def _json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fenced = _FENCE.match(stripped)
    if fenced:
        stripped = fenced.group(1).strip()
    try:
        payload = json.loads(stripped)
    except ValueError as exc:
        raise PlanFormatProblem(
            "Die Antwort war kein JSON. Antworte mit genau einem "
            "JSON-Objekt, ohne Text davor oder danach."
        ) from exc
    if not isinstance(payload, dict):
        raise PlanFormatProblem("Die Antwort war kein JSON-Objekt.")
    return payload


def _first_error(exc: ValidationError) -> str:
    error = exc.errors()[0]
    where = ".".join(str(part) for part in error["loc"]) or "(Wurzel)"
    return f"Feld {where}: {error['msg']}."


def _check_rationale(rationale: str) -> None:
    sentences = len(_SENTENCE_END.findall(rationale.strip()))
    if sentences > MAX_RATIONALE_SENTENCES:
        raise PlanFormatProblem(
            f'"rationale" hat {sentences} Sätze, erlaubt sind höchstens '
            f"{MAX_RATIONALE_SENTENCES}."
        )


def _check_dates(days: list[ModelPlanDay], window: tuple[dt.date, ...]) -> None:
    given = tuple(day.date for day in days)
    if given != window:
        wanted = ", ".join(day.isoformat() for day in window)
        raise PlanFormatProblem(
            f"Die Tage müssen genau diese Daten in dieser Reihenfolge haben: {wanted}."
        )


def _check_day(day: ModelPlanDay) -> None:
    where = day.date.isoformat()
    if day.kind == "session":
        if day.duration_min is None or day.zone is None:
            raise PlanFormatProblem(
                f'{where}: eine Einheit braucht "duration_min" und "zone".'
            )
    elif day.duration_min is not None or day.zone is not None:
        raise PlanFormatProblem(
            f'{where}: ein Ruhetag hat weder "duration_min" noch "zone".'
        )
    if len(_SENTENCE_END.findall(day.purpose.strip())) > 1:
        raise PlanFormatProblem(f'{where}: "purpose" ist genau ein Satz.')


def check(text: str, *, window: tuple[dt.date, ...]) -> str | None:
    """The validator the AI service calls. Returns the problem, or None."""
    try:
        parse(text, window=window)
    except PlanFormatProblem as problem:
        return str(problem)
    return None


# --- turning it into what the screen draws -----------------------------


def resolve(
    plan: ModelWeekPlan,
    *,
    window: tuple[dt.date, ...],
    athlete: AthleteSettings | None,
) -> WeekPlan:
    """Add what the server knows: weekdays, and the zones' heart rates."""
    zones = zones_for_athlete(
        lthr=athlete.lthr if athlete else None,
        hr_max=athlete.hr_max if athlete else None,
        zone_model=athlete.zone_model if athlete else None,
    )
    hr_max = athlete.hr_max if athlete else None

    days = tuple(_resolve_day(day, zones=zones, hr_max=hr_max) for day in plan.days)
    return WeekPlan(
        from_date=window[0],
        to_date=window[-1],
        rationale=plan.rationale.strip(),
        days=days,
        limitations=tuple(entry.strip() for entry in plan.limitations if entry.strip()),
        hr_source=zones.model if zones else None,
        hr_note=(
            None
            if zones
            else (
                "Zielherzfrequenzen fehlen, solange Schwellen-HF oder HFmax "
                "nicht in den Einstellungen hinterlegt sind."
            )
        ),
    )


def _resolve_day(
    day: ModelPlanDay, *, zones: ZoneSet | None, hr_max: int | None
) -> PlanDay:
    zone = ZONE_LABELS.index(day.zone) + 1 if day.zone else None
    low, high = hr_window(zone, zones=zones, hr_max=hr_max)
    weekday = day.date.weekday()
    return PlanDay(
        date=day.date,
        weekday=WEEKDAYS_SHORT[weekday],
        weekday_long=WEEKDAYS_LONG[weekday],
        kind=day.kind,
        title=day.title.strip(),
        duration_s=None if day.duration_min is None else day.duration_min * 60,
        zone=zone,
        zone_label=day.zone,
        target_hr_low=low,
        target_hr_high=high,
        purpose=day.purpose.strip(),
    )


def hr_window(
    zone: int | None, *, zones: ZoneSet | None, hr_max: int | None
) -> tuple[int | None, int | None]:
    """The heart rate window of a zone, from the athlete's own bounds.

    The open ends are left open rather than filled with a plausible
    number. Friel's zone 1 starts at zero, which is not a target, and
    zone 5 has no ceiling other than HFmax — and only when that is known.
    """
    if zone is None or zones is None or zones.kind != "hr":
        return None, None
    lower = zones.lower_bounds[zone - 1]
    low = round(lower) if lower > 0 else None
    if zone < len(zones.lower_bounds):
        return low, round(zones.lower_bounds[zone]) - 1
    return low, hr_max


# --- the contract the model is held to ---------------------------------


def format_contract(window: tuple[dt.date, ...]) -> str:
    """The block that says what the answer has to look like.

    Built per request because it carries the week's dates, which is the
    point: the model is not asked to work out which seven days it is
    planning, it is told, and an answer that says otherwise is rejected.
    """
    dates = "\n".join(
        f'    {{"date": "{day.isoformat()}", …}}   {WEEKDAYS_LONG[day.weekday()]}'
        for day in window
    )
    return f"""\
Antwortformat — dieser Block geht der Stilregel vor. Deine gesamte \
Antwort ist genau ein JSON-Objekt: kein Text davor, kein Text danach, \
kein Code-Zaun.

{{
  "rationale": "…",
  "days": [ … ],
  "limitations": [ "…" ]
}}

"rationale": höchstens {MAX_RATIONALE_SENTENCES} Sätze zur Begründung der \
Woche als Ganzes.

"days": genau {PLAN_DAYS} Einträge, in genau dieser Reihenfolge und mit \
genau diesen Daten:
{dates}

Ein Eintrag hat diese Felder und keine weiteren:
  "date"          das Datum aus der Liste oben
  "kind"          "session" für eine Einheit, "rest" für einen Ruhetag
  "title"         kurze Bezeichnung, höchstens {MAX_TITLE_CHARS} Zeichen,
                  etwa "Lockerer Dauerlauf" oder "Ruhetag"
  "duration_min"  Dauer in Minuten als Ganzzahl bei "session",
                  null bei "rest"
  "zone"          "Z1" bis "Z5" bei "session", null bei "rest"
  "purpose"       der Zweck in genau einem Satz

Zielherzfrequenzen trägst du nicht ein. Tempo rechnet sie aus der Zone \
und den Schwellenwerten des Athleten; eine Herzfrequenz aus deiner Feder \
wäre eine gerechnete Zahl und damit nicht deine Aufgabe. Dasselbe gilt \
für Distanzen und Pace-Vorgaben — Dauer und Zone genügen.

"limitations": was die Datenlage nicht hergibt — fehlende Historie, \
veraltete Werte, Kennzahlen unter ihrer Mindesthistorie — je Punkt ein \
kurzer Satz, höchstens {MAX_LIMITATIONS} Punkte. Diese Hinweise stehen \
ausschließlich hier und nicht in "rationale" oder "purpose". Gibt es \
nichts anzumerken, ist die Liste leer."""
