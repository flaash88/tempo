/**
 * Screen 4 — Plan, with the transfer to the watch from phase 6.
 *
 * Every session carries its own sync state, and the action offered depends
 * on it: unconfirmed sessions get "Bestätigen", confirmed ones "An Uhr
 * senden", and one that changed after being transferred says so rather than
 * pretending the watch is current.
 *
 * The generated week is a list of days, not a page of prose. A week of
 * training rendered as one block of text is unreadable on a phone — the
 * Tuesday disappears into the Monday — so the endpoint answers with
 * structure and this screen lays it out: one row per day, the reasoning
 * above it, the gaps in the data collected in their own block, and the
 * model's raw answer one tap away for anyone who wants to check.
 */

import { useEffect, useState } from "react";
import {
  Card,
  EmptyState,
  PrimaryButton,
  SecondaryButton,
  TileHeader,
  TileSkeleton,
} from "../components/Tile";
import { Screen, StatusBanner } from "../components/Chrome";
import { useResource } from "../lib/useResource";
import { apiSend, ApiError } from "../lib/api";
import type {
  AiAnswer,
  AiWeekPlan,
  PlanAdoptResponse,
  PlanAdoptResult,
  PlanDay,
  PlannedWorkout,
  PlanResponse,
} from "../lib/types";
import { formatDate, formatHrTarget, formatTarget, formatTime } from "../lib/format";
import {
  adoptBody,
  badgeTone,
  dayDuration,
  openSessions,
  sameWindow,
  zoneColour,
} from "../lib/plan";

/** A window the calendar can show, as the API writes its dates. */
type Week = { from: string; to: string };

/**
 * The week switch. Outlined either way — a filled chip would be the one
 * filled surface on a screen where primary actions are outlined.
 */
function WeekChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className="num px-3 text-sub"
      style={{
        minHeight: "var(--touch)",
        borderRadius: "var(--r-pill)",
        border: "none",
        background: "transparent",
        color: active ? "var(--t-ink)" : "var(--t-ink-3)",
        boxShadow: `inset 0 0 0 1px ${active ? "var(--t-line-strong)" : "var(--t-line)"}`,
        font: "inherit",
      }}
    >
      {children}
    </button>
  );
}

const SYNC_LABEL: Record<PlannedWorkout["sync"]["status"], string> = {
  not_sent: "Nicht übertragen",
  sending: "Wird übertragen …",
  on_watch: "Auf Uhr",
  outdated: "Geändert — Uhr hat die alte Version",
  failed: "Übertragung fehlgeschlagen",
};

const SYNC_TONE: Record<PlannedWorkout["sync"]["status"], string> = {
  not_sent: "var(--st-neutral)",
  sending: "var(--st-caution)",
  on_watch: "var(--st-good)",
  outdated: "var(--st-caution)",
  failed: "var(--st-warn)",
};

