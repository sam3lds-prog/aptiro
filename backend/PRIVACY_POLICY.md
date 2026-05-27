# Aptiro Privacy Policy

**Last updated: 2026-05-27**

## 1. What Aptiro Is

Aptiro is a local-first, self-hosted job-application cockpit. By default, **all
data lives on your own machine or private server.** No personal data is
transmitted to third-party services unless you explicitly configure external
providers.

## 2. Data Stored

| Category | What is stored | Where it lives |
|---|---|---|
| Account | Email address + salted PBKDF2-hashed password | Local DB only |
| Career content | Résumé text, extracted claims, evidence refs, packages | Local DB only |
| Application history | Tracker entries, immutable submission snapshots | Local DB only |
| Research findings | Public-context search results you have approved | Local DB only |
| Notifications | In-app notification center items | Local DB only |
| Audit trail | Append-only server log of mutations (not exportable) | Local DB only |

Plaintext passwords are **never** stored. Credentials are **never** logged and
**never** included in the privacy export bundle.

## 3. Optional Third-Party Services (all strictly opt-in)

None of the following services are contacted unless you explicitly configure
the corresponding environment variable.

### Anthropic AI (`APTIRO_AI_PROVIDER=anthropic`)

When the Anthropic path is enabled, a limited portion of your career content
is sent to the Anthropic API for text rewriting assistance. Specifically:

- The exact text of the claim being rewritten.
- No account identifiers, email addresses, or personal contact information.

The AI grounding gate blocks any output that introduces facts not present in
the source claim before it is shown to you.

### SMTP email (`APTIRO_SMTP_HOST`)

When SMTP is configured, notification digests and alerts are sent to the email
address you provide in notification preferences. No data is sent to a third
party other than the SMTP server you configure.

### Twilio SMS (`APTIRO_TWILIO_SID`)

When Twilio is configured **and** you explicitly enable SMS in notification
preferences, alerts are delivered to the phone number you provide. No data is
sent unless both conditions are met.

### Job providers (Remotive, Greenhouse, Lever, Ashby)

When configured, public job posting data is fetched from these providers'
public APIs. No personal data is transmitted to them.

**No analytics trackers. No advertising networks. No telemetry.**

## 4. Data Retention and Deletion

You own your data and can remove it at any time:

- **Delete all data, keep account:** Profile Vault → Privacy → *Delete all my
  data*, or `DELETE /api/privacy/data`.
- **Delete account and all data:** Privacy → *Delete my account*, or
  `DELETE /api/auth/account` with `{"confirm": "DELETE MY ACCOUNT"}`.

Both operations are immediate and irreversible. The audit trail is excluded from
the privacy export (it is tamper-resistant by design) but is deleted with the
account.

## 5. Security Practices

| Control | Implementation |
|---|---|
| Password hashing | Salted PBKDF2-HMAC-SHA256, 120 000 rounds (stdlib) |
| Bearer tokens | Cryptographically random 256-bit values (`secrets.token_urlsafe`) |
| Session expiry | Opt-in via `APTIRO_SESSION_HOURS`; NULL = no expiry |
| Token rotation | `POST /api/auth/rotate` |
| Signed export links | SHA-256 hashed token, configurable TTL (default 60 min) |
| Rate limiting | Auth endpoints: 100 req/min per IP; mutations: 300 req/min (production) |
| Security headers | `X-Content-Type-Options`, `X-Frame-Options`, `X-XSS-Protection`, `Referrer-Policy`, `Permissions-Policy`, HSTS (production) |
| Data isolation | Every entity is owner-scoped; cross-user access returns 404 (no existence leak) |
| AI safety | Mandatory grounding gate — AI outputs that introduce unsupported facts are blocked before display |

## 6. Open Source

Aptiro is open-source software. You can review the complete source code,
self-host your own instance, and audit every data flow at:

> https://github.com/sam3lds-prog/aptiro

For issues or questions, please open a GitHub issue.
