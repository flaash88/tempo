"""The feature document the model is given.

This is the whole of what the AI layer knows. Three rules shape it, and all
three are enforced here rather than trusted to a prompt:

* **No raw streams, ever.** Nothing in this module reads
  ``activity_stream``. The per-second recording is what the metric engine
  is for; putting it in a context window would be expensive, useless and
  exactly the thing CLAUDE.md forbids.
* **No unfiltered activity list.** At most
  :data:`MAX_RECENT_ACTIVITIES` recent sessions, each as a handful of
  finished numbers, plus weekly totals for the shape of the block.
* **The confidence metadata travels with every metric.** A value the app
  will not show is a value the model must not interpret, and the only way
  it can know that is if ``have``, ``required``, ``confidence`` and
  ``stale`` arrive with the number.

The document is capped at :data:`MAX_FEATURE_BYTES`. If it does not fit, it
is trimmed in a fixed order and the document says it was trimmed, so a
short answer is never mistaken for a complete picture.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import math
from dataclasses import dataclass
from typing import Any, Final

from sqlalchemy import Engine, select

from tempo.ai.errors import FeaturesTooLarge
from tempo.db.models import Activity, AthleteSettings, DailyLoad, WellnessDay
from tempo.db.session import session_scope
from tempo.metrics import thresholds as metric_thresholds
from tempo.metrics.confidence import MetricResult
from tempo.reports import (
    ActivityReport,
    PerformanceReport,
    build_activity_report,
    build_performance_report,
    build_plan_report,
    build_today_extras,
)
from tempo.snapshot import Snapshot, build_snapshot

log = logging.getLogger(__name__)

# The plan's ceiling. A document this size is a page of numbers, which is
# what the model needs; anything larger is a sign that raw data crept in.
MAX_FEATURE_BYTES: Final = 4_096

# How many recent sessions travel as individual entries. Enough to see the
# shape of the last two weeks for someone training daily, and a hard stop
# that no amount of history can grow past.
MAX_RECENT_ACTIVITIES: Final = 10

# Weekly totals give the block its shape without listing anything.
RECENT_WEEKS: Final = 8

# Planned sessions from today forward.
PLAN_HORIZON_DAYS: Final = 7

# How many laps of the session under discussion travel with it. Enough for
# an interval session to be recognisable as one; beyond that the split
# list stops being a summary and starts being a stream.
MAX_LAPS: Final = 12

# What gets dropped first when the document is too large, in order. Each
# entry is a top-level key; the document records what went.
TRIM_ORDER: Final[tuple[str, ...]] = (
    "recent_activities",
    "weekly_volume",
    "performance",
    "plan",
)


@dataclass(frozen=True, slots=True)
class FeatureDocument:
    """The document, its serialisation and what it cost to fit."""

    payload: dict[str, Any]
    json_text: str
    trimmed: tuple[str, ...] = ()

    @property
    def size_bytes(self) -> int:
        return len(self.json_text.encode("utf-8"))


def envelope(result: MetricResult[Any], value: Any = None) -> dict[str, Any]:
    """A metric with the evidence behind it, in the compact wire form.

    ``available_from`` is omitted when there is nothing to wait for, and
    ``stale`` only appears when it is true — the document is read by a
    model, and every key that is always the same is a key that costs tokens
    without carrying information.
    """
    entry: dict[str, Any] = {
        "value": value if result.value is not None else None,
        "confidence": round(result.confidence, 3),
        "have": result.have,
        "required": result.required,
    }
    if result.value is None and result.available_from is not None:
        entry["available_from"] = result.available_from.isoformat()
    if result.last_data_point is not None:
        entry["last_data_point"] = result.last_data_point.isoformat()
    if result.stale:
        entry["stale"] = True
    return entry


def _baseline(result: MetricResult[Any]) -> dict[str, Any]:
    """A baseline, in the readings' own units and without naming the metric."""
    baseline = result.value
    if baseline is None:
        return envelope(result)
    if baseline.log_transformed:
        value = {
            "mean": round(math.exp(baseline.mean), 1),
            "lower": round(math.exp(baseline.lower), 1),
            "upper": round(math.exp(baseline.upper), 1),
        }
    else:
        value = {
            "mean": round(baseline.mean, 1),
            "lower": round(baseline.lower, 1),
            "upper": round(baseline.upper, 1),
        }
    value["days"] = baseline.days
    if baseline.source_field:
        # Carried, not interpreted: the model is told which field the
        # number came from and nothing about what the measure is.
        value["source_field"] = baseline.source_field
    return envelope(result, value)


