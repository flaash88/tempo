import { describe, expect, it } from "vitest";
import { adoptBody, daySpec, openSessions, zoneTone } from "./plan";
import { formatHrTarget, formatPlannedDuration } from "./format";
import type { PlanAdoptResult, PlanDay } from "./types";

function aDay(overrides: Partial<PlanDay> = {}): PlanDay {
  return {
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
    ...overrides,
  };
}

const rest = aDay({
  kind: "rest",
  title: "Ruhetag",
  duration_s: null,
  zone: null,
  zone_label: null,
  target_hr_low: null,
  target_hr_high: null,
  purpose: "Erholung.",
});

describe("a planned duration", () => {
  it("reads as minutes, not as a pace", () => {
    // "40:00" on a training plan reads as 4:00 min/km's big brother.
    expect(formatPlannedDuration(2400)).toBe("40 min");
    expect(formatPlannedDuration(4500)).toBe("1:15 h");
    expect(formatPlannedDuration(null)).toBe("—");
  });
});

describe("a target heart rate", () => {
  it("writes the half it has when the other half is open", () => {
    expect(formatHrTarget(143, 150)).toBe("143–150 bpm");
    expect(formatHrTarget(null, 142)).toBe("bis 142 bpm");
    expect(formatHrTarget(168, null)).toBe("ab 168 bpm");
    expect(formatHrTarget(null, null)).toBeNull();
  });
});

describe("a day's line", () => {
  it("carries duration and zone", () => {
    expect(daySpec(aDay())).toBe("40 min · Z2");
  });

  it("is empty for a rest day, which has neither", () => {
    expect(daySpec(rest)).toBe("");
  });

  it("uses zone colours, which describe, and never status colours", () => {
    expect(zoneTone(aDay())).toEqual({
      background: "var(--z2-band)",
      color: "var(--z2)",
    });
    expect(zoneTone(rest).color).toBe("var(--t-ink-3)");
    // Nothing here may reach for --st-good or --st-warn: a Tuesday is not
    // good or bad, it is easy or hard.
    for (const day of [aDay(), aDay({ zone: 5, zone_label: "Z5" }), rest]) {
      expect(JSON.stringify(zoneTone(day))).not.toContain("--st-");
    }
  });
});

describe("what the week button takes over", () => {
  const days = [aDay({ date: "2026-09-14" }), rest, aDay({ date: "2026-09-16" })];

  it("is the sessions, never the rest days", () => {
    expect(openSessions(days, {}).map((day) => day.date)).toEqual([
      "2026-09-14",
      "2026-09-16",
    ]);
  });

  it("drops a day that has already been taken over", () => {
    const results: Record<string, PlanAdoptResult> = {
      "2026-09-14": { date: "2026-09-14", created: true, workout: null, detail: null },
    };

    expect(openSessions(days, results).map((day) => day.date)).toEqual(["2026-09-16"]);
  });

  it("keeps a day whose first attempt was refused", () => {
    const results: Record<string, PlanAdoptResult> = {
      "2026-09-14": {
        date: "2026-09-14",
        created: false,
        workout: null,
        detail: "Für diesen Tag ist bereits eine bestätigte Einheit hinterlegt",
      },
    };

    expect(openSessions(days, results)).toHaveLength(2);
  });
});

describe("the body of an adoption", () => {
  it("sends the validated fields and nothing composed on the way", () => {
    const body = adoptBody([aDay()]);

    expect(body.replace).toBe(false);
    expect(body.days).toEqual([
      {
        date: "2026-09-14",
        title: "Lockerer Dauerlauf",
        duration_s: 2400,
        zone_label: "Z2",
        purpose: "Grundlage aufbauen ohne Ermüdung.",
      },
    ]);
    // The description the calendar gets is the server's to assemble.
    expect(JSON.stringify(body)).not.toContain("description");
  });

  it("only ever replaces when it is told to", () => {
    expect(adoptBody([aDay()], { replace: true }).replace).toBe(true);
  });
});
