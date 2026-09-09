/**
 * Screen 4 — Plan, with the transfer to the watch from phase 6.
 *
 * Every session carries its own sync state, and the action offered depends
 * on it: unconfirmed sessions get "Bestätigen", confirmed ones "An Uhr
 * senden", and one that changed after being transferred says so rather than
 * pretending the watch is current.
 */

import { useState } from "react";
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
import type { AiAnswer, PlannedWorkout, PlanResponse } from "../lib/types";
import { formatDate, formatTarget, formatTime } from "../lib/format";

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
  const plan = useResource<PlanResponse>("/api/plan");
  const [answer, setAnswer] = useState<AiAnswer | null>(null);
  const [aiProblem, setAiProblem] = useState<string | null>(null);
  const [thinking, setThinking] = useState(false);

  async function planWeek() {
    setThinking(true);
    setAiProblem(null);
    try {
      setAnswer(await apiSend<AiAnswer>("/api/ai/plan-week"));
    } catch (cause) {
      setAiProblem(
        cause instanceof ApiError ? cause.message : "Die Auswertung ist fehlgeschlagen.",
      );
    } finally {
      setThinking(false);
    }
  }

  const data = plan.data;

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
        <div className="mt-1">
          {plan.loading ? <TileSkeleton lines={3} /> : null}
          {data && data.workouts.length === 0 ? (
            <EmptyState message="Für diese Woche ist nichts geplant." />
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
        <div className="mt-3 flex flex-col gap-3">
          {answer ? <AiText answer={answer} /> : null}
          {aiProblem ? (
            <p className="m-0 text-sub" style={{ color: "var(--st-warn)" }}>
              {aiProblem}
            </p>
          ) : null}
          <PrimaryButton onClick={planWeek} disabled={thinking}>
            {thinking ? "Wird erstellt …" : "Woche vorschlagen"}
          </PrimaryButton>
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