def _form(result: MetricResult[Any]) -> dict[str, Any]:
    form = result.value
    if form is None:
        return envelope(result)
    return envelope(
        result,
        {
            "ctl": round(form.ctl, 1),
            "atl": round(form.atl, 1),
            "tsb": round(form.tsb, 1),
        },
    )


def _rounded(result: MetricResult[Any], digits: int = 2) -> dict[str, Any]:
    value = result.value
    if isinstance(value, int | float):
        return envelope(result, round(value, digits))
    return envelope(result, value)


def _athlete_profile(settings: AthleteSettings | None) -> dict[str, Any]:
    if settings is None:
        return {"configured": False}
    return {
        "configured": True,
        "hr_max": settings.hr_max,
        "hr_rest": settings.hr_rest,
        "lthr": settings.lthr,
        "threshold_pace_s_per_km": settings.threshold_pace_s_per_km,
        "zone_model": settings.zone_model,
    }


def _critical_speed(result: MetricResult[Any]) -> dict[str, Any]:
    value = result.value
    if value is None:
        return envelope(result)
    return envelope(
        result,
        {
            "cs_m_s": round(value.cs_m_s, 2),
            "d_prime_m": round(value.d_prime_m),
            "points": value.points,
        },
    )


def _performance(report: PerformanceReport) -> dict[str, Any]:
    """Best efforts and predictions, as finished numbers."""
    entry: dict[str, Any] = {
        "critical_speed": _critical_speed(report.critical_speed),
        "vdot": _rounded(report.vdot, 1),
    }
    if report.reference is not None:
        entry["reference_effort"] = {
            "duration_s": report.reference.duration_s,
            "distance_m": round(report.reference.distance_m),
            "date": (
                report.reference.date.isoformat() if report.reference.date else None
            ),
            "stale": report.predictions_stale,
        }
    if report.predictions:
        entry["predictions_s"] = {
            name: {method: round(seconds) for method, seconds in entries}
            for name, entries in report.predictions.items()
        }
    return entry


