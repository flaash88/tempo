/**
 * Screen 1 — Heute.
 *
 * Six tiles, each in whatever state its own envelope puts it in. On this
 * instance that currently means: readiness, HRV, resting heart rate and the
 * form curve are all building, sleep carries a date from June, and the
 * planned session is empty. That is the screen this was built against —
 * not the mock-up's full house of numbers.
 */

import { useMemo } from "react";
import { Link } from "react-router-dom";
import {
  BuildingState,
  Card,
  EmptyState,
  PrimaryButton,
  StaleMark,
  TileError,
  TileHeader,
  TileSkeleton,
} from "../components/Tile";
import { Screen, StatusBanner } from "../components/Chrome";
import { useResource } from "../lib/useResource";
import { tileState } from "../lib/state";
import type { BaselineValue, FormValue, SleepValue, TodayResponse } from "../lib/types";
import {
  formatDate,
  formatHoursMinutes,
  formatLongDate,
  formatNumber,
  formatTarget,
} from "../lib/format";
import { useMinimumLabel } from "../lib/thresholds";

function ReadinessRing({ value, confidence }: { value: number; confidence: number }) {
  const radius = 54;
  const circumference = 2 * Math.PI * radius;
  const filled = Math.max(0, Math.min(100, value)) / 100;
  const tone =
    value >= 70 ? "var(--st-good)" : value >= 45 ? "var(--st-caution)" : "var(--st-warn)";
  return (
    <div className="flex items-center gap-4">
      <svg width="128" height="128" viewBox="0 0 128 128" aria-hidden="true">
        <circle cx="64" cy="64" r={radius} fill="none" stroke="var(--t-surface-2)" strokeWidth="8" />
        <circle
          cx="64"
          cy="64"
          r={radius}
          fill="none"
          stroke={tone}
          strokeWidth="8"
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - filled)}
          transform="rotate(-90 64 64)"
          opacity={0.35 + 0.65 * confidence}
        />
      </svg>
      <div className="flex flex-col">
        <span className="num text-hero" style={{ lineHeight: "var(--lh-tight)", fontWeight: 500 }}>
          {formatNumber(value)}
        </span>
        <span className="text-sub" style={{ color: "var(--t-ink-3)" }}>
          von 100
        </span>
      </div>
    </div>
  );
}

