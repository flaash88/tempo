/**
 * The week, as it is laid out.
 *
 * The complaint that started this was not that the plan was wrong but
 * that it was unreadable: one block of prose over several screen heights
 * in which the individual days went under. So what is checked here is the
 * layout's promises — a row per day, the reasoning short and above them,
 * the caveats in their own block, and the prose *not* the default view.
 */

import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { WeekProposal } from "./Plan";
import type { AiWeekPlan, PlanDay } from "../lib/types";

const [MONTAG, DIENSTAG]: PlanDay[] = [
  {
    date: "2026-09-14",
    weekday: "Mo",
    weekday_long: "Montag",
    kind: "session",
    title: "Lockerer Dauerlauf",
    duration_s: 2400,
    zone: 2,
    zone_label: "Z2",
    target_hr_low: 143,
    target_hr_high: 150,
    purpose: "Grundlage aufbauen ohne Ermüdung.",
  },
  {
    date: "2026-09-15",
    weekday: "Di",
    weekday_long: "Dienstag",
    kind: "rest",
    title: "Ruhetag",
    duration_s: null,
    zone: null,
    zone_label: null,
    target_hr_low: null,
    target_hr_high: null,
    purpose: "Erholung nach dem Wiedereinstieg.",
  },
] as [PlanDay, PlanDay];

const DAYS: PlanDay[] = [MONTAG, DIENSTAG];

const RAW = JSON.stringify({ rationale: "…", days: [], limitations: [] });

function anAnswer(overrides: Partial<AiWeekPlan> = {}): AiWeekPlan {
  return {
    task: "plan_week",
    text: RAW,
    model: "claude-opus-5",
    created_at: "2026-09-13T06:00:00Z",
    cached: false,
    budget: {
      month: "2026-09",
      spent_eur: 0.4,
      budget_eur: 10,
      remaining_eur: 9.6,
      exhausted: false,
    },
    features_bytes: 3200,
    features_trimmed: [],
    cost_eur: 0.04,
    plan: {
      from_date: "2026-09-14",
      to_date: "2026-09-20",
      rationale: "Erste Woche zurück. Kurz und locker, Ruhe dazwischen.",
      days: DAYS,
      limitations: ["Die Formkurve braucht 42 Tage, vorliegen 8."],
      hr_source: "friel_run_lthr",
      hr_note: null,
    },
    ...overrides,
  };
}

function render(answer: AiWeekPlan = anAnswer()): string {
  return renderToStaticMarkup(<WeekProposal answer={answer} onAdopted={() => {}} />);
}

describe("the generated week", () => {
  it("gives every day its own row", () => {
    const markup = render();

    for (const day of DAYS) {
      expect(markup).toContain(day.weekday);
      expect(markup).toContain(day.title);
    }
  });

  it("puts type, duration and zone in the closed row", () => {
    const markup = render();

    expect(markup).toContain("Einheit");
    expect(markup).toContain("Ruhetag");
    expect(markup).toContain("40 min");
    expect(markup).toContain("Z2");
  });

  it("keeps the reasoning above the days and short", () => {
    const markup = render();

    const rationale = markup.indexOf("Erste Woche zurück");
    const firstDay = markup.indexOf("Lockerer Dauerlauf");
    expect(rationale).toBeGreaterThan(-1);
    expect(rationale).toBeLessThan(firstDay);
  });

  it("holds the purpose behind the tap rather than in the row", () => {
    const markup = render();

    expect(markup).not.toContain("Grundlage aufbauen ohne Ermüdung.");
    expect(markup).toContain('aria-expanded="false"');
  });

  it("collects the caveats in their own block, not in the days", () => {
    const markup = render();

    expect(markup).toContain("Datenlage (1)");
    // Collapsed: the caveat is reachable, not in the way.
    expect(markup).not.toContain("Die Formkurve braucht 42 Tage");
  });

  it("does not carry the raw answer at all", () => {
    // It was there for traceability and turned out to be ballast: the day
    // list is the answer. The text is still in the response and in the
    // database for anyone debugging.
    const markup = render();

    expect(markup).not.toContain("Rohfassung");
    expect(markup).not.toContain(RAW);
  });

  it("offers to take the whole week over exactly once, under the list", () => {
    const markup = render();
    const label = "Ganze Woche übernehmen";

    expect(markup.split(label)).toHaveLength(2);
    expect(markup.indexOf(label)).toBeGreaterThan(markup.lastIndexOf(DIENSTAG.title));
  });

  it("gives every session the same badge, whatever zone it is", () => {
    const markup = render();

    // Two sessions in different zones must not differ in the badge; the
    // zone's colour belongs on the zone.
    for (const zone of ["--z1-band", "--z2-band", "--z3-band"]) {
      expect(markup).not.toContain(zone);
    }
    expect(markup).toContain("var(--z2)");
  });

  it("wraps a long title over two lines instead of cutting it", () => {
    const answer = anAnswer();
    const long = "Langer ruhiger Dauerlauf mit Gehpausen";
    const markup = render({
      ...answer,
      plan: { ...answer.plan, days: [{ ...MONTAG, title: long }] },
    });

    expect(markup).toContain(long);
    expect(markup).toContain("line-clamp-2");
    expect(markup).not.toContain("truncate");
  });

  it("says which model wrote it, from the answer and not from a constant", () => {
    expect(render()).toContain("claude-opus-5");
    expect(render(anAnswer({ model: "claude-sonnet-5" }))).toContain("claude-sonnet-5");
  });

  it("names the date of a stored answer instead of passing it off as new", () => {
    const markup = render(anAnswer({ cached: true }));

    expect(markup).toContain("gespeicherte Antwort vom 13.09.");
  });

  it("says so when there are no target heart rates to show", () => {
    const answer = anAnswer();
    const markup = render({
      ...answer,
      plan: {
        ...answer.plan,
        hr_source: null,
        hr_note: "Zielherzfrequenzen fehlen, solange Schwellen-HF fehlt.",
      },
    });

    expect(markup).toContain("Zielherzfrequenzen fehlen");
  });
});
