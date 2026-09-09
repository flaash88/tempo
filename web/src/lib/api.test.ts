/** The cache that makes the last known state readable offline. */

import { beforeEach, describe, expect, it } from "vitest";
import { clearCache, readCache } from "./api";

describe("the response cache", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("returns nothing for a path it has never seen", () => {
    expect(readCache("/api/today")).toBeNull();
  });

  it("returns what was stored, marked as coming from the cache", () => {
    localStorage.setItem(
      "tempo:v1:/api/today",
      JSON.stringify({ data: { date: "2026-09-09" }, receivedAt: 1_757_000_000_000 }),
    );

    const cached = readCache<{ date: string }>("/api/today");

    expect(cached?.data.date).toBe("2026-09-09");
    expect(cached?.fromCache).toBe(true);
    // The timestamp is the point: a screen has to be able to say how old
    // what it is showing actually is.
    expect(cached?.receivedAt).toBe(1_757_000_000_000);
  });

  it("ignores a half-written entry rather than throwing", () => {
    localStorage.setItem("tempo:v1:/api/today", "{not json");

    expect(readCache("/api/today")).toBeNull();
  });

  it("ignores an entry with no timestamp — it could not be dated", () => {
    localStorage.setItem("tempo:v1:/api/today", JSON.stringify({ data: {} }));

    expect(readCache("/api/today")).toBeNull();
  });

  it("clears only its own keys", () => {
    localStorage.setItem("tempo:v1:/api/today", JSON.stringify({ data: 1, receivedAt: 1 }));
    localStorage.setItem("etwas-anderes", "bleibt");

    clearCache();

    expect(readCache("/api/today")).toBeNull();
    expect(localStorage.getItem("etwas-anderes")).toBe("bleibt");
  });
});
