/**
 * Screen 3 — Trends.
 *
 * Two things are drawn only if there is something to draw: the form curve
 * needs its minimum history, the HRV band needs its baseline. With this
 * history both are still building, so what the screen mostly shows is
 * progress and the weekly volume that does exist.
 */

import { useState } from "react";
import { Card, BuildingState, EmptyState, StaleMark, TileError, TileHeader, TileSkeleton } from "../components/Tile";
import { Screen, StatusBanner } from "../components/Chrome";
import { useResource } from "../lib/useResource";
import { tileState } from "../lib/state";
import type { PerformanceResponse, TrendsResponse } from "../lib/types";
import {
  formatDate,
  formatDistance,
  formatDuration,
  formatNumber,
  formatPace,
} from "../lib/format";
import { useThresholds } from "../lib/thresholds";

const WINDOWS = ["6w", "12w", "52w"] as const;

function Sparkline({
  points,
  colour,
}: {
  points: { x: number; y: number | null }[];
  colour: string;
}) {
  const values = points.filter((p): p is { x: number; y: number } => p.y !== null);
  if (values.length < 2) return null;
  const ys = values.map((p) => p.y);
  const min = Math.min(...ys);
  const max = Math.max(...ys);
  const span = max - min || 1;
  const path = values
    .map((point, index) => {
      const x = (index / (values.length - 1)) * 100;
      const y = 30 - ((point.y - min) / span) * 28;
      return `${index === 0 ? "M" : "L"}${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");
  return (
    <svg viewBox="0 0 100 32" preserveAspectRatio="none" className="h-16 w-full" aria-hidden="true">
      <path d={path} fill="none" stroke={colour} strokeWidth="1.2" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

export default function Trends() {
  const [window, setWindow] = useState<(typeof WINDOWS)[number]>("12w");
  const trends = useResource<TrendsResponse>(`/api/trends?window=${window}`, [window]);
  const performance = useResource<PerformanceResponse>("/api/performance");
  const { thresholds } = useThresholds();

  const data = trends.data;
  const failed = !data && trends.error !== null;
  const hrv = tileState({
    envelope: data?.hrv,
    loading: trends.loading,
    error: failed,
  });
  const monotony = tileState<number>({
    envelope: data?.monotony,
    loading: trends.loading,
    error: failed,
  });

  // The form curve has no envelope of its own on this endpoint: the series
  // is what it publishes, and a point with a CTL is a day the minimum
  // history was met. No history, no line — and the tile says which.
  const fitness = (data?.fitness ?? []).filter((point) => point.ctl !== null);
  const formMinimum = thresholds?.minimum_history?.form;
  // A block of zeroes is not a training history — it is the absence of one,
  // and twelve identical rows saying "0 m" say that worse than one sentence.
  const recorded = (data?.weeks ?? []).filter(
    (week) => week.distance_m > 0 || week.duration_s > 0,
  );

  return (
    <Screen
      title="Trends"
      subtitle={data ? `${formatDate(data.from_date)} – ${formatDate(data.to_date)}` : undefined}
    >
      <StatusBanner
        kind={trends.error === null ? "none" : navigator.onLine ? "error" : "offline"}
        receivedAt={trends.receivedAt}
        detail={trends.error?.message}
        onRetry={trends.reload}
      />

      <div className="flex gap-2" role="tablist" aria-label="Zeitraum">
        {WINDOWS.map((option) => (
          <button
            key={option}
            role="tab"
            aria-selected={option === window}
            onClick={() => setWindow(option)}
            className="num px-3 text-sub"
            style={{
              minHeight: "var(--touch)",
              borderRadius: "var(--r-pill)",
              color: option === window ? "var(--t-accent-ink)" : "var(--t-ink-3)",
              boxShadow:
                option === window
                  ? "inset 0 0 0 1px var(--t-accent-line)"
                  : "inset 0 0 0 1px var(--t-line)",
            }}
          >
            {option}
          </button>
        ))}
      </div>

      <Card>
        <TileHeader title="Formkurve" />
        <div className="mt-3">
          {trends.loading ? <TileSkeleton lines={2} /> : null}
          {failed ? <TileError onRetry={trends.reload} /> : null}
          {!trends.loading && !failed && fitness.length === 0 ? (
            <EmptyState
              message={
                formMinimum
                  ? `Im aktuellen Fenster noch kein Wert — die Kurve braucht ${formMinimum.required} ${formMinimum.unit === "days" ? "zusammenhängende Tage" : formMinimum.unit}.`
                  : "Im aktuellen Fenster noch kein Wert."
              }
            />
          ) : null}
          {fitness.length > 0 ? (
            <>
              <Sparkline
                points={fitness.map((point, index) => ({ x: index, y: point.ctl }))}
                colour="var(--t-accent)"
              />
              <div className="grid grid-cols-3 gap-2">
                {(
                  [
                    ["CTL", fitness.at(-1)?.ctl ?? null],
                    ["ATL", fitness.at(-1)?.atl ?? null],
                    ["TSB", fitness.at(-1)?.tsb ?? null],
                  ] as const
                ).map(([label, value]) => (
                  <div key={label} className="flex flex-col">
                    <span className="num text-metric-sm" style={{ fontWeight: 500 }}>
                      {formatNumber(value, 1)}
                    </span>
                    <span className="label-micro">{label}</span>
                  </div>
                ))}
              </div>
            </>
          ) : null}
        </div>
      </Card>

      <Card>
        <TileHeader title="Wochenvolumen" />
        <div className="mt-3 flex flex-col gap-2">
          {trends.loading ? <TileSkeleton lines={3} /> : null}
          {!trends.loading && data && recorded.length === 0 ? (
            <EmptyState message="In diesem Zeitraum wurde nichts aufgezeichnet." />
          ) : null}
          {recorded.map((week) => {
            const total = week.zones.reduce((sum, share) => sum + share.seconds, 0);
            return (
              <div key={week.week_start} className="flex flex-col gap-1">
                <div className="flex items-baseline justify-between">
                  <span className="num text-sub" style={{ color: "var(--t-ink-2)" }}>
                    {formatDate(week.week_start)}
                  </span>
                  <span className="num text-sub">
                    {formatDistance(week.distance_m)} · {formatDuration(week.duration_s)}
                  </span>
                </div>
                <div className="flex h-2 overflow-hidden" style={{ borderRadius: "var(--r-pill)" }}>
                  {total > 0 ? (
                    week.zones.map((share) => (
                      <div
                        key={share.zone}
                        style={{
                          width: `${(share.seconds / total) * 100}%`,
                          background: `var(--z${share.zone})`,
                        }}
                      />
                    ))
                  ) : (
                    <div className="w-full" style={{ background: "var(--t-surface-2)" }} />
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </Card>

      <Card>
        <TileHeader
          title="HFV-Band"
          right={hrv.state === "stale" ? <StaleMark date={hrv.lastDataPoint} /> : null}
        />
        <div className="mt-3">
          {hrv.state === "loading" ? <TileSkeleton lines={1} /> : null}
          {hrv.state === "error" ? <TileError onRetry={trends.reload} /> : null}
          {hrv.state === "empty" ? <EmptyState message="Keine Messungen." /> : null}
          {hrv.state === "building" ? <BuildingState metric="hrv_baseline" view={hrv} /> : null}
          {hrv.value ? (
            <div className="flex flex-col gap-2">
              <Sparkline
                points={(data?.hrv_series ?? []).map((point, index) => ({
                  x: index,
                  y: point.value,
                }))}
                colour="var(--z2)"
              />
              <span className="num text-metric-sm" style={{ fontWeight: 500 }}>
                {formatNumber(hrv.value.mean, 1)}
                <span className="text-sub" style={{ color: "var(--t-ink-3)" }}>
                  {" "}
                  Ø {hrv.value.days} Tage
                </span>
              </span>
            </div>
          ) : null}
        </div>
      </Card>

      <Card>
        <TileHeader title="Monotonie" />
        <div className="mt-3">
          {monotony.state === "loading" ? <TileSkeleton lines={1} /> : null}
          {monotony.state === "building" ? (
            <BuildingState metric="form" view={monotony} />
          ) : null}
          {monotony.state === "empty" ? (
            <EmptyState message="Noch keine Belastung erfasst." />
          ) : null}
          {monotony.value !== null ? (
            <span className="num text-metric" style={{ fontWeight: 500 }}>
              {formatNumber(monotony.value, 2)}
            </span>
          ) : null}
        </div>
      </Card>

      <Card>
        <TileHeader
          title="Bestleistungen"
          right={
            performance.data?.predictions_stale ? (
              <StaleMark date={performance.data.reference_date} />
            ) : null
          }
        />
        <div className="mt-3 flex flex-col gap-2">
          {performance.loading ? <TileSkeleton lines={2} /> : null}
          {performance.data && performance.data.best_efforts.length === 0 ? (
            <EmptyState message="Noch keine Läufe aufgezeichnet." />
          ) : null}
          {performance.data?.best_efforts.map((effort) => (
            <div key={effort.duration_s} className="flex items-baseline justify-between">
              <span className="num text-sub" style={{ color: "var(--t-ink-2)" }}>
                {formatDuration(effort.duration_s)}
              </span>
              <span className="num text-sub">
                {formatDistance(effort.distance_m)} · {formatPace(effort.pace_s_per_km)} /km
                {effort.date ? ` · ${formatDate(effort.date)}` : ""}
              </span>
            </div>
          ))}
          {performance.data?.predictions_stale ? (
            <p className="m-0 text-micro" style={{ color: "var(--st-caution)" }}>
              Bestleistungen bleiben Rekorde: die Prognosen stützen sich auf eine
              Leistung, die älter ist als {performance.data.prediction_stale_after_days}{" "}
              Tage.
            </p>
          ) : null}
        </div>
      </Card>
    </Screen>
  );
}
