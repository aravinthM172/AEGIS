"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, Json, api } from "./api";

export interface Polled<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  refresh: () => void;
}

/** Fetch `path` now and every `intervalMs` (0 = once). Keeps the last good data if a refresh fails. */
export function usePoll<T = Json>(path: string | null, intervalMs = 3000): Polled<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const alive = useRef(true);

  const load = useCallback(async () => {
    if (!path) return;
    try {
      const result = await api<T>(path);
      if (alive.current) {
        setData(result);
        setError(null);
      }
    } catch (e) {
      if (alive.current) setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      if (alive.current) setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    alive.current = true;
    setLoading(true);
    load();
    if (!intervalMs) return () => { alive.current = false; };
    const timer = window.setInterval(load, intervalMs);
    return () => {
      alive.current = false;
      window.clearInterval(timer);
    };
  }, [load, intervalMs]);

  return { data, error, loading, refresh: load };
}

export function useApiKey(): [string, (k: string) => void] {
  const [key, setKey] = useState("");
  useEffect(() => {
    const read = () => setKey(window.localStorage.getItem("faultscope.apiKey") || "");
    read();
    window.addEventListener("faultscope-key", read);
    return () => window.removeEventListener("faultscope-key", read);
  }, []);
  return [key, setKey];
}
