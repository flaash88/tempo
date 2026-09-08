"""Confidence, history and staleness — the shape every metric answers in.

The rules live in CLAUDE.md; this is the one implementation of them, and
every other module in ``tempo.metrics`` goes through it. Nothing here
touches the database: it works on sequences of dates and numbers, so each
rule can be tested against a hand-written case.

Three ideas do the work:

* **The current window.** Not "all the history there is" but the unbroken
  run of data that reaches up to today. A run of days interrupted by more
  than :data:`~tempo.metrics.thresholds.BASELINE_RESET_GAP_DAYS` days
  without data is over, and so is one whose newest day is that far behind
  today. A block of 16 tracked days from three months ago is history, not
  a window, and it can never supply a current value.
* **Minimum history.** Below ``required`` days in that window there is no
  value — only ``have``, ``required`` and the date it could arrive.
* **Confidence.** How full the window is, multiplied by how recent it is.
  Both fall to zero on their own, and a value with no data behind it has
  confidence zero rather than a small number that invites reading.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Final

from tempo.metrics.thresholds import (
    BASELINE_RESET_GAP_DAYS,
    STALE_AFTER_DAYS,
)

# The window is broken by more than this many consecutive days with no data
# at all, and confidence decays to zero over the same span.
DEFAULT_MAX_GAP_DAYS: Final[int] = BASELINE_RESET_GAP_DAYS


@dataclass(frozen=True, slots=True)
class MetricResult[T]:
    """A value together with the evidence behind it.

    ``value`` is ``None`` whenever the window is too short. In that state
    ``have``, ``required`` and ``available_from`` describe the build-up, and
    ``last_data_point`` still names the newest data there is — that is what
    lets a tile say "Stand 03.06." instead of just going blank.
    """

    value: T | None = None
    confidence: float = 0.0
    days_of_history: int = 0
    have: int = 0
    required: int = 0
    last_data_point: dt.date | None = None
    available_from: dt.date | None = None
    stale: bool = False

    @property
    def is_available(self) -> bool:
        return self.value is not None

    @property
    def missing(self) -> int:
        """How many more data points the value needs."""
        return max(0, self.required - self.have)


def gap_days(earlier: dt.date, later: dt.date) -> int:
    """Days with no data between two dates, exclusive of both.

    Consecutive days have a gap of zero. Negative spans clamp to zero, so a
    date that is not actually earlier cannot produce a nonsense gap.
    """
    return max(0, (later - earlier).days - 1)


def days_since(day: dt.date, as_of: dt.date) -> int:
    """Age of a date in days. Today is zero; the future clamps to zero."""
    return max(0, (as_of - day).days)


def is_stale(last_data_point: dt.date | None, as_of: dt.date) -> bool:
    """True when the newest data point is too old to be the current state.

    No data at all is not stale — there is nothing to be out of date. That
    case is the empty state, which ``have == 0`` already says.
    """
    if last_data_point is None:
        return False
    return days_since(last_data_point, as_of) > STALE_AFTER_DAYS


def recency(last_data_point: dt.date | None, as_of: dt.date) -> float:
    """How current the newest data point is, from 1.0 down to 0.0.

    Falls linearly to zero over the baseline reset gap: at that age the
    reset rule discards the baseline anyway, so its contribution to
    confidence is nothing.
    """
    if last_data_point is None:
        return 0.0
    age = days_since(last_data_point, as_of)
    if age >= BASELINE_RESET_GAP_DAYS:
        return 0.0
    return 1.0 - age / BASELINE_RESET_GAP_DAYS


def window_fill(have: int, required: int) -> float:
    """How full the window is, from 0.0 to 1.0."""
    if required <= 0:
        return 1.0 if have > 0 else 0.0
    return min(1.0, max(0, have) / required)


def confidence_of(
    have: int, required: int, last_data_point: dt.date | None, as_of: dt.date
) -> float:
    """Window fill multiplied by recency, as CLAUDE.md defines it."""
    return window_fill(have, required) * recency(last_data_point, as_of)


def available_from(have: int, required: int, as_of: dt.date) -> dt.date | None:
    """The earliest date the value can exist, if data keeps coming daily.

    ``None`` once the requirement is met — there is nothing left to wait
    for.
    """
    missing = required - have
    if missing <= 0:
        return None
    return as_of + dt.timedelta(days=missing)


def current_window(
    days: Iterable[dt.date],
    *,
    as_of: dt.date,
    max_gap_days: int = DEFAULT_MAX_GAP_DAYS,
) -> tuple[dt.date, ...]:
    """The unbroken run of data days that reaches up to ``as_of``.

    Walks back from the newest day and stops at the first gap longer than
    ``max_gap_days``. Returns an empty tuple when the newest day is itself
    that far behind ``as_of`` — data that old cannot describe the present,
    and averaging across the gap to reach it is exactly what the reset rule
    forbids. Days after ``as_of`` are ignored.
    """
    ordered = sorted({day for day in days if day <= as_of})
    if not ordered:
        return ()

    newest = ordered[-1]
    if gap_days(newest, as_of) > max_gap_days:
        return ()

    start = 0
    for index in range(len(ordered) - 1, 0, -1):
        if gap_days(ordered[index - 1], ordered[index]) > max_gap_days:
            start = index
            break
    return tuple(ordered[start:])


def newest_of(days: Iterable[dt.date]) -> dt.date | None:
    """The newest of a set of days, or None when there are none."""
    ordered = sorted(days)
    return ordered[-1] if ordered else None


def tracked_window(
    wellness_days: Iterable[dt.date],
    activity_days: Iterable[dt.date],
    *,
    as_of: dt.date,
    max_gap_days: int = DEFAULT_MAX_GAP_DAYS,
) -> tuple[dt.date, ...]:
    """The window over which load history counts as history.

    A day counts as tracked if it carries wellness data or a session. This
    is what separates "the athlete rested for a week" from "the watch sat in
    a drawer for three months": a rest day with wellness data is real
    information and load zero on it is a fact, while a day with nothing at
    all is an absence. Only the second kind breaks the window, so a taper
    does not throw the fitness curve away and an untracked stretch does.
    """
    return current_window(
        list(wellness_days) + list(activity_days),
        as_of=as_of,
        max_gap_days=max_gap_days,
    )


@dataclass(frozen=True, slots=True)
class WindowState:
    """How long the current window was, as of one particular day."""

    date: dt.date
    length: int
    last_data_point: dt.date | None


def window_lengths_by_day(
    tracked_days: Iterable[dt.date],
    calendar: Sequence[dt.date],
    *,
    max_gap_days: int = DEFAULT_MAX_GAP_DAYS,
) -> tuple[WindowState, ...]:
    """The window length as of each day of a calendar, in one pass.

    ``fitness_day`` is a series, so each row has to carry the confidence that
    applied *on that day* rather than today's. Computing the window from
    scratch per day would be quadratic; this walks forward instead, growing
    the run while data keeps arriving and dropping it to zero once the gap
    since the last data point exceeds the limit.
    """
    tracked = set(tracked_days)
    states: list[WindowState] = []
    run = 0
    last: dt.date | None = None

    for day in calendar:
        if day in tracked:
            if last is not None and gap_days(last, day) > max_gap_days:
                run = 1
            else:
                run += 1
            last = day
        elif last is not None and gap_days(last, day) > max_gap_days:
            run = 0
        states.append(WindowState(date=day, length=run, last_data_point=last))
    return tuple(states)


def evaluate[T](
    value: T | None,
    *,
    have: int,
    required: int,
    as_of: dt.date,
    last_data_point: dt.date | None,
    days_of_history: int | None = None,
) -> MetricResult[T]:
    """Wrap a value in its evidence, withholding it below the minimum.

    Pass the value the formula produced; whether it is delivered is decided
    here, in one place, so no module has to remember the rule.
    """
    enough = have >= required
    return MetricResult(
        value=value if enough else None,
        confidence=(
            confidence_of(have, required, last_data_point, as_of)
            if enough and value is not None
            else 0.0
        ),
        days_of_history=have if days_of_history is None else days_of_history,
        have=have,
        required=required,
        last_data_point=last_data_point,
        available_from=available_from(have, required, as_of),
        stale=is_stale(last_data_point, as_of),
    )


def from_window[T](
    value: T | None,
    *,
    window: Sequence[dt.date],
    required: int,
    as_of: dt.date,
    newest_data_point: dt.date | None = None,
) -> MetricResult[T]:
    """``evaluate`` for the common case of a date window.

    ``newest_data_point`` overrides what the tile shows as the last data
    point. Pass it when the window came out empty but data exists further
    back: the window says there is no current value, the date says how far
    back the last one was.
    """
    last = window[-1] if window else newest_data_point
    return evaluate(
        value,
        have=len(window),
        required=required,
        as_of=as_of,
        last_data_point=last,
    )


def unavailable[T](
    value_type: type[T],
    *,
    have: int,
    required: int,
    as_of: dt.date,
    last_data_point: dt.date | None = None,
) -> MetricResult[T]:
    """A metric that has no value, with its progress spelled out.

    ``value_type`` only fixes what the result would have held, so callers do
    not have to annotate the empty case at every site.
    """
    del value_type
    return evaluate(
        None,
        have=have,
        required=required,
        as_of=as_of,
        last_data_point=last_data_point,
    )