function WorkoutRow({
  workout,
  onChanged,
}: {
  workout: PlannedWorkout;
  onChanged: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);
  const sync = workout.sync;

  async function act(path: string) {
    setBusy(true);
    setProblem(null);
    try {
      await apiSend(path);
      onChanged();
    } catch (cause) {
      setProblem(cause instanceof ApiError ? cause.message : "Hat nicht geklappt.");
    } finally {
      setBusy(false);
    }
  }

  const confirmed = sync.confirmed_at !== null;
  return (
    <div className="flex flex-col gap-2 py-3" style={{ borderTop: "1px solid var(--t-line)" }}>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-body">{workout.name ?? "Einheit"}</span>
        <span className="num text-micro" style={{ color: "var(--t-ink-3)" }}>
          {formatDate(workout.date)}
        </span>
      </div>
      <span className="num text-sub" style={{ color: "var(--t-ink-3)" }}>
        {formatTarget(workout.target_time_s, workout.target_dist_m)}
        {workout.done ? " · abgehakt" : ""}
      </span>
      {workout.description ? (
        <span className="text-sub" style={{ color: "var(--t-ink-2)" }}>
          {workout.description}
        </span>
      ) : null}

      <div className="flex items-center gap-2">
        <span
          className="px-2 py-1 text-micro"
          style={{
            borderRadius: "var(--r-pill)",
            color: SYNC_TONE[sync.status],
            boxShadow: `inset 0 0 0 1px ${SYNC_TONE[sync.status]}`,
          }}
        >
          {SYNC_LABEL[sync.status]}
          {sync.synced_at ? ` · ${formatTime(sync.synced_at)}` : ""}
        </span>
        {!sync.sendable ? (
          <span className="text-micro" style={{ color: "var(--t-ink-3)" }}>
            aus dem Kalender der Quelle
          </span>
        ) : null}
      </div>

      {sync.error ? (
        <p className="m-0 text-micro" style={{ color: "var(--st-warn)" }}>
          {sync.error}
        </p>
      ) : null}
      {problem ? (
        <p className="m-0 text-micro" style={{ color: "var(--st-warn)" }}>
          {problem}
        </p>
      ) : null}

      {sync.sendable ? (
        <div className="flex flex-wrap gap-2">
          {!confirmed ? (
            <PrimaryButton
              disabled={busy}
              onClick={() => act(`/api/plan/workouts/${workout.id}/confirm`)}
            >
              Bestätigen
            </PrimaryButton>
          ) : (
            <PrimaryButton
              disabled={busy || sync.status === "sending"}
              onClick={() => act(`/api/plan/workouts/${workout.id}/push`)}
            >
              {sync.status === "outdated" ? "Änderung an Uhr senden" : "An Uhr senden"}
            </PrimaryButton>
          )}
        </div>
      ) : null}
    </div>
  );
}

