"""Response schemas.

The metric envelope is the contract for the whole API: no endpoint returns
a bare number. Every value arrives with the evidence behind it, so the
interface can tell "63" apart from "63, but from a three month old block"
and from "not yet, 34 of 42 days".

The field lists follow ``design/README.md``: each screen's endpoint carries
what that screen displays. Where a mock-up shows a threshold in its text
("mindestens 7 Nächte"), the number is not in the response text — it is in
``GET /api/thresholds``, and the frontend fills the sentence at runtime.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import BaseModel, Field

from tempo.config import MAX_CHAT_MESSAGE_CHARS, MAX_CHAT_TURNS


class MetricEnvelope[T](BaseModel):
    """A single metric together with how much it can be trusted."""

    value: T | None = Field(
        default=None,
        description="None while the minimum history is not reached.",
    )
    confidence: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Window fill ratio multiplied by recency.",
    )
    days_of_history: int = Field(
        default=0,
        ge=0,
        description="Days in the current unbroken window.",
    )
    have: int = Field(default=0, ge=0)
    required: int = Field(default=0, ge=0)
    available_from: dt.date | None = Field(
        default=None,
        description="Earliest date the value can exist, if data continues.",
    )
    last_data_point: dt.date | None = None
    stale: bool = Field(
        default=False,
        description="Last data point older than the staleness threshold.",
    )


class HealthResponse(BaseModel):
    """Liveness answer. Deliberately free of any athlete data."""

    status: Literal["ok"] = "ok"
    version: str
    database: Literal["ok", "missing", "error"]
    schema_revision: str | None = Field(
        default=None,
        description="Applied Alembic revision, None if not migrated yet.",
    )


# --- authentication ----------------------------------------------------


class LoginRequest(BaseModel):
    password: str = Field(min_length=1)


class SessionResponse(BaseModel):
    """Whether the caller has a session. Never carries a token."""

    authenticated: bool
    # False when no password hash is configured, so the interface can say
    # "finish the setup" instead of "wrong password".
    configured: bool = True


# --- shared value objects ----------------------------------------------


class BaselineValue(BaseModel):
    """A rolling baseline and the band around it, in the reading's units."""

    mean: float
    lower: float
    upper: float
    sd: float
    days: int
    # Which field of the source the readings came from. Carried, never
    # interpreted: nothing here asserts which HRV measure this is.
    source_field: str | None = None


class FormValue(BaseModel):
    ctl: float
    atl: float
    tsb: float


class ZoneBounds(BaseModel):
    """Five zones as ascending lower bounds, in the value's own unit."""

    kind: Literal["hr", "speed"]
    model: str
    lower_bounds: list[float]


class ZoneShare(BaseModel):
    zone: int = Field(ge=1, le=5)
    seconds: int = Field(ge=0)


class SyncSourceStatus(BaseModel):
    source: str
    status: str | None = None
    last_success_at: dt.datetime | None = None
    last_activity_start: dt.datetime | None = None
    last_wellness_date: dt.date | None = None
    started_at: dt.datetime | None = None
    finished_at: dt.datetime | None = None
    detail: str | None = None
    # True while a run of this source is in flight.
    running: bool = False


class SyncStatusResponse(BaseModel):
    sources: list[SyncSourceStatus]
    # When the next incremental run is allowed, given the hourly guard.
    next_allowed_at: dt.datetime | None = None


class SyncStartedResponse(BaseModel):
    started: bool
    detail: str


# --- today -------------------------------------------------------------


class SleepValue(BaseModel):
    seconds: int
    score: int | None = None


class SubjectiveDay(BaseModel):
    """How a day felt, as it was entered. Raw, and read by nothing.

    No scale is asserted and no direction is implied: what a 2 means, and
    whether it is better or worse than a 3, depends on the source and has
    not been confirmed against one. ``source`` says where the three values
    came from so a later calibration can tell an athlete's own entry from a
    synced one.
    """

    date: dt.date
    fatigue: int | None = None
    soreness: int | None = None
    mood: int | None = None
    source: str | None = None

    @property
    def is_empty(self) -> bool:
        return all(value is None for value in (self.fatigue, self.soreness, self.mood))


