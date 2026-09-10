/**
 * The generated week, away from the markup.
 *
 * What a day says about itself, what colour describes it, and what is
 * sent when it is taken over — all of it is decided here so it can be
 * checked without rendering anything.
 */

import { formatPlannedDuration } from "./format";
import type { PlanResponse, PlanAdoptResult, PlanDay, WeekPlan } from "./types";

/** The duration a day's row shows, or null for a rest day. */
export function dayDuration(day: PlanDay): string | null {
  return day.duration_s === null ? null : formatPlannedDuration(day.duration_s);
}

/**
 * The type badge's colours: session or rest day, and nothing else.
 *
 * It carried the zone's colour once, which put two different colours on
 * two days of the same type and made the badge look like it meant
 * something it did not. The zone's colour belongs on the zone.
 */
export function badgeTone(day: PlanDay): {
  background: string;
  color: string;
  boxShadow?: string;
} {
  if (day.kind === "rest") {
    return {
      background: "transparent",
      color: "var(--t-ink-3)",
      boxShadow: "inset 0 0 0 1px var(--t-line)",
    };
  }
  return { background: "var(--t-surface-2)", color: "var(--t-ink-2)" };
}

/**
 * The colour of the zone label, from the zone palette in
 * `tempo-tokens.css` and from nowhere else.
 *
 * Zone colours describe; they never say whether a day is good. Status
 * colours stay out of the plan entirely.
 */
export function zoneColour(day: PlanDay): string | null {
  if (day.kind === "rest" || day.zone === null) return null;
  return `var(--z${day.zone})`;
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

/**
 * Whether the calendar above the proposal is showing the proposal's week.
 *
 * The fault this exists to prevent: a header reading "07.09. – 13.09."
 * over a proposal for "11.09. – 17.09.". Two windows on one screen that
 * disagree are worse than one window, so when this is false the proposal
 * is not drawn at all — only a line saying which week it is for.
 */
export function sameWindow(
  plan: Pick<WeekPlan, "from_date" | "to_date">,
  calendar: Pick<PlanResponse, "from_date" | "to_date"> | null,
): boolean {
  if (calendar === null) return false;
  return plan.from_date === calendar.from_date && plan.to_date === calendar.to_date;
}