def build_features(
    engine: Engine,
    *,
    as_of: dt.date | None = None,
    snapshot: Snapshot | None = None,
    include: tuple[str, ...] | None = None,
    extra: dict[str, Any] | None = None,
) -> FeatureDocument:
    """Aggregate the metrics into the document the model is given.

    Everything here comes from the derived tables and the metric engine.
    The one place raw rows are read is the recent-session summary, and that
    reads the activity row — never its stream.
    """
    as_of = as_of or dt.datetime.now(tz=dt.UTC).date()
    snapshot = snapshot or build_snapshot(engine, as_of=as_of)
    extras = build_today_extras(engine, as_of=as_of)
    performance = build_performance_report(engine, as_of=as_of)
    plan = build_plan_report(
        engine,
        from_date=as_of,
        to_date=as_of + dt.timedelta(days=PLAN_HORIZON_DAYS),
    )

    with session_scope(engine) as session:
        athlete = session.get(AthleteSettings, 1)
        profile = _athlete_profile(athlete)
        recent = session.execute(
            select(
                Activity.start_local,
                Activity.sport,
                Activity.distance_m,
                Activity.moving_s,
                Activity.avg_hr,
            )
            .order_by(Activity.start_local.desc())
            .limit(MAX_RECENT_ACTIVITIES)
        ).all()
        loads = session.execute(
            select(DailyLoad.date, DailyLoad.distance_m, DailyLoad.duration_s)
            .where(DailyLoad.date > as_of - dt.timedelta(weeks=RECENT_WEEKS))
            .where(DailyLoad.date <= as_of)
        ).all()
        wellness_days = session.scalar(
            select(WellnessDay.date).order_by(WellnessDay.date.desc()).limit(1)
        )

    payload: dict[str, Any] = {
        "as_of": as_of.isoformat(),
        "athlete": profile,
        "metrics": {
            "readiness": envelope(snapshot.readiness, snapshot.readiness.value),
            "form": _form(snapshot.form),
            "acwr": _rounded(snapshot.acwr, 2),
            "hrv_baseline": _baseline(snapshot.hrv),
            "resting_hr_baseline": _baseline(snapshot.resting_hr),
            "sleep_s": envelope(extras.sleep, extras.sleep.value),
        },
        "readiness_inputs": {
            name: (None if value is None else round(value))
            for name, value in (
                ("hrv", snapshot.readiness_components.hrv),
                ("resting_hr", snapshot.readiness_components.resting_hr),
                ("sleep", snapshot.readiness_components.sleep),
                ("tsb", snapshot.readiness_components.tsb),
            )
        },
        "minimum_history": {
            key: item.required
            for key, item in metric_thresholds.MINIMUM_HISTORY.items()
        },
        "last_wellness_day": (
            wellness_days.isoformat() if isinstance(wellness_days, dt.date) else None
        ),
    }

    if extras.subjective is not None and any(
        value is not None
        for value in (
            extras.subjective.fatigue,
            extras.subjective.soreness,
            extras.subjective.mood,
        )
    ):
        # Raw and unlabelled: no scale is asserted here either.
        payload["subjective_today"] = {
            "fatigue": extras.subjective.fatigue,
            "soreness": extras.subjective.soreness,
            "mood": extras.subjective.mood,
            "source": extras.subjective.source,
            "scale_unverified": True,
        }

    payload["recent_activities"] = [
        {
            "date": start.date().isoformat(),
            "sport": sport,
            "distance_m": None if distance is None else round(distance),
            "moving_s": moving,
            "avg_hr": avg_hr,
        }
        for start, sport, distance, moving, avg_hr in recent
    ]

    weeks: dict[str, dict[str, float]] = {}
    for day, distance_m, duration_s in loads:
        if not isinstance(day, dt.date):
            continue
        monday = (day - dt.timedelta(days=day.weekday())).isoformat()
        bucket = weeks.setdefault(monday, {"distance_m": 0.0, "duration_s": 0.0})
        bucket["distance_m"] += distance_m or 0.0
        bucket["duration_s"] += duration_s or 0
    payload["weekly_volume"] = {
        monday: {
            "distance_m": round(totals["distance_m"]),
            "duration_s": int(totals["duration_s"]),
        }
        for monday, totals in sorted(weeks.items())
    }

    payload["performance"] = _performance(performance)
    payload["plan"] = [
        {
            "date": entry.date.isoformat(),
            "category": entry.category,
            "sport": entry.sport,
            "name": entry.name,
            "target_time_s": entry.target_time_s,
            "target_dist_m": (
                None if entry.target_dist_m is None else round(entry.target_dist_m)
            ),
        }
        for entry in (*plan.workouts, *plan.races)
    ]

    if include is not None:
        payload = {
            key: value
            for key, value in payload.items()
            if key in include or key in {"as_of", "metrics", "minimum_history"}
        }

    if extra:
        # Whatever the request is about is not subject to trimming: the
        # session being discussed cannot be the thing that gets dropped to
        # make room. TRIM_ORDER only names context.
        payload.update(extra)

    return _fit(payload)


def activity_block(
    engine: Engine, activity_id: str, *, as_of: dt.date | None = None
) -> dict[str, Any] | None:
    """The one session an activity request is about, as finished numbers.

    Built from the same report the detail screen uses, which means the
    stream has already been reduced to metrics before it gets here. The
    raw channels are never read in this module and are not read here.
    """
    report = build_activity_report(engine, activity_id, as_of=as_of)
    if report is None:
        return None
    return {"activity": _activity_entry(report)}


