/**
 * The tile state machine, against the situation this instance is actually
 * in — not against the mock-up's full house of numbers.
 */

import { describe, expect, it } from "vitest";
import { tileState } from "./state";
import type { MetricEnvelope } from "./types";

function envelope<T>(overrides: Partial<MetricEnvelope<T>> = {}): MetricEnvelope<T> {
  return {
    value: null,
    confidence: 0,
    days_of_history: 0,
    have: 0,
    required: 0,
    available_from: null,
    last_data_point: null,
    stale: false,
    ...overrides,
  };
}

describe("tileState", () => {
  it("shows progress, not a value, below the minimum history", () => {
    // Readiness on this instance: some nights on record, not enough of them.
    const view = tileState({
      envelope: envelope<number>({
        have: 5,
        required: 14,
        available_from: "2026-09-23",
        last_data_point: "2026-06-03",
      }),
    });

    expect(view.state).toBe("building");
    expect(view.value).toBeNull();
    expect(view.progress).toEqual({
      have: 5,
      required: 14,
      ratio: 5 / 14,
      availableFrom: "2026-09-23",
    });
  });

  it("tells 'nothing yet' apart from 'not enough yet'", () => {
    const nothing = tileState({ envelope: envelope<number>({ required: 14 }) });
    const some = tileState({
      envelope: envelope<number>({ have: 3, required: 14, last_data_point: "2026-06-03" }),
    });

    // Asking someone to connect a source they already connected is the
    // more annoying of the two mistakes.
    expect(nothing.state).toBe("empty");
    expect(some.state).toBe("building");
  });

  it("marks a value whose last reading is old", () => {
    const view = tileState({
      envelope: envelope<number>({
        value: 61,
        confidence: 0.4,
        have: 20,
        required: 14,
        last_data_point: "2026-06-03",
        stale: true,
      }),
    });

    expect(view.state).toBe("stale");
    expect(view.value).toBe(61);
    expect(view.lastDataPoint).toBe("2026-06-03");
  });

  it("shows a fresh value as ready", () => {
    const view = tileState({
      envelope: envelope<number>({
        value: 61,
        confidence: 0.9,
        have: 30,
        required: 14,
        last_data_point: "2026-09-09",
      }),
    });

    expect(view.state).toBe("ready");
  });

  it("prefers loading over everything, and error over missing data", () => {
    expect(tileState({ envelope: envelope<number>(), loading: true }).state).toBe("loading");
    expect(tileState({ envelope: null }).state).toBe("error");
    expect(tileState({ envelope: envelope<number>(), error: true }).state).toBe("error");
  });

  it("never reports a ratio outside 0..1", () => {
    const view = tileState({
      envelope: envelope<number>({ have: 90, required: 42, value: 1 }),
    });

    expect(view.progress?.ratio).toBe(1);
  });

  it("carries no progress when the metric needs no history", () => {
    const view = tileState({ envelope: envelope<number>({ value: 5, required: 0 }) });

    expect(view.progress).toBeNull();
    expect(view.state).toBe("ready");
  });
});
