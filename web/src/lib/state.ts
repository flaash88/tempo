/**
 * Which of the six states a tile is in.
 *
 * This is the whole of the interface's judgement about a number, and it is
 * one function so that six screens cannot disagree about it. Two rules from
 * CLAUDE.md decide everything here:
 *
 * A value below its minimum history is not shown at all — the tile shows
 * progress instead ("34 / 42 Tagen · verfügbar ab 12.10."). And a value
 * whose last data point is old is never presented as the current state:
 * the tile carries the date it is from.
 *
 * The states are per tile, not per screen. Several tiles being in different
 * states at once is the normal case here, not the exception.
 */

import type { MetricEnvelope } from "./types";

export type TileState = "ready" | "loading" | "empty" | "building" | "stale" | "error";

export type Progress = {
  have: number;
  required: number;
  /** 0..1, for the progress line. */
  ratio: number;
  availableFrom: string | null;
};

export type TileView<T> = {
  state: TileState;
  value: T | null;
  confidence: number;
  progress: Progress | null;
  lastDataPoint: string | null;
};

export type TileInput<T> = {
  envelope: MetricEnvelope<T> | null | undefined;
  loading?: boolean;
  error?: boolean;
};

/**
 * A metric with no history at all is empty; with some but not enough it is
 * building; with a value whose last reading is old it is stale.
 *
 * "Empty" and "building" are deliberately distinct: the first asks the
 * athlete to connect a source, the second asks them to wait, and telling
 * someone to connect a source they already connected is the more annoying
 * of the two mistakes.
 */
export function tileState<T>({ envelope, loading, error }: TileInput<T>): TileView<T> {
  if (loading) {
    return { state: "loading", value: null, confidence: 0, progress: null, lastDataPoint: null };
  }
  if (error || !envelope) {
    return { state: "error", value: null, confidence: 0, progress: null, lastDataPoint: null };
  }

  const progress: Progress | null =
    envelope.required > 0
      ? {
          have: envelope.have,
          required: envelope.required,
          ratio: Math.max(0, Math.min(1, envelope.have / envelope.required)),
          availableFrom: envelope.available_from,
        }
      : null;

  if (envelope.value === null) {
    const nothingAtAll = envelope.have === 0 && envelope.last_data_point === null;
    return {
      state: nothingAtAll ? "empty" : "building",
      value: null,
      confidence: envelope.confidence,
      progress,
      lastDataPoint: envelope.last_data_point,
    };
  }

  return {
    state: envelope.stale ? "stale" : "ready",
    value: envelope.value,
    confidence: envelope.confidence,
    progress,
    lastDataPoint: envelope.last_data_point,
  };
}

/** How confident the tile looks: three bands, so the eye can sort them. */
export function confidenceBand(confidence: number): "high" | "medium" | "low" {
  if (confidence >= 0.75) return "high";
  if (confidence >= 0.4) return "medium";
  return "low";
}