export default function Heute() {
  const today = useResource<TodayResponse>("/api/today");
  const data = today.data;
  const readinessLabel = useMinimumLabel("readiness");

  const readiness = tileState<number>({
    envelope: data?.readiness,
    loading: today.loading,
    error: !data && today.error !== null,
  });
  const hrv = tileState<BaselineValue>({
    envelope: data?.hrv,
    loading: today.loading,
    error: !data && today.error !== null,
  });
  const restingHr = tileState<BaselineValue>({
    envelope: data?.resting_hr,
    loading: today.loading,
    error: !data && today.error !== null,
  });
  const sleep = tileState<SleepValue>({
    envelope: data?.sleep,
    loading: today.loading,
    error: !data && today.error !== null,
  });
  const form = tileState<FormValue>({
    envelope: data?.form,
    loading: today.loading,
    error: !data && today.error !== null,
  });

  const bannerKind = useMemo(() => {
    if (today.error === null) return "none" as const;
    return navigator.onLine ? ("error" as const) : ("offline" as const);
  }, [today.error]);

  return (
    <Screen
      title={formatLongDate(data?.date ?? new Date())}
      subtitle={data ? `KI-Modell ${data.ai_model}` : undefined}
    >
      <StatusBanner
        kind={bannerKind}
        receivedAt={today.receivedAt}
        detail={today.error?.message}
        onRetry={today.reload}
      />

      <Card>
        <TileHeader
          title="Bereitschaft"
          right={readiness.state === "stale" ? <StaleMark date={readiness.lastDataPoint} /> : null}
        />
        <div className="mt-3">
          {readiness.state === "loading" ? <TileSkeleton lines={2} /> : null}
          {readiness.state === "error" ? <TileError onRetry={today.reload} /> : null}
          {readiness.state === "empty" ? (
            <EmptyState
              message={
                readinessLabel
                  ? `${readinessLabel} — noch keine Nächte aufgezeichnet.`
                  : "Noch keine Daten."
              }
              action={
                <Link to="/more" style={{ textDecoration: "none" }}>
                  <PrimaryButton>intervals.icu verbinden</PrimaryButton>
                </Link>
              }
            />
          ) : null}
          {readiness.state === "building" ? (
            <BuildingState metric="readiness" view={readiness} />
          ) : null}
          {(readiness.state === "ready" || readiness.state === "stale") &&
          readiness.value !== null ? (
            <ReadinessRing value={readiness.value} confidence={readiness.confidence} />
          ) : null}
        </div>
        {data ? <ReadinessInputs data={data} /> : null}
      </Card>

      <div className="grid grid-cols-2 gap-4">
        <Card>
          <TileHeader
            title="HFV"
            right={hrv.state === "stale" ? <StaleMark date={hrv.lastDataPoint} /> : null}
          />
          <div className="mt-3">
            {hrv.state === "loading" ? <TileSkeleton lines={1} /> : null}
            {hrv.state === "error" ? <TileError onRetry={today.reload} /> : null}
            {hrv.state === "empty" ? <EmptyState message="Keine Messungen." /> : null}
            {hrv.state === "building" ? <BuildingState metric="hrv_baseline" view={hrv} /> : null}
            {hrv.value ? (
              <div className="flex flex-col">
                <span className="num text-metric" style={{ fontWeight: 500 }}>
                  {formatNumber(hrv.value.mean, 1)}
                </span>
                <span className="num text-micro" style={{ color: "var(--t-ink-3)" }}>
                  Band {formatNumber(hrv.value.lower, 1)}–{formatNumber(hrv.value.upper, 1)}
                  {hrv.value.source_field ? ` · ${hrv.value.source_field}` : ""}
                </span>
              </div>
            ) : null}
            {data?.hrv_latest !== null && data?.hrv_latest !== undefined && !hrv.value ? (
              <p className="num m-0 mt-2 text-micro" style={{ color: "var(--t-ink-3)" }}>
                zuletzt {formatNumber(data.hrv_latest, 1)}
              </p>
            ) : null}
          </div>
        </Card>

        <Card>
          <TileHeader
            title="Ruhepuls"
            right={restingHr.state === "stale" ? <StaleMark date={restingHr.lastDataPoint} /> : null}
          />
          <div className="mt-3">
            {restingHr.state === "loading" ? <TileSkeleton lines={1} /> : null}
            {restingHr.state === "error" ? <TileError onRetry={today.reload} /> : null}
            {restingHr.state === "empty" ? <EmptyState message="Keine Messungen." /> : null}
            {restingHr.state === "building" ? (
              <BuildingState metric="hrv_baseline" view={restingHr} />
            ) : null}
            {restingHr.value ? (
              <div className="flex flex-col">
                <span className="num text-metric" style={{ fontWeight: 500 }}>
                  {formatNumber(restingHr.value.mean)}
                  <span className="text-sub" style={{ color: "var(--t-ink-3)" }}>
                    {" "}
                    bpm
                  </span>
                </span>
              </div>
            ) : null}
            {data?.resting_hr_latest !== null &&
            data?.resting_hr_latest !== undefined &&
            !restingHr.value ? (
              <p className="num m-0 mt-2 text-micro" style={{ color: "var(--t-ink-3)" }}>
                zuletzt {formatNumber(data.resting_hr_latest)} bpm
              </p>
            ) : null}
          </div>
        </Card>
      </div>

      <Card>
        <TileHeader
          title="Schlaf"
          right={sleep.state === "stale" ? <StaleMark date={sleep.lastDataPoint} /> : null}
        />
        <div className="mt-3">
          {sleep.state === "loading" ? <TileSkeleton lines={1} /> : null}
          {sleep.state === "error" ? <TileError onRetry={today.reload} /> : null}
          {sleep.state === "empty" ? <EmptyState message="Keine Schlafdaten." /> : null}
          {sleep.state === "building" ? <BuildingState metric="readiness" view={sleep} /> : null}
          {sleep.value ? (
            <span className="num text-metric" style={{ fontWeight: 500 }}>
              {formatHoursMinutes(sleep.value.seconds)}
            </span>
          ) : null}
        </div>
      </Card>

      <Card>
        <TileHeader
          title="Formzustand"
          right={form.state === "stale" ? <StaleMark date={form.lastDataPoint} /> : null}
        />
        <div className="mt-3">
          {form.state === "loading" ? <TileSkeleton lines={2} /> : null}
          {form.state === "error" ? <TileError onRetry={today.reload} /> : null}
          {form.state === "empty" ? <EmptyState message="Noch keine Belastung erfasst." /> : null}
          {form.state === "building" ? <BuildingState metric="form" view={form} /> : null}
          {form.value ? (
            <div className="grid grid-cols-3 gap-2">
              {(
                [
                  ["CTL", "Fitness", form.value.ctl],
                  ["ATL", "Ermüdung", form.value.atl],
                  ["TSB", "Form", form.value.tsb],
                ] as const
              ).map(([short, long, number]) => (
                <div key={short} className="flex flex-col">
                  <span className="num text-metric-sm" style={{ fontWeight: 500 }}>
                    {formatNumber(number, 1)}
                  </span>
                  <span className="label-micro">{short}</span>
                  <span className="text-micro" style={{ color: "var(--t-ink-3)" }}>
                    {long}
                  </span>
                </div>
              ))}
            </div>
          ) : null}
        </div>
      </Card>

      <Card>
        <TileHeader title="Geplante Einheit" />
        <div className="mt-3">
          {today.loading ? (
            <TileSkeleton lines={1} />
          ) : data?.planned ? (
            <div className="flex flex-col gap-1">
              <span className="text-body">{data.planned.name ?? "Einheit"}</span>
              <span className="num text-sub" style={{ color: "var(--t-ink-3)" }}>
                {formatTarget(data.planned.target_time_s, data.planned.target_dist_m)}
                {data.planned.done ? " · abgehakt" : ""}
              </span>
              {data.plan_context ? (
                <span className="text-micro" style={{ color: "var(--t-ink-3)" }}>
                  {data.plan_context}
                </span>
              ) : null}
            </div>
          ) : (
            <EmptyState
              message="Für heute ist nichts geplant."
              action={
                <Link to="/plan" style={{ textDecoration: "none" }}>
                  <PrimaryButton>Woche planen</PrimaryButton>
                </Link>
              }
            />
          )}
        </div>
      </Card>
    </Screen>
  );
}