class SubjectiveUpdate(BaseModel):
    """What the athlete may record about a day.

    The bounds are a plausibility check and nothing more: they keep a typo
    or a stray zero out of the column without claiming to know the scale.
    A field left out stays as it is; a field sent as null is cleared.
    """

    model_config = {"extra": "forbid"}

    fatigue: int | None = Field(default=None, ge=1, le=10)
    soreness: int | None = Field(default=None, ge=1, le=10)
    mood: int | None = Field(default=None, ge=1, le=10)


class WorkoutSyncState(BaseModel):
    """How far one session has got towards the watch.

    The states the Plan screen draws: not transferred, transferring, on the
    watch, changed since it was transferred, and failed. ``confirmed_at``
    is here too, because the interface has to tell "ready to send" from
    "still a suggestion" — nothing goes out without it.
    """

    status: Literal["not_sent", "sending", "on_watch", "outdated", "failed"]
    confirmed_at: dt.datetime | None = None
    synced_at: dt.datetime | None = None
    # Why the last attempt failed. Never carries a credential.
    error: str | None = None
    # False for a session the source owns: it is already where it came
    # from, and Tempo does not write it anywhere.
    sendable: bool = False


class PlannedWorkoutSummary(BaseModel):
    """One calendar entry, as the plan and today screens show it."""

    id: str
    date: dt.date
    category: str | None = None
    sport: str | None = None
    name: str | None = None
    description: str | None = None
    target_time_s: int | None = None
    target_dist_m: float | None = None
    target_load: float | None = None
    workout_doc: dict[str, Any] | None = None
    external_id: str | None = None
    # True when an activity of the same sport is already recorded that day.
    done: bool = False
    source: str = "intervals"
    sync: WorkoutSyncState


class TodayResponse(BaseModel):
    """The Heute screen: one tile per metric, each with its own state."""

    date: dt.date
    readiness: MetricEnvelope[int]
    readiness_components: dict[str, float | None]
    readiness_weights: dict[str, float]
    hrv: MetricEnvelope[BaselineValue]
    hrv_latest: float | None = None
    hrv_source_field: str | None = None
    resting_hr: MetricEnvelope[BaselineValue]
    resting_hr_latest: int | None = None
    sleep: MetricEnvelope[SleepValue]
    # Raw and uninterpreted; nothing in the metric engine reads them.
    subjective: SubjectiveDay | None = None
    form: MetricEnvelope[FormValue]
    acwr: MetricEnvelope[float]
    planned: PlannedWorkoutSummary | None = None
    # The training block this week sits in, when the calendar says so.
    plan_context: str | None = None
    sync: SyncStatusResponse
    # Rendered from the configuration, never hard coded in the interface.
    ai_model: str


# --- activities --------------------------------------------------------


class ActivityListItem(BaseModel):
    id: str
    start_local: dt.datetime
    sport: str
    distance_m: float | None = None
    moving_s: int | None = None
    avg_hr: int | None = None
    avg_pace_s_per_km: float | None = None
    has_stream: bool = False


class ActivityListResponse(BaseModel):
    activities: list[ActivityListItem]
    total: int
    limit: int
    offset: int


class LapItem(BaseModel):
    index: int
    distance_m: float | None = None
    duration_s: int | None = None
    avg_hr: int | None = None
    avg_pace_s_per_km: float | None = None
    # Difference to the planned pace, where the calendar has one.
    pace_delta_s_per_km: float | None = None


class ActivityDetailResponse(BaseModel):
    """The Aktivitätsdetail screen."""

    id: str
    start_local: dt.datetime
    sport: str
    distance_m: float | None = None
    moving_s: int | None = None
    elapsed_s: int | None = None
    elevation_gain_m: float | None = None
    avg_hr: int | None = None
    max_hr: int | None = None
    avg_pace_s_per_km: float | None = None
    source: str
    has_stream: bool = False

    # Derived, each with its own evidence.
    trimp: MetricEnvelope[float]
    hr_tss: MetricEnvelope[float]
    r_tss: MetricEnvelope[float]
    gap_pace_s_per_km: MetricEnvelope[float]
    efficiency_factor: MetricEnvelope[float]
    decoupling: MetricEnvelope[float]
    avg_cadence_spm: int | None = None

    zones: list[ZoneShare] = Field(default_factory=list)
    zone_bounds: ZoneBounds | None = None
    seconds_below_zone_1: int = 0
    seconds_unknown: int = 0
    laps: list[LapItem] = Field(default_factory=list)
    planned: PlannedWorkoutSummary | None = None


