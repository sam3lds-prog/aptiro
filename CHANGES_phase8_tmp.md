# CHANGES — Upgrade Phase 8: Auth Hardening & Launch Security

Phase 8 is **additive and behavior-preserving**. Every existing invariant
from Phases 1–7 is untouched: all prior tests stay green and the suite grows
**180 → 198** (18 new tests, 0 removed, 0 modified). No new Python
dependencies — all cryptographic operations use the stdlib (`hashlib`,
`hmac`, `secrets`). The zero-config local default — auth off, no expiry,
no rate limits — is fully preserved.

## Added

### Backend (`app.py`)

- **`ExportToken` model** — signed, expiring download links for package
  exports. Raw token never stored; SHA-256 hash stored in DB.
  `POST /api/packages/{id}/export/sign` creates a link.
  `GET /api/exports/{token}` serves the file without a bearer token.
  Fields: `owner_id`, `package_id`, `token_hash`, `format`, `artifact`,
  `include_unsupported`, `expires_at`, `used_at`.

- **`_p8_ensure_columns()`** — adds `token_expires_at DATETIME NULL`
  to the `user` table at startup (additive, idempotent).

- **Session expiry (opt-in)** — `APTIRO_SESSION_HOURS` (default `0` = no
  expiry). When set:
  - `POST /api/auth/rotate` issues a fresh token and stamps `token_expires_at`.
  - The `_p8_security_middleware` rejects expired tokens with 401
    `"Session expired. Please sign in again."` for authenticated paths.
  - NULL `token_expires_at` (all existing/legacy tokens) is treated as
    no expiry — fully backwards compatible.

- **`POST /api/auth/rotate`** — issues a new bearer token for the current
  user. If `APTIRO_SESSION_HOURS > 0`, stamps `token_expires_at`. Returns
  `{token, expires_at}`. Rejects the default local user (403).

- **`DELETE /api/auth/account`** — confirmed hard-delete. Caller must pass
  `{"confirm": "DELETE MY ACCOUNT"}` exactly. Deletes all owned entities
  (Sources, Claims, Strategies, Jobs, Packages, Applications, ExportTokens,
  notification preferences, in-app notifications, research findings) and the
  User row itself. Default local user is rejected (403). Returns 204.

- **In-memory rate limiter** (`_p8_rate_ok`) — sliding-window, stdlib only,
  thread-safe. **Disabled by default in dev/test.** Enabled when
  `APTIRO_ENV=production` or `APTIRO_AUTH_RATE_LIMIT` is explicitly set:
  - Auth endpoints (`/api/auth/login`, `/api/auth/register`): 100 req/min/IP
    (configurable via `APTIRO_AUTH_RATE_LIMIT`).
  - All other mutating endpoints: 300 req/min/IP (`APTIRO_MUTATE_RATE_LIMIT`).
  - Returns 429 with `Retry-After: 60` header when triggered.

- **`_p8_security_middleware`** (outermost ASGI middleware):
  - Rate limiting (above).
  - Token expiry check (above).
  - Security headers on **every response**:
    `X-Content-Type-Options: nosniff`
    `X-Frame-Options: DENY`
    `X-XSS-Protection: 1; mode=block`
    `Referrer-Policy: strict-origin-when-cross-origin`
    `Permissions-Policy: camera=(), microphone=(), geolocation=()`
    `Strict-Transport-Security` (production only; 2-year max-age).

- **`GET /api/legal/privacy`** and **`GET /api/legal/terms`** — serve the
  privacy policy and terms of service as markdown strings.

- **Production warning** — when `APTIRO_ENV=production` but `APTIRO_AUTH`
  is not set, a startup warning is logged to stderr.

- **`upgrade_phases_shipped`** health field updated to include `8`.

### Backend — migration

- **`0010_phase8_auth_hardening`** — creates `exporttoken` table and adds
  `token_expires_at` column to `user`. Chains to `0009_phase7_notifications`.
  Reversible.

### Frontend (`frontend/src/`)

- **`lib/api.ts`** — 401 with an active token auto-signs out (session
  expired/revoked). 429 surfaces a friendly "Too many requests" message.
  New exports: `signExportLink()`, `rotateToken()`, `deleteAccount()`,
  `legalDoc()`.

- **`lib/types.ts`** — new types: `ExportToken`, `RotateOut`,
  `SignedExportLink`, `LegalDoc`.

- **`pages/Privacy.tsx`** — new "Delete Account" section (inline confirm
  input requiring exact phrase; only shown when auth is on and user is not
  the default local user). New "Legal" section serving privacy policy and
  ToS inline. Data export / wipe unchanged.

### Docs

- `PRIVACY_POLICY.md` — full privacy policy (served by `/api/legal/privacy`
  and as a standalone doc).
- `TERMS_OF_SERVICE.md` — full terms of service (served by `/api/legal/terms`).

## Config additions (`.env.example`)

```
# Phase 8: auth hardening
APTIRO_ENV=development
APTIRO_SESSION_HOURS=0
APTIRO_AUTH_RATE_LIMIT=100
APTIRO_MUTATE_RATE_LIMIT=300
APTIRO_EXPORT_SECRET=change-me-in-production
```

## Fast-follow (not this phase, not launch-blocking)

Explicitly deferred to a post-launch iteration:
- Email verification on registration
- Password-reset flow
- OAuth (GitHub / Google)
- Refresh token rotation

## Explicitly NOT changed (non-negotiables honored)

- Default behavior is single-user, auth-free, no rate limits, no session
  expiry — zero friction for local / self-hosted installs.
- No auto-submit, no scraping, no fabricated content. The grounding gate,
  export trust gate, provenance controls, immutable snapshot, and council are
  all unchanged.
- Mock providers stay the default; app + full suite run offline with no key.
- No existing endpoint/model contract changed without migration + test.

## Validation

`pytest -q` → **198 passed**. End-to-end: security headers on every response;
signed export link created, served (no bearer token), expired (410), invalid
(403); account deletion wipes User row + all owned Sources; rate limiter fires
429 at configured threshold; token rotation issues new token; legal endpoints
return markdown content; health reports `upgrade_phases_shipped` includes 8.
