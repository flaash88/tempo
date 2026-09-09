/**
 * One hook per screen's data, with the cached copy shown while the fresh
 * one is on its way.
 *
 * The four things a screen needs to render honestly are all here: the data,
 * whether it is the first load, whether what is on screen came from the
 * cache, and when it arrived. A screen that has only "loading" and "data"
 * cannot tell the athlete that they are looking at this morning's numbers.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, apiGet, readCache, type Cached } from "./api";

export type Resource<T> = {
  data: T | null;
  /** True only while nothing has been shown yet. */
  loading: boolean;
  /** Set when the last attempt failed; the data may still be readable. */
  error: Error | null;
  fromCache: boolean;
  receivedAt: number | null;
  reload: () => void;
};

export function useResource<T>(path: string | null, deps: unknown[] = []): Resource<T> {
  const [state, setState] = useState<Cached<T> | null>(() =>
    path ? readCache<T>(path) : null,
  );
  const [loading, setLoading] = useState(path !== null);
  const [error, setError] = useState<Error | null>(null);
  const [nonce, setNonce] = useState(0);
  // A reload is always an explicit ask — a retry, or a refresh after
  // something was changed — so it never comes out of a cache.
  const fresh = nonce > 0;
  const alive = useRef(true);

  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
    };
  }, []);

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  useEffect(() => {
    if (path === null) {
      setLoading(false);
      return;
    }
    const cached = readCache<T>(path);
    if (cached) {
      setState(cached);
      setLoading(false);
    } else {
      setLoading(true);
    }

    let cancelled = false;
    apiGet<T>(path, { fresh })
      .then((fresh) => {
        if (cancelled || !alive.current) return;
        setState(fresh);
        setError(null);
      })
      .catch((cause: unknown) => {
        if (cancelled || !alive.current) return;
        // A failed refresh does not throw away what is on screen: it marks
        // it as what it now is, the last known state.
        setError(cause instanceof Error ? cause : new Error(String(cause)));
      })
      .finally(() => {
        if (!cancelled && alive.current) setLoading(false);
      });

    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [path, nonce, ...deps]);

  return {
    data: state?.data ?? null,
    loading: loading && state === null,
    error,
    fromCache: state?.fromCache ?? false,
    receivedAt: state?.receivedAt ?? null,
    reload,
  };
}

export function isUnauthorised(error: Error | null): boolean {
  return error instanceof ApiError && error.status === 401;
}