class StreamsResponse(BaseModel):
    """The chart data. Gaps stay gaps: null means not recorded."""

    activity_id: str
    resolution_s: int = Field(
        ge=1, description="Seconds per returned sample after decimation."
    )
    samples: int
    fields: list[str]
    offset_s: list[int]
    series: dict[str, list[float | None]]


# --- trends ------------------------------------------------------------


class FitnessPointOut(BaseModel):
    date: dt.date
    load: float | None = None
    ctl: float | None = None
    atl: float | None = None
    tsb: float | None = None
    confidence: float = 0.0
    days_of_history: int = 0


class WeekVolume(BaseModel):
    week_start: dt.date
    distance_m: float
    duration_s: int
    load: float | None = None
    zones: list[ZoneShare] = Field(default_factory=list)


class BaselinePoint(BaseModel):
    date: dt.date
    value: float
    mean: float | None = None
    lower: float | None = None
    upper: float | None = None


class TrendsResponse(BaseModel):
    """The Trends screen."""

    window: Literal["6w", "12w", "52w"]
    from_date: dt.date
    to_date: dt.date
    fitness: list[FitnessPointOut] = Field(default_factory=list)
    weeks: list[WeekVolume] = Field(default_factory=list)
    hrv: MetricEnvelope[BaselineValue]
    hrv_series: list[BaselinePoint] = Field(default_factory=list)
    hrv_source_field: str | None = None
    resting_hr: MetricEnvelope[BaselineValue]
    resting_hr_series: list[BaselinePoint] = Field(default_factory=list)
    vo2max: MetricEnvelope[float]
    vdot: MetricEnvelope[float]
    monotony: MetricEnvelope[float]
    strain: MetricEnvelope[float]
    average_week_distance_m: float | None = None


# --- performance -------------------------------------------------------


class BestEffortOut(BaseModel):
    duration_s: int
    distance_m: float
    speed_m_s: float
    pace_s_per_km: float
    activity_id: str | None = None
    date: dt.date | None = None


class CriticalSpeedValue(BaseModel):
    cs_m_s: float
    cs_pace_s_per_km: float
    d_prime_m: float
    points: int
    r_squared: float


class PredictionOut(BaseModel):
    method: Literal["riegel", "vdot"]
    seconds: float
    pace_s_per_km: float


class RacePrediction(BaseModel):
    """Both methods side by side, never averaged."""

    distance_m: float
    predictions: list[PredictionOut]
    # The effort the prediction rests on, and whether it has aged out.
    based_on_date: dt.date | None = None
    stale: bool = False


class PerformanceResponse(BaseModel):
    """The performance half of the Trends screen."""

    best_efforts: list[BestEffortOut] = Field(default_factory=list)
    critical_speed: MetricEnvelope[CriticalSpeedValue]
    vdot: MetricEnvelope[float]
    predictions: dict[str, RacePrediction] = Field(default_factory=dict)
    # Which effort the predictions were derived from.
    reference_distance_m: float | None = None
    reference_seconds: float | None = None
    reference_date: dt.date | None = None
    predictions_stale: bool = False
    prediction_stale_after_days: int


# --- plan --------------------------------------------------------------


class PlanResponse(BaseModel):
    """The Plan screen."""

    from_date: dt.date
    to_date: dt.date
    workouts: list[PlannedWorkoutSummary] = Field(default_factory=list)
    planned_distance_m: float = 0.0
    planned_duration_s: int = 0
    planned_load: float | None = None
    # Entries whose category is a target race rather than a session.
    races: list[PlannedWorkoutSummary] = Field(default_factory=list)


# --- settings ----------------------------------------------------------


class CredentialStatus(BaseModel):
    """Whether a key is configured, and which one it is. Never the key.

    ``last4`` is there so the athlete can recognise which key is in place
    without the API ever handing back something that could be extended into
    the key itself — not even a masked form of it.
    """

    valid: bool
    last4: str | None = None


