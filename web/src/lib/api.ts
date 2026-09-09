/**
 * The API client, and the offline story that goes with it.
 *
 * Stale-while-revalidate happens twice over, on purpose. The service worker
 * does it at the network layer so a cold start with no connection still
 * renders; this module does it in the app so the interface knows *when* the
 * data it is showing arrived. A screen that silently shows yesterday's
 * numbers is the failure this whole project is built to avoid, so the
 * timestamp travels with the payload and every screen shows it.
 */

const CACHE_PREFIX = "tempo:v1:";

export class ApiError extends Error {
  readonly status: number;
  readonly detail?: unknown;

  constructor(message: string, status: number, detail?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

export type Cached<T> = {
  data: T;
  /** When this body was received, as epoch milliseconds. */
  receivedAt: number;
  /** True when it came from the local cache rather than the network. */
  fromCache: boolean;
};

function cacheKey(path: string): string {
  return `${CACHE_PREFIX}${path}`;
}

export function readCache<T>(path: string): Cached<T> | null {
  try {
    const raw = localStorage.getItem(cacheKey(path));
    if (!raw) return null;
    const parsed = JSON.parse(raw) as { data: T; receivedAt: number };
    if (typeof parsed.receivedAt !== "number") return null;
    return { data: parsed.data, receivedAt: parsed.receivedAt, fromCache: true };
  } catch {
    // A private window, a full quota, a half-written entry: none of those
    // are worth breaking the screen over.
    return null;
  }
}

function writeCache<T>(path: string, data: T, receivedAt: number): void {
  try {
    localStorage.setItem(cacheKey(path), JSON.stringify({ data, receivedAt }));
  } catch {
    /* see above */
  }
}

export function clearCache(): void {
  try {
    for (const key of Object.keys(localStorage)) {
      if (key.startsWith(CACHE_PREFIX)) localStorage.removeItem(key);
    }
  } catch {
    /* see above */
  }
}

async function parseError(response: Response): Promise<never> {
  let detail: unknown;
  try {
    const body = (await response.json()) as { detail?: unknown };
    detail = body?.detail;
  } catch {
    detail = undefined;
  }
  const message =
    typeof detail === "string"
      ? detail
      : `Die Anfrage ist mit ${response.status} fehlgeschlagen`;
  throw new ApiError(message, response.status, detail);
}

export async function apiGet<T>(
  path: string,
  { cache = true, fresh = false } = {},
): Promise<Cached<T>> {
  const response = await fetch(path, {
    headers: {
      Accept: "application/json",
      // Both caches in the way have to be told: the browser's, and the
      // service worker's stale-while-revalidate route, which matches on
      // this header precisely so a refresh after a mutation can opt out.
      ...(fresh ? { "Cache-Control": "no-cache" } : {}),
    },
    cache: fresh ? "no-store" : "default",
    credentials: "same-origin",
  });
  if (!response.ok) await parseError(response);
  const data = (await response.json()) as T;
  const receivedAt = Date.now();
  if (cache) writeCache(path, data, receivedAt);
  return { data, receivedAt, fromCache: false };
}

export async function apiSend<T>(
  path: string,
  { method = "POST", body }: { method?: string; body?: unknown } = {},
): Promise<T> {
  const response = await fetch(path, {
    method,
    headers: {
      Accept: "application/json",
      ...(body === undefined ? {} : { "Content-Type": "application/json" }),
    },
    credentials: "same-origin",
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!response.ok) await parseError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}
