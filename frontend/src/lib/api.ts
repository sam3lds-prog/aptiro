/**
 * Aptiro API client.
 *
 * - Resolves base URL from window.APTIRO_API (runtime) or "" (Vite proxy in dev).
 * - Attaches the bearer token from authStore if present.
 * - Throws ApiError with status + body for non-2xx, so pages can show
 *   useful, specific messages.
 */
import { getToken } from "@/stores/auth";

function resolveBase(): string {
  const w = (window as unknown as { APTIRO_API?: string }).APTIRO_API;
  // In dev the Vite proxy forwards /api -> :8000. In prod APTIRO_API can
  // be set to the absolute API host. Both end up at "/api/..."
  return (w || "") + "/api";
}

export class ApiError extends Error {
  status: number;
  body: unknown;
  constructor(message: string, status: number, body?: unknown) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.body = body;
  }
}

type Method = "GET" | "POST" | "PUT" | "PATCH" | "DELETE";

export interface ApiOpts {
  method?: Method;
  body?: unknown;
  signal?: AbortSignal;
  /** When true, body is FormData and Content-Type is left to the browser. */
  multipart?: boolean;
  /** Raw response (e.g. for file downloads). */
  raw?: boolean;
  /** Extra query params. */
  params?: Record<string, string | number | boolean | undefined>;
}

export async function api<T = unknown>(path: string, opts: ApiOpts = {}): Promise<T> {
  const { method = "GET", body, signal, multipart, raw, params } = opts;

  const headers: Record<string, string> = {};
  if (!multipart) headers["Content-Type"] = "application/json";
  const token = getToken();
  if (token) headers["Authorization"] = `Bearer ${token}`;

  let url = resolveBase() + path;
  if (params) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== null) qs.set(k, String(v));
    }
    const s = qs.toString();
    if (s) url += (url.includes("?") ? "&" : "?") + s;
  }

  const init: RequestInit = { method, headers, signal };
  if (body !== undefined) {
    init.body = multipart ? (body as BodyInit) : JSON.stringify(body);
  }

  const res = await fetch(url, init);
  if (raw) return res as unknown as T;

  if (res.status === 204) return undefined as unknown as T;
  const text = await res.text();
  let parsed: unknown;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    parsed = text;
  }

  if (!res.ok) {
    let msg = `HTTP ${res.status}`;
    if (parsed && typeof parsed === "object" && parsed !== null && "detail" in parsed) {
      const d = (parsed as { detail: unknown }).detail;
      if (typeof d === "string") msg = d;
      else if (Array.isArray(d) && d[0] && typeof d[0] === "object") {
        const first = d[0] as { msg?: string };
        if (first.msg) msg = first.msg;
      }
    }
    throw new ApiError(msg, res.status, parsed);
  }
  return parsed as T;
}

/** Build a direct download URL (used for /export which returns a file). */
export function downloadUrl(path: string, params?: Record<string, string | number | boolean>): string {
  let url = resolveBase() + path;
  if (params) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) qs.set(k, String(v));
    const s = qs.toString();
    if (s) url += (url.includes("?") ? "&" : "?") + s;
  }
  return url;
}