export default function Plan() {
  // Which week the calendar shows. null is the week the athlete is
  // standing in, which is what the server answers without a range.
  const [shown, setShown] = useState<Week | null>(null);
  const plan = useResource<PlanResponse>(
    shown === null
      ? "/api/plan"
      : `/api/plan?from_date=${shown.from}&to_date=${shown.to}`,
  );
  const [answer, setAnswer] = useState<AiWeekPlan | null>(null);
  const [aiProblem, setAiProblem] = useState<string | null>(null);
  const [thinking, setThinking] = useState(false);

  const data = plan.data;

  async function planWeek() {
    setThinking(true);
    setAiProblem(null);
    // Switch the calendar to the week that is about to be planned, so the
    // header never names one week while the proposal names another.
    if (data) setShown({ from: data.plan_week_from, to: data.plan_week_to });
    try {
      const produced = await apiSend<AiWeekPlan>("/api/ai/plan-week");
      // And follow the answer itself, which is the window that was really
      // planned rather than the one that was expected.
      setShown({ from: produced.plan.from_date, to: produced.plan.to_date });
      setAnswer(produced);
    } catch (cause) {
      setAiProblem(
        cause instanceof ApiError ? cause.message : "Die Auswertung ist fehlgeschlagen.",
      );
    } finally {
      setThinking(false);
    }
  }

  // The week the athlete is standing in, remembered from the first answer
  // the server gave without being asked for a range — once the calendar
  // has moved on, that window is no longer in any response.
  const [thisWeek, setThisWeek] = useState<Week | null>(null);
  useEffect(() => {
    if (shown === null && data !== null && thisWeek === null) {
      setThisWeek({ from: data.from_date, to: data.to_date });
    }
  }, [shown, data, thisWeek]);

  const planned: Week | null = data
    ? { from: data.plan_week_from, to: data.plan_week_to }
    : null;
  // On a Monday the week to plan is the week the athlete is in, and a
  // switch between one week and the same week is not a switch.
  const twoWeeks =
    thisWeek !== null && planned !== null && thisWeek.from !== planned.from;

  return (
    <Screen
      title="Plan"
      subtitle={data ? `${formatDate(data.from_date)} – ${formatDate(data.to_date)}` : undefined}
    >
      <StatusBanner
        kind={plan.error === null ? "none" : navigator.onLine ? "error" : "offline"}
        receivedAt={plan.receivedAt}
        detail={plan.error?.message}
        onRetry={plan.reload}
      />

      <Card>
        <TileHeader title="Woche" />
        {twoWeeks && planned ? (
          <div className="mt-2 flex flex-wrap gap-2">
            <WeekChip active={shown === null} onClick={() => setShown(null)}>
              Diese Woche
            </WeekChip>
            <WeekChip active={shown !== null} onClick={() => setShown(planned)}>
              {formatDate(planned.from)} – {formatDate(planned.to)}
            </WeekChip>
          </div>
        ) : null}
        <div className="mt-1">
          {plan.loading ? <TileSkeleton lines={3} /> : null}
          {data && data.workouts.length === 0 ? (
            // Named rather than "diese Woche": next to a week switch, a
            // demonstrative pronoun stops saying which week it means.
            <EmptyState
              message={`Für ${formatDate(data.from_date)} – ${formatDate(
                data.to_date,
              )} ist nichts geplant.`}
            />
          ) : null}
          {data?.workouts.map((workout) => (
            <WorkoutRow key={workout.id} workout={workout} onChanged={plan.reload} />
          ))}
        </div>
      </Card>

      {data && data.races.length > 0 ? (
        <Card>
          <TileHeader title="Wettkämpfe" />
          <div className="mt-1">
            {data.races.map((race) => (
              <div key={race.id} className="flex items-baseline justify-between py-2">
                <span className="text-body">{race.name ?? "Wettkampf"}</span>
                <span className="num text-sub" style={{ color: "var(--t-ink-3)" }}>
                  {formatDate(race.date)}
                </span>
              </div>
            ))}
          </div>
        </Card>
      ) : null}

      <NewWorkout onCreated={plan.reload} />

      <Card>
        <TileHeader title="Wochenvorschlag" />
        <div className="mt-3 flex flex-col gap-4">
          {thinking && answer === null ? <TileSkeleton lines={5} /> : null}
          {!thinking && answer === null && aiProblem === null ? (
            <EmptyState message="Noch kein Vorschlag für die kommende Woche." />
          ) : null}
          {answer && sameWindow(answer.plan, data) ? (
            <WeekProposal
              key={`${answer.plan.from_date}:${answer.created_at}`}
              answer={answer}
              onAdopted={plan.reload}
            />
          ) : null}
          {answer && !sameWindow(answer.plan, data) ? (
            // The calendar has been switched to another week. Drawing the
            // proposal here would put two different weeks on one screen,
            // which is the fault this card was rebuilt to remove.
            <div className="flex flex-col items-start gap-3">
              <p className="m-0 text-sub" style={{ color: "var(--t-ink-3)" }}>
                Der Vorschlag gilt für {formatDate(answer.plan.from_date)} –{" "}
                {formatDate(answer.plan.to_date)}.
              </p>
              <SecondaryButton
                onClick={() =>
                  setShown({ from: answer.plan.from_date, to: answer.plan.to_date })
                }
              >
                Zu dieser Woche
              </SecondaryButton>
            </div>
          ) : null}
          {aiProblem ? (
            <p className="m-0 text-sub" style={{ color: "var(--st-warn)" }}>
              {aiProblem}
            </p>
          ) : null}
          {answer ? (
            <SecondaryButton onClick={planWeek} disabled={thinking}>
              {thinking ? "Wird erstellt …" : "Woche neu planen lassen"}
            </SecondaryButton>
          ) : (
            <PrimaryButton onClick={planWeek} disabled={thinking}>
              {thinking ? "Wird erstellt …" : "Woche vorschlagen"}
            </PrimaryButton>
          )}
          <p className="m-0 text-micro" style={{ color: "var(--t-ink-3)" }}>
            Die KI schlägt eine Verteilung vor — du bestätigst, bevor etwas
            angelegt oder überschrieben wird.
          </p>
        </div>
      </Card>
    </Screen>
  );
}

/**
 * An answer, with the accent line that marks it as the model's words and
 * the footer that names which model wrote them — from the response, never
 * from a constant.
 */
export function AiText({ answer }: { answer: AiAnswer }) {
  return (
    <div
      className="flex flex-col gap-2 py-1 pl-3"
      style={{ borderLeft: "2px solid var(--t-accent-line)" }}
    >
      <p className="m-0 text-body" style={{ color: "var(--t-ink)" }}>
        {answer.text}
      </p>
      <p className="num m-0 text-micro" style={{ color: "var(--t-ink-3)" }}>
        {answer.model}
        {answer.cached ? " · gespeicherte Antwort" : ""}
        {" · noch "}
        {answer.budget.remaining_eur.toFixed(2)} € im {answer.budget.month}
      </p>
    </div>
  );
}