def _activity_entry(report: ActivityReport) -> dict[str, Any]:
    activity = report.activity
    entry: dict[str, Any] = {
        "id": activity.id,
        "date": activity.start_local.date().isoformat(),
        "sport": activity.sport,
        "distance_m": (
            None if activity.distance_m is None else round(activity.distance_m)
        ),
        "moving_s": activity.moving_s,
        "elevation_gain_m": (
            None
            if activity.elevation_gain_m is None
            else round(activity.elevation_gain_m)
        ),
        "avg_hr": activity.avg_hr,
        "max_hr": activity.max_hr,
        "avg_pace_s_per_km": (
            None
            if activity.avg_pace_s_per_km is None
            else round(activity.avg_pace_s_per_km)
        ),
        "avg_cadence_spm": report.avg_cadence_spm,
        "metrics": {
            "trimp": _rounded(report.trimp, 1),
            "hr_tss": _rounded(report.hr_tss, 1),
            "r_tss": _rounded(report.r_tss, 1),
            "gap_pace_s_per_km": _rounded(report.gap_pace, 0),
            "efficiency_factor": _rounded(report.efficiency_factor, 4),
            "decoupling_pct": _rounded(report.decoupling, 1),
        },
    }
    if report.zone_seconds:
        entry["zone_seconds"] = list(report.zone_seconds)
        entry["seconds_below_zone_1"] = report.seconds_below_zone_1
        entry["seconds_unknown"] = report.seconds_unknown
    if report.laps:
        if len(report.laps) <= MAX_LAPS:
            entry["laps"] = [
                {
                    "index": lap.index,
                    "distance_m": (
                        None if lap.distance_m is None else round(lap.distance_m)
                    ),
                    "duration_s": lap.duration_s,
                    "avg_hr": lap.avg_hr,
                    "avg_pace_s_per_km": (
                        None
                        if lap.avg_pace_s_per_km is None
                        else round(lap.avg_pace_s_per_km)
                    ),
                }
                for lap in report.laps
            ]
        else:
            # Named rather than silently dropped, so a session with sixty
            # splits does not read as one with none.
            entry["laps_omitted"] = len(report.laps)
    if report.planned is not None:
        entry["planned"] = {
            "name": report.planned.name,
            "category": report.planned.category,
            "target_time_s": report.planned.target_time_s,
            "target_dist_m": (
                None
                if report.planned.target_dist_m is None
                else round(report.planned.target_dist_m)
            ),
        }
    return entry


def _serialise(payload: dict[str, Any]) -> str:
    """Compact, with sorted keys so an identical document caches identically."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _fit(payload: dict[str, Any]) -> FeatureDocument:
    """Serialise, and trim in a fixed order until it fits.

    What was dropped is recorded in the document itself: a model reading a
    trimmed document has to know it is trimmed, or it will read the absence
    of the last two weeks as two weeks of rest.
    """
    text = _serialise(payload)
    if len(text.encode("utf-8")) <= MAX_FEATURE_BYTES:
        return FeatureDocument(payload=payload, json_text=text)

    trimmed: list[str] = []
    working = dict(payload)
    for key in TRIM_ORDER:
        if key not in working:
            continue
        working.pop(key)
        trimmed.append(key)
        working["omitted_for_size"] = list(trimmed)
        text = _serialise(working)
        if len(text.encode("utf-8")) <= MAX_FEATURE_BYTES:
            log.info("feature document trimmed", extra={"dropped": ",".join(trimmed)})
            return FeatureDocument(
                payload=working, json_text=text, trimmed=tuple(trimmed)
            )

    raise FeaturesTooLarge(
        f"feature document is {len(text.encode('utf-8'))} bytes after trimming "
        f"{', '.join(trimmed)}; the limit is {MAX_FEATURE_BYTES}"
    )
