/**
 * The generated week, away from the markup.
 *
 * What a day says about itself, what colour describes it, and what is
 * sent when it is taken over — all of it is decided here so it can be
 * checked without rendering anything.
 */

import { formatPlannedDuration } from "./format";
import type { PlanAdoptResult, PlanDay } from "./types";

/** "40 min · Z2" for a session, "" for a rest day. */
export function daySpec(day: PlanDay): string {
  return [
    day.duration_s === null ? null : formatPlannedDuration(day.duration_s),
    day.zone_label,
  ]
    .filter((part): part is string => Boolean(part))
    .join(" · ");
}

/**
 * The badge's colours.
 *
 * Zone colours, which describe — never status colours, which judge. A
 * plan does not say whether a Tuesday is good.
 */
export function zoneTone(day: PlanDay): { background: string; color: string } {
  if (day.kind === "rest" || day.zone === null) {
    return { background: "var(--t-surface-2)", color: "var(--t-ink-3)" };
  }
  return { background: `var(--z${day.zone}-band)`, color: `var(--z${day.zone})` };
}

/** The session days that are not in the calendar yet. Rest days never are. */
export function openSessions(
  days: PlanDay[],
  results: Record<string, PlanAdoptResult>,
): PlanDay[] {
  return days.filter(
    (day) => day.kind === "session" && results[day.date]?.created !== true,
  );
}

/**
 * What "übernehmen" sends.
 *
 * Only the fields the server validates, and nothing derived: the
 * description that ends up in the calendar is assembled server-side from
 * these, not composed here and posted as a string.
 */
export function adoptBody(
  days: PlanDay[],
  { replace = false } = {},
): {
  replace: boolean;
  days: {
    date: string;
    title: string;
    duration_s: number | null;
    zone_label: string | null;
    purpose: string;
  }[];
} {
  return {
    replace,
    days: days.map((day) => ({
      date: day.date,
      title: day.title,
      duration_s: day.duration_s,
      zone_label: day.zone_label,
      purpose: day.purpose,
    })),
  };
}