/** "HFV, Ruhepuls und Schlaf" — a list, not a chain of "und". */
function joinGerman(parts: string[]): string {
  if (parts.length === 0) return "";
  if (parts.length === 1) return parts[0] ?? "";
  return `${parts.slice(0, -1).join(", ")} und ${parts.at(-1)}`;
}

/**
 * Which inputs the readiness score actually rests on today.
 *
 * With this history the form term contributes nothing, and saying so is
 * more useful than a score that looks complete — the mock-up's sentence
 * "Auf zwei von drei Treibern gerechnet" is filled from the data, not
 * written into the interface.
 */
function ReadinessInputs({ data }: { data: TodayResponse }) {
  const entries = Object.entries(data.readiness_components);
  const present = entries.filter(([, value]) => value !== null);
  if (entries.length === 0) return null;
  const labels: Record<string, string> = {
    hrv: "HFV",
    resting_hr: "Ruhepuls",
    sleep: "Schlaf",
    tsb: "Form",
  };
  const missing = entries.filter(([, value]) => value === null).map(([key]) => labels[key] ?? key);
  const missingText = joinGerman(missing);
  return (
    <div className="mt-4 flex flex-col gap-2">
      <div className="flex flex-wrap gap-2">
        {present.map(([key, value]) => (
          <span
            key={key}
            className="num px-2 py-1 text-micro"
            style={{
              borderRadius: "var(--r-pill)",
              background: "var(--t-surface-2)",
              color: "var(--t-ink-2)",
            }}
          >
            {labels[key] ?? key} {formatNumber(value)}
            {" · "}
            {formatNumber((data.readiness_weights[key] ?? 0) * 100)} %
          </span>
        ))}
      </div>
      {missing.length > 0 ? (
        <p className="m-0 text-micro" style={{ color: "var(--t-ink-3)" }}>
          {present.length === 0
            ? `Keiner der Treiber liegt im aktuellen Fenster: ${missingText} fehlen.`
            : `Ohne ${missingText} gerechnet.`}
          {data.readiness.last_data_point
            ? ` Letzter Datenpunkt ${formatDate(data.readiness.last_data_point)}.`
            : ""}
        </p>
      ) : null}
    </div>
  );
}
