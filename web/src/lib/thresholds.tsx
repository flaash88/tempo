/**
 * The thresholds, from the server, once per session.
 *
 * CLAUDE.md is explicit: the numbers live in tempo/metrics/thresholds.py and
 * reach the interface through GET /api/thresholds. Nothing here hard codes
 * 14, 21, 42 or 7 — not as a constant, not as a fallback, not in a sentence.
 * A screen that cannot reach the thresholds says so instead of guessing.
 */

import { createContext, useContext, type ReactNode } from "react";
import { useResource } from "./useResource";
import type { ThresholdsResponse } from "./types";

type ThresholdsContext = {
  thresholds: ThresholdsResponse | null;
  loading: boolean;
};

const Context = createContext<ThresholdsContext>({ thresholds: null, loading: true });

export function ThresholdsProvider({ children }: { children: ReactNode }) {
  const { data, loading } = useResource<ThresholdsResponse>("/api/thresholds");
  return (
    <Context.Provider value={{ thresholds: data, loading }}>{children}</Context.Provider>
  );
}

export function useThresholds(): ThresholdsContext {
  return useContext(Context);
}

/**
 * A unit, in German, in the case the sentence around it needs.
 *
 * The API names the unit ("days", "nights", "performances") because which
 * one applies is a property of the metric; putting the German words here
 * rather than in the API keeps the interface's grammar out of the metric
 * engine. "42 Tagen" and "14 Nächten" are different words for a reason.
 */
export function germanUnit(
  unit: string,
  count: number,
  form: "dative" | "nominative" = "dative",
): string {
  if (unit === "nights") return count === 1 ? "Nacht" : form === "dative" ? "Nächten" : "Nächte";
  if (unit === "days") return count === 1 ? "Tag" : form === "dative" ? "Tagen" : "Tage";
  if (unit === "performances") return count === 1 ? "Bestleistung" : "Bestleistungen";
  return unit;
}

export function useHistoryUnit(metric: string, count: number): string {
  const { thresholds } = useThresholds();
  const entry = thresholds?.minimum_history?.[metric];
  if (!entry) return "";
  return germanUnit(entry.unit, count);
}

export function useMinimumLabel(metric: string): string | null {
  const { thresholds } = useThresholds();
  return thresholds?.minimum_history?.[metric]?.label_de ?? null;
}
