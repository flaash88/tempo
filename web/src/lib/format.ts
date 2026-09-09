/** German formatting. Every number the interface shows passes through here. */

const LOCALE = "de-DE";

export function formatDate(value: string | null | undefined, fallback = "—"): string {
  if (!value) return fallback;
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return fallback;
  return new Intl.DateTimeFormat(LOCALE, { day: "2-digit", month: "2-digit" }).format(date);
}

export function formatLongDate(value: string | Date): string {
  const date = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(LOCALE, {
    weekday: "long",
    day: "numeric",
    month: "long",
  }).format(date);
}

export function formatTime(value: string | null | undefined): string {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(LOCALE, { hour: "2-digit", minute: "2-digit" }).format(date);
}

export function formatNumber(value: number | null | undefined, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return "—";
  return new Intl.NumberFormat(LOCALE, {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  }).format(value);
}

/** Seconds as h:mm or mm:ss, whichever the magnitude calls for. */
export function formatDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return "—";
  const total = Math.round(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  if (hours > 0) return `${hours}:${String(minutes).padStart(2, "0")} h`;
  return `${minutes}:${String(rest).padStart(2, "0")}`;
}

/** Sleep and other long spans, as the mock-up writes them: "7 h 24 min". */
export function formatHoursMinutes(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined) return "—";
  const total = Math.round(seconds / 60);
  return `${Math.floor(total / 60)} h ${String(total % 60).padStart(2, "0")} min`;
}

export function formatPace(secondsPerKm: number | null | undefined): string {
  if (secondsPerKm === null || secondsPerKm === undefined || secondsPerKm <= 0) return "—";
  const total = Math.round(secondsPerKm);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

export function formatDistance(metres: number | null | undefined): string {
  if (metres === null || metres === undefined) return "—";
  if (metres < 1000) return `${formatNumber(metres)} m`;
  return `${formatNumber(metres / 1000, 1)} km`;
}

export function formatEuro(value: number): string {
  return new Intl.NumberFormat(LOCALE, { style: "currency", currency: "EUR" }).format(value);
}

/** "vor 3 Minuten" for the offline banner's timestamp. */
export function formatRelative(value: string | number | null | undefined): string {
  if (value === null || value === undefined) return "unbekannt";
  const then = typeof value === "number" ? value : new Date(value).getTime();
  if (Number.isNaN(then)) return "unbekannt";
  const seconds = Math.round((Date.now() - then) / 1000);
  const relative = new Intl.RelativeTimeFormat(LOCALE, { numeric: "auto" });
  if (seconds < 60) return relative.format(-seconds, "second");
  if (seconds < 3600) return relative.format(-Math.round(seconds / 60), "minute");
  if (seconds < 86400) return relative.format(-Math.round(seconds / 3600), "hour");
  return relative.format(-Math.round(seconds / 86400), "day");
}

/** The clock time of a cached response, for "Stand 05:58". */
export function formatClock(at: number | null): string {
  if (at === null) return "—";
  return new Intl.DateTimeFormat(LOCALE, { hour: "2-digit", minute: "2-digit" }).format(
    new Date(at),
  );
}

/** "45:00 · 9,5 km", or just "45:00" when there is no distance. */
export function formatTarget(
  seconds: number | null | undefined,
  metres: number | null | undefined,
): string {
  const parts = [
    seconds === null || seconds === undefined ? null : formatDuration(seconds),
    metres === null || metres === undefined ? null : formatDistance(metres),
  ].filter((part): part is string => part !== null);
  return parts.length > 0 ? parts.join(" · ") : "ohne Vorgabe";
}
