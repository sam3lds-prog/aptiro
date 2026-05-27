/**
 * Aptiro API client — Phase 8 update.
 *
 * Changes from Phase 7:
 *   • 401 with an active token → auto sign-out (session expired or revoked)
 *   • 429 → friendly rate-limit error message
 *   • signExportLink() — creates a signed, expiring download link
 *   • rotateToken()   — issues a fresh bearer token
 *   • deleteAccount() — confirmed hard-delete of account + all owned data
 *   • legalDoc()      — fetch privacy policy or terms of service
 */
import { getToken, useAuth } from "@/stores/auth";

function resolveBase(): string {
  const w = (window as unknown as { APTIRO_API?: string }).APTIRO_API;
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
    // ── Phase 8: handle specific status codes ──────────────────────────
    if (res.status === 401 && token) {
      // Our token is no longer valid (expired or revoked) — sign out so
      // the login screen appears on the next navigation.
      useAuth.getState().signOut();
    }

    let msg = `HTTP ${res.status}`;

    if (res.status === 429) {
      msg = "Too many requests — please wait a moment before trying again.";
    } else if (
      parsed &&
      typeof parsed === "object" &&
      parsed !== null &&
      "detail" in parsed
    ) {
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
export function downloadUrl(
  path: string,
  params?: Record<string, string | number | boolean>
): string {
  let url = resolveBase() + path;
  if (params) {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) qs.set(k, String(v));
    const s = qs.toString();
    if (s) url += (url.includes("?") ? "&" : "?") + s;
  }
  return url;
}

// ── Phase 8 helpers ────────────────────────────────────────────────────────

export interface SignedExportLink {
  token: string;
  url: string;
  expires_at: string;
  format: string;
  artifact: string;
}

/**
 * Create a signed, expiring export download link for the given package.
 * The returned `url` can be fetched without a bearer token.
 */
export async function signExportLink(
  packageId: string,
  options: {
    format?: string;
    artifact?: string;
    include_unsupported?: boolean;
    ttl_minutes?: number;
  } = {}
): Promise<SignedExportLink> {
  return api<SignedExportLink>(`/packages/${packageId}/export/sign`, {
    method: "POST",
    params: {
      format: options.format ?? "md",
      artifact: options.artifact ?? "resume",
      include_unsupported: options.include_unsupported ?? false,
      ttl_minutes: options.ttl_minutes ?? 60,
    },
  });
}

export interface RotateOut {
  token: string;
  expires_at: string | null;
}

/** Rotate the current bearer token. Call this after login when session expiry is desired. */
export async function rotateToken(): Promise<RotateOut> {
  return api<RotateOut>("/auth/rotate", { method: "POST" });
}

/**
 * Hard-delete the calling user's account and all owned data.
 * The caller must pass `confirm = "DELETE MY ACCOUNT"` explicitly.
 */
export async function deleteAccount(confirm: string): Promise<void> {
  return api("/auth/account", {
    method: "DELETE",
    body: { confirm },
  });
}

export interface LegalDoc {
  content: string;
  format: "markdown";
  last_updated: string;
}

/** Fetch the privacy policy or terms of service document. */
export async function legalDoc(doc: "privacy" | "terms"): Promise<LegalDoc> {
  return api<LegalDoc>(`/legal/${doc}`);
}
