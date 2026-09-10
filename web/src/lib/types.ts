/** The shapes the API sends. Mirrors tempo/api/schemas.py. */

export type MetricEnvelope<T> = {
  value: T | null;
  confidence: number;
  days_of_history: number;
  have: number;
  required: number;
  available_from: string | null;
  last_data_point: string | null;
  stale: boolean;
};

export type BaselineValue = {
  mean: number;
  lower: number;
  upper: number;
  days: number;
  log_transformed: boolean;
  source_field: string | null;
};

export type FormValue = { ctl: number; atl: number; tsb: number };
export type SleepValue = { seconds: number };

export type SubjectiveDay = {
  date: string;
  fatigue: number | null;
  soreness: number | null;
  mood: number | null;
  source: string | null;
};

export type WorkoutSyncState = {
  status: "not_sent" | "sending" | "on_watch" | "outdated" | "failed";
  confirmed_at: string | null;
  synced_at: string | null;
  error: string | null;
  sendable: boolean;
};

export type PlannedWorkout = {
  id: string;
  date: string;
  category: string | null;
  sport: string | null;
  name: string | null;
  description: string | null;
  target_time_s: number | null;
  target_dist_m: number | null;
  target_load: number | null;
  external_id: string | null;
  done: boolean;
  source: string;
  sync: WorkoutSyncState;
};

export type SyncSourceStatus = {
  source: string;
  status: string | null;
  last_success_at: string | null;
  last_activity_start: string | null;
  last_wellness_date: string | null;
  started_at: string | null;
  finished_at: string | null;
  detail: string | null;
  running: boolean;
};

export type SyncStatus = {
  sources: SyncSourceStatus[];
  next_allowed_at: string | null;
};

export type TodayResponse = {
  date: string;
  readiness: MetricEnvelope<number>;
  readiness_components: Record<string, number | null>;
  readiness_weights: Record<string, number>;
  hrv: MetricEnvelope<BaselineValue>;
  hrv_latest: number | null;
  hrv_source_field: string | null;
  resting_hr: MetricEnvelope<BaselineValue>;
  resting_hr_latest: number | null;
  sleep: MetricEnvelope<SleepValue>;
  subjective: SubjectiveDay | null;
  form: MetricEnvelope<FormValue>;
  acwr: MetricEnvelope<number>;
  planned: PlannedWorkout | null;
  plan_context: string | null;
  sync: SyncStatus;
  ai_model: string;
};

export type MinimumHistory = {
  required: number;
  unit: string;
  label_de: string;
};

export type ThresholdsResponse = {
  minimum_history: Record<string, MinimumHistory>;
  stale_after_days: number;
  baseline_reset_gap_days: number;
  readiness: {
    weights: Record<string, number>;
    bands?: Record<string, number>;
    [key: string]: unknown;
  };
  performance: { prediction_stale_after_days: number; [key: string]: unknown };
  zones: { count: number; [key: string]: unknown };
  [key: string]: unknown;
};

export type ActivityListItem = {
  id: string;
  start_local: string;
  sport: string;
  distance_m: number | null;
  moving_s: number | null;
  avg_hr: number | null;
  avg_pace_s_per_km: number | null;
  has_stream: boolean;
};

export type ActivityList = {
  activities: ActivityListItem[];
  total: number;
  limit: number;
  offset: number;
};

export type LapItem = {
  index: number;
  distance_m: number | null;
  duration_s: number | null;
  avg_hr: number | null;
  avg_pace_s_per_km: number | null;
  pace_delta_s_per_km: number | null;
};

export type ActivityDetail = {
  id: string;
  start_local: string;
  sport: string;
  distance_m: number | null;
  moving_s: number | null;
  elapsed_s: number | null;
  elevation_gain_m: number | null;
  avg_hr: number | null;
  max_hr: number | null;
  avg_pace_s_per_km: number | null;
  avg_cadence_spm: number | null;
  has_stream: boolean;
  trimp: MetricEnvelope<number>;
  hr_tss: MetricEnvelope<number>;
  r_tss: MetricEnvelope<number>;
  gap_pace_s_per_km: MetricEnvelope<number>;
  efficiency_factor: MetricEnvelope<number>;
  decoupling: MetricEnvelope<number>;
  zones: ZoneShare[];
  zone_bounds: { kind: string; model: string; lower_bounds: number[] } | null;
  seconds_below_zone_1: number;
  seconds_unknown: number;
  laps: LapItem[];
  planned: PlannedWorkout | null;
  source: string;
};

export type ZoneShare = { zone: number; seconds: number };