class AiUsage(BaseModel):
    month: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_eur: float = 0.0
    budget_eur: float = 0.0
    calls: int = 0


class SettingsResponse(BaseModel):
    """The Einstellungen screen."""

    hr_max: int | None = None
    hr_rest: int | None = None
    lthr: int | None = None
    threshold_pace_s_per_km: float | None = None
    sleep_target_s: int | None = None
    zone_model: str
    hr_zones: ZoneBounds | None = None
    pace_zones: ZoneBounds | None = None
    updated_at: dt.datetime | None = None

    intervals: CredentialStatus
    anthropic: CredentialStatus
    intervals_athlete_id: str
    ai_model_daily: str
    ai_model_planning: str
    ai_usage: AiUsage
    garmin_direct_enabled: bool
    readiness_weights: dict[str, float]
    # Files in the volume that no activity refers to — dropped in by hand,
    # or left behind by a sync that failed after the download. Not a count
    # of stored files: those all belong to an activity.
    fit_files_pending: int = 0
    sync: SyncStatusResponse


# --- the AI layer ------------------------------------------------------


class AiBudgetState(BaseModel):
    """The month's spend against its limit.

    Travels with every AI answer, including the refused one: the interface
    shows the same tile either way, and the only difference is whether it
    reads "noch 4,10 EUR" or "Budget erreicht".
    """

    month: str
    spent_eur: float
    budget_eur: float
    remaining_eur: float
    exhausted: bool


class AiAnswerResponse(BaseModel):
    """One interpretation, and what produced it.

    ``model`` is the model that actually answered, taken from the response
    rather than from the request, because the footer in the interface names
    the model and must not name one that did not write the text.
    """

    task: str
    text: str
    model: str
    created_at: dt.datetime
    # True when nothing was sent: the same numbers had already been asked
    # about, so the stored answer stands.
    cached: bool
    budget: AiBudgetState
    features_bytes: int
    features_trimmed: list[str] = Field(
        default_factory=list,
        description="Context dropped to fit the size limit, in the order dropped.",
    )
    cost_eur: float = 0.0


class WorkoutProposalRequest(BaseModel):
    """A session Tempo suggests. Plausibility only — no coaching here."""

    model_config = {"extra": "forbid"}

    date: dt.date
    sport: str = Field(default="Run", max_length=32)
    name: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=4_000)
    target_time_s: int | None = Field(default=None, gt=0, le=86_400)
    target_dist_m: float | None = Field(default=None, gt=0, le=1_000_000)
    target_load: float | None = Field(default=None, ge=0, le=1_000)
    workout_doc: dict[str, Any] | None = None


class WorkoutPushResponse(BaseModel):
    """The result of one transfer attempt."""

    workout: PlannedWorkoutSummary
    # False when nothing was sent because nothing had changed.
    sent: bool
    detail: str | None = None


class AiBudgetError(BaseModel):
    """The defined state of a spent budget. Not a fault."""

    detail: str
    budget: AiBudgetState


class ChatTurn(BaseModel):
    """One earlier message. Context, never a source of numbers."""

    model_config = {"extra": "forbid"}

    role: Literal["user", "assistant"]
    text: str = Field(min_length=1, max_length=MAX_CHAT_MESSAGE_CHARS)


class AiChatRequest(BaseModel):
    model_config = {"extra": "forbid"}

    question: str = Field(min_length=1, max_length=MAX_CHAT_MESSAGE_CHARS)
    history: list[ChatTurn] = Field(
        default_factory=list,
        max_length=MAX_CHAT_TURNS,
        description=(
            "Earlier turns, oldest first. Capped again server-side by the "
            "configured limits and by a hard ceiling on the whole history."
        ),
    )


class SettingsUpdate(BaseModel):
    """What may be changed. No credentials: those live in the environment."""

    model_config = {"extra": "forbid"}

    hr_max: int | None = Field(default=None, ge=100, le=250)
    hr_rest: int | None = Field(default=None, ge=25, le=120)
    lthr: int | None = Field(default=None, ge=80, le=230)
    threshold_pace_s_per_km: float | None = Field(default=None, gt=120, lt=1200)
    sleep_target_s: int | None = Field(default=None, ge=3600, le=57600)
    zone_model: Literal["friel_run_lthr", "percent_hr_max"] | None = None
