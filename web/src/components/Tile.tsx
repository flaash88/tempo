/**
 * The card every metric is shown in, and the six states it can be in.
 *
 * The state comes from the envelope, never from the screen: a tile decides
 * for itself whether it shows a number, progress towards one, or the date
 * of the last one. That is what lets one screen carry tiles in four
 * different states at once, which — with this athlete's history — is what
 * it does most days.
 */

import type { ReactNode } from "react";
import type { TileView } from "../lib/state";
import { formatDate } from "../lib/format";
import { useHistoryUnit } from "../lib/thresholds";

export function Card({
  children,
  className = "",
  as: Tag = "section",
}: {
  children: ReactNode;
  className?: string;
  as?: "section" | "article" | "div";
}) {
  return (
    <Tag
      className={`fadein rounded-lg bg-surface p-4 shadow-e1 ${className}`}
      style={{ borderRadius: "var(--r-lg)" }}
    >
      {children}
    </Tag>
  );
}

export function TileHeader({
  title,
  right,
}: {
  title: string;
  right?: ReactNode;
}) {
  return (
    <header className="flex items-baseline justify-between gap-2">
      <h2 className="label-micro m-0">{title}</h2>
      {right}
    </header>
  );
}

/** "Stand 24.08." — a value that is no longer current says so, on the tile. */
export function StaleMark({ date }: { date: string | null }) {
  if (!date) return null;
  return (
    <span
      className="num text-micro"
      style={{ color: "var(--st-caution)" }}
      title="Der letzte Datenpunkt ist älter als die Aktualitätsgrenze"
    >
      Stand {formatDate(date)}
    </span>
  );
}

export function Skeleton({ className = "" }: { className?: string }) {
  return (
    <div
      aria-hidden="true"
      className={`shimmer ${className}`}
      style={{ borderRadius: "var(--r-sm)" }}
    />
  );
}

export function TileSkeleton({ lines = 2 }: { lines?: number }) {
  return (
    <div className="flex flex-col gap-2" role="status" aria-label="Wird geladen">
      <Skeleton className="h-8 w-2/3" />
      {Array.from({ length: lines }).map((_, index) => (
        <Skeleton key={index} className="h-3 w-1/2" />
      ))}
    </div>
  );
}

/**
 * The progress state — the one this instance shows most.
 *
 * The numbers are the envelope's own have/required, and the date is the
 * server's available_from. Nothing is computed here, because a countdown
 * the interface calculates would eventually disagree with the one the API
 * publishes.
 */
export function BuildingState({
  metric,
  view,
}: {
  metric: string;
  view: TileView<unknown>;
}) {
  const progress = view.progress;
  const unit = useHistoryUnit(metric, progress?.required ?? 0);
  if (!progress) {
    return <p className="m-0 text-sub" style={{ color: "var(--t-ink-3)" }}>Noch kein Wert</p>;
  }
  return (
    <div className="flex flex-col gap-2">
      <p className="m-0 text-metric-sm" style={{ color: "var(--t-ink-2)" }}>
        Im Aufbau
      </p>
      <div
        className="h-1 w-full overflow-hidden"
        style={{ background: "var(--t-surface-2)", borderRadius: "var(--r-pill)" }}
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={progress.required}
        aria-valuenow={progress.have}
      >
        <div
          className="h-full"
          style={{
            width: `${Math.round(progress.ratio * 100)}%`,
            background: "var(--t-accent-line)",
            borderRadius: "var(--r-pill)",
          }}
        />
      </div>
      <p className="num m-0 text-sub" style={{ color: "var(--t-ink-3)" }}>
        {progress.have} / {progress.required} {unit}
        {progress.availableFrom ? ` · verfügbar ab ${formatDate(progress.availableFrom)}` : ""}
      </p>
      {view.lastDataPoint ? (
        <p className="num m-0 text-micro" style={{ color: "var(--t-ink-3)" }}>
          letzter Datenpunkt {formatDate(view.lastDataPoint)}
        </p>
      ) : null}
    </div>
  );
}

export function EmptyState({
  message,
  action,
}: {
  message: string;
  action?: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-3">
      <p className="m-0 text-sub" style={{ color: "var(--t-ink-3)" }}>
        {message}
      </p>
      {action}
    </div>
  );
}

export function TileError({ onRetry }: { onRetry?: () => void }) {
  return (
    <div className="flex flex-col gap-3">
      <p className="m-0 text-sub" style={{ color: "var(--st-warn)" }}>
        Konnte nicht geladen werden.
      </p>
      {onRetry ? <SecondaryButton onClick={onRetry}>Erneut</SecondaryButton> : null}
    </div>
  );
}

/** Primary actions are outlined, never filled — Nocturne, and CLAUDE.md. */
export function PrimaryButton({
  children,
  onClick,
  disabled,
  type = "button",
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
  type?: "button" | "submit";
}) {
  return (
    <button
      type={type}
      onClick={onClick}
      disabled={disabled}
      className="num inline-flex items-center justify-center gap-2 px-4 text-body transition-opacity disabled:opacity-50"
      style={{
        minHeight: "var(--touch)",
        borderRadius: "var(--r-md)",
        color: "var(--t-accent-ink)",
        background: "transparent",
        boxShadow: "inset 0 0 0 1px var(--t-accent-line)",
        fontWeight: 500,
      }}
    >
      {children}
    </button>
  );
}

export function SecondaryButton({
  children,
  onClick,
  disabled,
}: {
  children: ReactNode;
  onClick?: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="inline-flex items-center justify-center gap-2 px-4 text-body transition-opacity disabled:opacity-50"
      style={{
        minHeight: "var(--touch)",
        borderRadius: "var(--r-md)",
        color: "var(--t-ink-2)",
        background: "transparent",
        boxShadow: "inset 0 0 0 1px var(--t-line-strong)",
        fontWeight: 500,
      }}
    >
      {children}
    </button>
  );
}