export type FitnessPoint = {
  date: string;
  load: number | null;
  ctl: number | null;
  atl: number | null;
  tsb: number | null;
  confidence: number;
  days_of_history: number;
};

export type WeekVolume = {
  week_start: string;
  distance_m: number;
  duration_s: number;
  load: number | null;
  zones: ZoneShare[];
};

export type BaselinePoint = {
  date: string;
  value: number;
  mean: number | null;
  lower: number | null;
  upper: number | null;
};

export type TrendsResponse = {
  window: string;
  from_date: string;
  to_date: string;
  fitness: FitnessPoint[];
  weeks: WeekVolume[];
  hrv: MetricEnvelope<BaselineValue>;
  hrv_series: BaselinePoint[];
  hrv_source_field: string | null;
  resting_hr: MetricEnvelope<BaselineValue>;
  resting_hr_series: BaselinePoint[];
  vo2max: MetricEnvelope<number>;
  vdot: MetricEnvelope<number>;
  monotony: MetricEnvelope<number>;
  strain: MetricEnvelope<number>;
  average_week_distance_m: number | null;
};

export type BestEffort = {
  duration_s: number;
  distance_m: number;
  speed_m_s: number;
  pace_s_per_km: number;
  activity_id: string | null;
  date: string | null;
};

export type Prediction = { method: string; seconds: number; pace_s_per_km: number };

export type RacePrediction = {
  distance_m: number;
  predictions: Prediction[];
  based_on_date: string | null;
  stale: boolean;
};

export type PerformanceResponse = {
  best_efforts: BestEffort[];
  critical_speed: MetricEnvelope<{
    cs_m_s: number;
    cs_pace_s_per_km: number;
    d_prime_m: number;
    points: number;
    r_squared: number | null;
  }>;
  vdot: MetricEnvelope<number>;
  predictions: RacePrediction[];
  reference_distance_m: number | null;
  reference_seconds: number | null;
  reference_date: string | null;
  predictions_stale: boolean;
  prediction_stale_after_days: number;
};

export type PlanResponse = {
  from_date: string;
  to_date: string;
  workouts: PlannedWorkout[];
  planned_distance_m: number | null;
  planned_duration_s: number | null;
  planned_load: number | null;
  races: PlannedWorkout[];
};

export type AiBudget = {
  month: string;
  spent_eur: number;
  budget_eur: number;
  remaining_eur: number;
  exhausted: boolean;
};

export type AiAnswer = {
  task: string;
  text: string;
  model: string;
  created_at: string;
  cached: boolean;
  budget: AiBudget;
  features_bytes: number;
  features_trimmed: string[];
  cost_eur: number;
};

/**
 * One day of a generated week.
 *
 * The heart rates are the server's, derived from the athlete's own zone
 * bounds — either end may be null rather than guessed, so the interface
 * renders "bis 142" and "ab 168" instead of inventing the missing half.
 */
export type PlanDay = {
  date: string;
  weekday: string;
  weekday_long: string;
  kind: "session" | "rest";
  title: string;
  duration_s: number | null;
  zone: number | null;
  zone_label: string | null;
  target_hr_low: number | null;
  target_hr_high: number | null;
  purpose: string;
};

export type WeekPlan = {
  from_date: string;
  to_date: string;
  rationale: string;
  days: PlanDay[];
  limitations: string[];
  hr_source: string | null;
  hr_note: string | null;
};

/** The plan endpoint's answer: the structure, and the raw text behind it. */
export type AiWeekPlan = AiAnswer & { plan: WeekPlan };

export type PlanAdoptResult = {
  date: string;
  created: boolean;
  workout: PlannedWorkout | null;
  detail: string | null;
};

export type PlanAdoptResponse = {
  results: PlanAdoptResult[];
  created: number;
  skipped: number;
};

export type CredentialStatus = { valid: boolean; last4: string | null };

export type SettingsResponse = {
  hr_max: number | null;
  hr_rest: number | null;
  lthr: number | null;
  threshold_pace_s_per_km: number | null;
  sleep_target_s: number | null;
  zone_model: string;
  hr_zones: { kind: string; model: string; lower_bounds: number[] } | null;
  pace_zones: { kind: string; model: string; lower_bounds: number[] } | null;
  updated_at: string | null;
  intervals: CredentialStatus;
  anthropic: CredentialStatus;
  intervals_athlete_id: string;
  ai_model_daily: string;
  ai_model_planning: string;
  ai_usage: {
    month: string;
    input_tokens: number;
    output_tokens: number;
    cost_eur: number;
    budget_eur: number;
    calls: number;
  };
  garmin_direct_enabled: boolean;
  readiness_weights: Record<string, number>;
  fit_files_pending: number;
  sync: SyncStatus;
};
