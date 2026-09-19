"use client";

export const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const KEY_STORAGE = "faultscope.apiKey";

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Json = any;

export function getApiKey(): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(KEY_STORAGE) || "";
  } catch {
    return "";
  }
}

export function setApiKey(key: string): void {
  try {
    window.localStorage.setItem(KEY_STORAGE, key);
    window.dispatchEvent(new Event("faultscope-key"));
  } catch {
    /* storage unavailable: the key then only lives for this page view */
  }
}

export class ApiError extends Error {
  status: number;
  body: Json;
  constructor(status: number, message: string, body: Json) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

function describe(status: number, body: Json): string {
  if (status === 401) return "Not authorised: set a valid API key in the sidebar.";
  if (status === 403) return "Your API key's role is not allowed to do this.";
  if (status === 503 && body?.detail) return String(body.detail);
  if (body?.errors) return (body.errors as string[]).join("; ");
  if (body?.detail) return typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
  if (body?.error) return String(body.error);
  return `Request failed (${status})`;
}

export async function api<T = Json>(path: string, init: { method?: string; body?: Json; auth?: boolean } = {}): Promise<T> {
  const headers: Record<string, string> = {};
  if (init.body !== undefined) headers["Content-Type"] = "application/json";
  const key = getApiKey();
  if (key) headers["X-API-Key"] = key;
  let response: Response;
  try {
    response = await fetch(`${API_URL}${path}`, {
      method: init.method || "GET",
      headers,
      body: init.body !== undefined ? JSON.stringify(init.body) : undefined,
      cache: "no-store",
    });
  } catch {
    throw new ApiError(0, `Cannot reach the control plane at ${API_URL}. Is it running?`, null);
  }
  let body: Json = null;
  try {
    body = await response.json();
  } catch {
    /* empty or non-JSON body */
  }
  if (!response.ok) throw new ApiError(response.status, describe(response.status, body), body);
  return body as T;
}

export const fmt = {
  ms: (v: number | null | undefined) => (v === null || v === undefined ? "–" : v >= 1000 ? `${(v / 1000).toFixed(2)} s` : `${Math.round(v * 10) / 10} ms`),
  pct: (v: number | null | undefined) => (v === null || v === undefined ? "–" : `${Math.round(v * 10) / 10}%`),
  num: (v: number | null | undefined) => (v === null || v === undefined ? "–" : String(v)),
  time: (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleTimeString() : "–"),
  datetime: (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString() : "–"),
  ago: (iso: string | null | undefined) => {
    if (!iso) return "never";
    const s = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
    return s < 60 ? `${s}s ago` : s < 3600 ? `${Math.round(s / 60)}m ago` : `${Math.round(s / 3600)}h ago`;
  },
};
