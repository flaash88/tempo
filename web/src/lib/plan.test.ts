import { describe, expect, it } from "vitest";
import {
  adoptBody,
  badgeTone,
  dayDuration,
  openSessions,
  sameWindow,
  zoneColour,
} from "./plan";
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
  it("carries the duration", () => {
    expect(dayDuration(aDay())).toBe("40 min");
  });

  it("has none for a rest day", () => {
    expect(dayDuration(rest)).toBeNull();
  });
});

describe("the type badge", () => {
  it("is the same for every session, whatever zone it is", () => {
    // The fault this replaced: Friday blue and Sunday green, both
    // "Einheit". A badge that changes colour looks like it means
    // something, and this one did not.
    const zones = [1, 2, 3, 4, 5].map((zone) =>
      badgeTone(aDay({ zone, zone_label: `Z${zone}` })),
    );
    for (const tone of zones) expect(tone).toEqual(zones[0]);
  });

  it("tells a session from a rest day without reaching for a hue", () => {
    expect(badgeTone(rest)).not.toEqual(badgeTone(aDay()));
    for (const tone of [badgeTone(aDay()), badgeTone(rest)]) {
      expect(JSON.stringify(tone)).not.toMatch(/--z[1-5]/);
      expect(JSON.stringify(tone)).not.toContain("--st-");
    }
  });
});

describe("the zone's colour", () => {
  it("comes from the zone palette and follows the zone", () => {
    for (const zone of [1, 2, 3, 4, 5]) {
      expect(zoneColour(aDay({ zone, zone_label: `Z${zone}` }))).toBe(`var(--z${zone})`);
    }
  });

  it("is nothing at all on a rest day", () => {
    expect(zoneColour(rest)).toBeNull();
  });

  it("never judges: no status colour appears in the plan's palette", () => {
    // Zone colours describe, status colours judge. A Tuesday is easy or
    // hard, not good or bad.
    for (const zone of [1, 2, 3, 4, 5]) {
      expect(zoneColour(aDay({ zone, zone_label: `Z${zone}` }))).not.toContain("--st-");
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

describe("the calendar and the proposal", () => {
  it("agree only when they are the same week", () => {
    const plan = { from_date: "2026-09-14", to_date: "2026-09-20" };

    expect(sameWindow(plan, { from_date: "2026-09-14", to_date: "2026-09-20" })).toBe(
      true,
    );
  });

  it("do not agree in the case that was reported from the device", () => {
    // Header "07.09. – 13.09." over a proposal for "11.09. – 17.09.".
    const plan = { from_date: "2026-09-11", to_date: "2026-09-17" };

    expect(sameWindow(plan, { from_date: "2026-09-07", to_date: "2026-09-13" })).toBe(
      false,
    );
  });

  it("do not agree when only one end matches", () => {
    const plan = { from_date: "2026-09-14", to_date: "2026-09-20" };

    expect(sameWindow(plan, { from_date: "2026-09-14", to_date: "2026-09-21" })).toBe(
      false,
    );
  });

  it("do not agree while the calendar has nothing loaded", () => {
    expect(sameWindow({ from_date: "2026-09-14", to_date: "2026-09-20" }, null)).toBe(
      false,
    );
  });
});