/**
 * The generated week: reasoning, then one row per day.
 *
 * The three blocks are kept apart on purpose. The reasoning is about the
 * week; a day's row is about that day; and what the data does not support
 * is its own collapsible block rather than a caveat woven into every
 * Tuesday. Mixing them is what made the prose version unreadable.
 */
export function WeekProposal({
  answer,
  onAdopted,
}: {
  answer: AiWeekPlan;
  onAdopted: () => void;
}) {
  const plan = answer.plan;
  const [results, setResults] = useState<Record<string, PlanAdoptResult>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const open = openSessions(plan.days, results);

  async function adopt(days: PlanDay[], { replace = false } = {}) {
    const only = days.length === 1 ? days[0] : undefined;
    if (days.length === 0) return;
    setBusy(only ? only.date : "week");
    setProblem(null);
    try {
      const answered = await apiSend<PlanAdoptResponse>("/api/plan/workouts/adopt", {
        body: adoptBody(days, { replace }),
      });
      setResults((earlier) => {
        const merged = { ...earlier };
        for (const result of answered.results) merged[result.date] = result;
        return merged;
      });
      onAdopted();
    } catch (cause) {
      setProblem(
        cause instanceof ApiError ? cause.message : "Das hat nicht geklappt.",
      );
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div
        className="flex flex-col gap-2 py-1 pl-3"
        style={{ borderLeft: "2px solid var(--t-accent-line)" }}
      >
        <p className="m-0 text-body" style={{ color: "var(--t-ink)" }}>
          {plan.rationale}
        </p>
        <p className="num m-0 text-micro" style={{ color: "var(--t-ink-3)" }}>
          {formatDate(plan.from_date)} – {formatDate(plan.to_date)}
        </p>
      </div>

      <div className="flex flex-col gap-2">
        {plan.days.map((day) => (
          <DayRow
            key={day.date}
            day={day}
            result={results[day.date]}
            busy={busy === day.date}
            disabled={busy !== null}
            onAdopt={(options) => void adopt([day], options)}
          />
        ))}
      </div>

      {open.length > 0 ? (
        <PrimaryButton disabled={busy !== null} onClick={() => void adopt(open)}>
          {busy === "week" ? "Wird übernommen …" : "Ganze Woche übernehmen"}
        </PrimaryButton>
      ) : null}

      {plan.hr_note ? (
        <p className="m-0 text-micro" style={{ color: "var(--t-ink-3)" }}>
          {plan.hr_note}
        </p>
      ) : null}

      {problem ? (
        <p className="m-0 text-sub" style={{ color: "var(--st-warn)" }}>
          {problem}
        </p>
      ) : null}

      {plan.limitations.length > 0 ? (
        <Collapsible
          label={`Datenlage (${plan.limitations.length})`}
          hint="was der Vorschlag nicht belegen kann"
        >
          <ul className="m-0 flex list-none flex-col gap-2 p-0">
            {plan.limitations.map((limitation) => (
              <li key={limitation} className="text-sub" style={{ color: "var(--t-ink-2)" }}>
                {limitation}
              </li>
            ))}
          </ul>
        </Collapsible>
      ) : null}

      <p className="num m-0 text-micro" style={{ color: "var(--t-ink-3)" }}>
        {answer.model}
        {answer.cached ? ` · gespeicherte Antwort vom ${formatDate(answer.created_at)}` : ""}
        {" · noch "}
        {answer.budget.remaining_eur.toFixed(2)} € im {answer.budget.month}
      </p>
    </div>
  );
}

/**
 * One day: weekday, badge, duration and zone in the closed row; purpose,
 * target heart rate and the confirmation behind the tap.
 */
function DayRow({
  day,
  result,
  busy,
  disabled,
  onAdopt,
}: {
  day: PlanDay;
  result: PlanAdoptResult | undefined;
  busy: boolean;
  disabled: boolean;
  onAdopt: (options?: { replace?: boolean }) => void;
}) {
  const [open, setOpen] = useState(false);
  const badge = badgeTone(day);
  const duration = dayDuration(day);
  const zone = zoneColour(day);
  const heartRate = formatHrTarget(day.target_hr_low, day.target_hr_high);
  const clash = result !== undefined && !result.created && result.detail !== null;

  return (
    <div
      style={{
        borderRadius: "var(--r-lg)",
        background: "var(--t-surface)",
        boxShadow: "var(--sh-1)",
      }}
    >
      <button
        type="button"
        onClick={() => setOpen((was) => !was)}
        aria-expanded={open}
        className="flex w-full items-center gap-3 px-3 text-left"
        style={{
          minHeight: "var(--touch)",
          border: "none",
          background: "transparent",
          color: "inherit",
          font: "inherit",
          borderRadius: "var(--r-lg)",
        }}
      >
        <span className="flex flex-col items-center" style={{ width: "var(--sp-6)" }}>
          <span className="label-micro" style={{ color: "var(--t-ink-3)" }}>
            {day.weekday}
          </span>
          <span className="num text-sub" style={{ color: "var(--t-ink-2)" }}>
            {formatDate(day.date)}
          </span>
        </span>
        <span className="flex min-w-0 flex-1 flex-col gap-1">
          <span className="flex flex-wrap items-center gap-2">
            <span
              className="px-2 text-micro"
              style={{ borderRadius: "var(--r-sm)", ...badge }}
            >
              {day.kind === "rest" ? "Ruhetag" : "Einheit"}
            </span>
            {/* Two lines rather than an ellipsis: a shortened session name
                is a session whose name you cannot read. */}
            <span className="line-clamp-2 min-w-0 text-body">{day.title}</span>
          </span>
          {duration || day.zone_label ? (
            <span className="num text-sub" style={{ color: "var(--t-ink-3)" }}>
              {duration}
              {duration && day.zone_label ? " · " : ""}
              {day.zone_label ? (
                <span style={zone === null ? undefined : { color: zone }}>
                  {day.zone_label}
                </span>
              ) : null}
            </span>
          ) : null}
        </span>
        <Chevron open={open} />
      </button>

      {open ? (
        <div className="flex flex-col gap-2 px-3 pb-3">
          <p className="m-0 text-sub" style={{ color: "var(--t-ink-2)" }}>
            {day.purpose}
          </p>
          {day.kind === "session" ? (
            <p className="num m-0 text-micro" style={{ color: "var(--t-ink-3)" }}>
              Zielherzfrequenz {heartRate ?? "—"}
            </p>
          ) : null}
          {result?.created ? (
            <p className="m-0 text-micro" style={{ color: "var(--st-good)" }}>
              Angelegt und bestätigt — noch nicht an die Uhr gesendet.
            </p>
          ) : null}
          {clash ? (
            <p className="m-0 text-micro" style={{ color: "var(--st-caution)" }}>
              {result?.detail}
            </p>
          ) : null}
          {day.kind === "session" && !result?.created ? (
            <div className="flex flex-wrap gap-2">
              <PrimaryButton disabled={disabled} onClick={() => onAdopt()}>
                {busy ? "Wird übernommen …" : "Bestätigen"}
              </PrimaryButton>
              {clash ? (
                <SecondaryButton
                  disabled={disabled}
                  onClick={() => onAdopt({ replace: true })}
                >
                  Bestehende ersetzen
                </SecondaryButton>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

/** A block that starts closed. Phosphor's caret, inline, on currentColor. */
function Chevron({ open }: { open: boolean }) {
  return (
    <svg
      width="14"
      height="14"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2.4"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      style={{
        color: "var(--t-ink-3)",
        flex: "none",
        transform: open ? "rotate(180deg)" : undefined,
      }}
    >
      <path d="M6 9l6 6 6-6" />
    </svg>
  );
}

function Collapsible({
  label,
  hint,
  children,
}: {
  label: string;
  hint?: string;
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="flex flex-col gap-2">
      <button
        type="button"
        onClick={() => setOpen((was) => !was)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-2 text-left"
        style={{
          minHeight: "var(--touch)",
          border: "none",
          background: "transparent",
          color: "inherit",
          font: "inherit",
          padding: 0,
        }}
      >
        <span className="flex flex-col">
          <span className="text-sub" style={{ color: "var(--t-ink-2)" }}>
            {label}
          </span>
          {hint ? (
            <span className="text-micro" style={{ color: "var(--t-ink-3)" }}>
              {hint}
            </span>
          ) : null}
        </span>
        <Chevron open={open} />
      </button>
      {open ? children : null}
    </div>
  );
}


/**
 * Adding a session, which is where the way to the watch begins.
 *
 * Deliberately small: a date, a name, a duration and a distance. The
 * structured step list the design sketches needs a screen of its own, and
 * a half-built editor would be worse than an honest short form — what
 * matters for phase 6 is that a session can be proposed at all, and that
 * proposing it is visibly not the same as confirming it.
 */
function NewWorkout({ onCreated }: { onCreated: () => void }) {
  const [open, setOpen] = useState(false);
  const [date, setDate] = useState(() => new Date().toISOString().slice(0, 10));
  const [name, setName] = useState("");
  const [minutes, setMinutes] = useState("45");
  const [kilometres, setKilometres] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function create(replace = false) {
    setBusy(true);
    setProblem(null);
    try {
      await apiSend(`/api/plan/workouts${replace ? "?replace=true" : ""}`, {
        body: {
          date,
          sport: "Run",
          name: name.trim() || "Lauf",
          target_time_s: Number(minutes) > 0 ? Math.round(Number(minutes) * 60) : null,
          target_dist_m:
            Number(kilometres) > 0 ? Math.round(Number(kilometres) * 1000) : null,
        },
      });
      setOpen(false);
      setName("");
      onCreated();
    } catch (cause) {
      setProblem(
        cause instanceof ApiError ? cause.message : "Die Einheit ließ sich nicht anlegen.",
      );
    } finally {
      setBusy(false);
    }
  }

  if (!open) {
    return (
      <Card>
        <TileHeader title="Einheit" />
        <div className="mt-3">
          <PrimaryButton onClick={() => setOpen(true)}>Einheit anlegen</PrimaryButton>
        </div>
      </Card>
    );
  }

  const clash = problem !== null && problem.includes("replace=true");
  return (
    <Card>
      <TileHeader title="Neue Einheit" />
      <div className="mt-3 flex flex-col gap-3">
        <Field label="Datum">
          <input
            type="date"
            value={date}
            onChange={(event) => setDate(event.target.value)}
            style={inputStyle}
          />
        </Field>
        <Field label="Name">
          <input
            type="text"
            value={name}
            placeholder="Lockerer Dauerlauf"
            onChange={(event) => setName(event.target.value)}
            style={inputStyle}
          />
        </Field>
        <div className="grid grid-cols-2 gap-3">
          <Field label="Dauer (min)">
            <input
              type="number"
              inputMode="numeric"
              value={minutes}
              onChange={(event) => setMinutes(event.target.value)}
              style={inputStyle}
            />
          </Field>
          <Field label="Distanz (km)">
            <input
              type="number"
              inputMode="decimal"
              step="0.1"
              value={kilometres}
              onChange={(event) => setKilometres(event.target.value)}
              style={inputStyle}
            />
          </Field>
        </div>
        {problem ? (
          <p className="m-0 text-sub" style={{ color: "var(--st-warn)" }}>
            {problem}
          </p>
        ) : null}
        <div className="flex flex-wrap gap-2">
          <PrimaryButton onClick={() => void create()} disabled={busy}>
            {busy ? "Legt an …" : "Vorschlagen"}
          </PrimaryButton>
          {clash ? (
            <SecondaryButton onClick={() => void create(true)} disabled={busy}>
              Bestehende ersetzen
            </SecondaryButton>
          ) : null}
          <SecondaryButton onClick={() => setOpen(false)}>Abbrechen</SecondaryButton>
        </div>
        <p className="m-0 text-micro" style={{ color: "var(--t-ink-3)" }}>
          Ein Vorschlag geht nirgendwohin, bis er bestätigt ist.
        </p>
      </div>
    </Card>
  );
}

const inputStyle: React.CSSProperties = {
  borderRadius: "var(--r-md)",
  background: "var(--t-surface-2)",
  color: "var(--t-ink)",
  border: "none",
  padding: "0 var(--sp-3)",
  minHeight: "var(--touch)",
  width: "100%",
};

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="flex flex-col gap-1">
      <span className="label-micro">{label}</span>
      {children}
    </label>
  );
}
