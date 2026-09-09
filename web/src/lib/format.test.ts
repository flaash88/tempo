/** German formatting, including the two units the mock-ups get wrong. */

import { describe, expect, it } from "vitest";
import {
  formatDistance,
  formatDuration,
  formatHoursMinutes,
  formatNumber,
  formatPace,
} from "./format";

describe("formatting", () => {
  it("writes a missing value as a dash, never as zero", () => {
    // "0 km" is a claim about a rest day; "—" is the absence of a reading.
    expect(formatNumber(null)).toBe("—");
    expect(formatDistance(null)).toBe("—");
    expect(formatDuration(null)).toBe("—");
    expect(formatPace(null)).toBe("—");
  });

  it("formats distances the way the screens read them", () => {
    expect(formatDistance(3500)).toBe("3,5 km");
    expect(formatDistance(800)).toBe("800 m");
  });

  it("formats a pace as minutes and seconds", () => {
    expect(formatPace(359)).toBe("5:59");
  });

  it("formats sleep as hours and minutes", () => {
    expect(formatHoursMinutes(26_640)).toBe("7 h 24 min");
  });

  it("switches from mm:ss to h:mm when an hour is passed", () => {
    expect(formatDuration(1_800)).toBe("30:00");
    expect(formatDuration(5_400)).toBe("1:30 h");
  });
});
