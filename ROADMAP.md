# Aptiro — Delivery Roadmap (Phase 2 onward)

Phase 1 (**Trust + Export**) is shipped: real PDF/DOCX/TXT/Markdown
ingestion, provenance-tracked claims, the export trust gate
(MD/HTML/DOCX/PDF with rejected/unsupported excluded by default), a
pluggable AI provider that is deterministic-mock by default, Docker /
Postgres / pgvector / Alembic scaffolding, and 63 green tests.

This document is the plan for what comes next. It is a planning
artifact, not code — each phase below becomes its own runnable delivery
pack (code + tests + an "explain-the-diff" `CHANGES` entry) once
selected.

## Rules every future pack inherits

These do not get renegotiated per phase; they are the spine of the
product and are restated here so each pack is held to them.

- **Additive and behavior-preserving.** The Delivery 1–4 + Trust+Export
  contract stays intact. The test suite only grows; it never drops
  below the current count.
- **Single-file `app.py` shape.** The build environment actively
  rewrites and shadows split Python modules — this is documented and
  real, and is why the prototype and this slice are one well-sectioned
  module plus the few additive helpers. Future packs keep that shape;
  fighting the environment produces a broken deliverable.
- **Deterministic, offline tests.** In-memory SQLite, mock providers,
  no network, no credentials. A live key is never required to run the
  app or the suite.
- **Non-negotiables.** No auto-apply or external submission. No
  LinkedIn or auth-walled scraping. No CAPTCHA / anti-bot
  circumvention. No fabricated claims; AI never produces grounded
  truth. Provenance colours, the per-claim approval gate, bullet
  accept/reject/rewrite/lock, the 13-step / 5-agent council, and the
  Axiom fabricated-metric block are all preserved.
- **Explain the diff.** Every pack ships a `CHANGES` section that says
  exactly what was added, what changed in `app.py` (surgically), and
  what was deliberately left untouched.

---

## Phase 2 — Real job discovery & explainable matching  ✅ DELIVERED

> Shipped. Test suite 63 → 83. See `CHANGES.md` for the full diff.
> Paste/URL job import with must-have vs nice-to-have requirements,
> per-criterion score breakdown citing the exact evidence claims, and a
> labelled secondary semantic signal that never affects score/ranking.

**Why this is the natural next step.** The trust, package, and export
half of the product is real; the "find the jobs" half is still mocked.
This makes the input side real while staying entirely inside the
non-negotiables.

**Scope.** Job ingestion gains two real paths: paste a full job
description (extends what exists) and fetch from a public posting URL
that the user explicitly supplies — a server-side fetch of *that* URL
only, robots-respecting, with timeout and response-size limits, HTML
reduced to text. No crawling, no LinkedIn, nothing behind an auth wall.
Requirements are parsed into structured form (must-have vs nice-to-have,
seniority, skills, domain) reusing the deterministic parser patterns
already in the codebase. Jobs are de-duplicated on company + normalised
title + normalised URL, can be archived, and always carry their source
attribution. On matching, the deterministic 0–100 weighted score stays
the single source of truth; what's added is an explainable per-criterion
breakdown surfaced in the UI — every point traced to the evidence claims
that earned it. An optional pgvector semantic-similarity signal is
introduced as a clearly-labelled *secondary* indicator, computed from a
deterministic mock embedding by default (real embeddings only behind the
same env-gated provider pattern as Anthropic), and it never overrides
the deterministic score.

**Done when.** A JD can be imported by paste and by user-supplied URL;
requirements come back as structured must/nice; the score view explains
every point with linked evidence; dedupe and archival work; the semantic
signal is additive, labelled, and deterministic in tests.

**Tests added.** URL fetch over a mocked transport (success, timeout,
oversized, non-HTML, robots-disallowed → refused); requirement
extraction invariants; score-breakdown sums to the total and cites
evidence; semantic signal is deterministic offline and never changes
ranking order on its own.

---

## Phase 3 — Application tracker: close the loop (human-in-loop only)

**Why.** You've ingested truthfully, built a package, and exported it.
The natural next step is tracking what happened to that application —
without the app ever submitting anything. This extends the export work
directly.

**Scope.** A package can become an Application through an explicit human
action ("I applied"). Status is a state machine — drafted → exported →
submitted-by-user → interviewing → offer → rejected → withdrawn — where
every transition is human-initiated and audit-logged. At the moment the
user marks it submitted, a frozen "what I sent" snapshot is stored so
the record is immutable evidence. An ATS-safe single-column export
profile is added alongside the existing renderers. Follow-up reminders
are generated deterministically through the existing mocked notification
provider with a due-date model, and the tracker is included in the
existing privacy export bundle (CSV/JSON).

**Done when.** An application can be created from a package; the submit
snapshot is immutable; every status transition requires explicit user
action and is audited; illegal transitions are rejected; reminders are
deterministic; the tracker appears in the privacy export.

**Tests added.** State-machine legality (illegal transition → 409),
snapshot immutability, an explicit assertion that no code path performs
an outbound application submission, reminder determinism, privacy-bundle
inclusion.

---

## Phase 4 — Multi-user, auth & data isolation

**Why.** Required before any deployment beyond a single local user —
even a private self-hosted instance. It touches every entity, so it is
its own phase with care taken to keep the single-user and test paths
behaviour-preserving.

**Scope.** A user model and standard auth (token/session with hashed
credentials, structured so OAuth can layer on later). Every entity —
sources, claims, strategies, jobs, packages, applications, export
records — is scoped per user. The privacy export/delete endpoints become
per-user. A migration adds `user_id` with a default "local" user so
existing single-user data and the entire current test suite keep passing
unchanged (the suite runs as the default local user; auth-specific tests
are added on top). Rate limiting and startup config validation are added
on the auth surface.

**Done when.** Two users cannot see each other's data (tested); the
existing suite passes under the default-user shim; mutating endpoints
require auth; the privacy bundle is scoped to the caller; the migration
is reversible.

**Tests added.** Cross-user isolation, default-user back-compat, auth
required on mutations, scoped privacy export, migration up/down.

---

## Phase 5 — Grounded AI assist (real provider depth, provenance-safe)

**Why.** The AI provider is pluggable but mock-only in practice. Making
the Anthropic path genuinely useful — bullet rewriting, cover-letter
drafting, richer council critique — is high personal value for an active
job search, but it must never fabricate grounded content and the suite
must stay deterministic and offline.

**Scope.** With the Anthropic provider enabled, bullet rewrite is
constrained to the linked approved claim's facts and cannot introduce
metrics or entities absent from that claim; cover-letter drafting draws
only from accepted bullets; council critiques get natural-language
narratives. The key addition is a provenance verification gate: a
post-generation check that rejects any AI output introducing a fact not
present in the source claim (a pre-emptive complement to Axiom's
existing fabricated-metric block). The mock provider stays the default
and is what tests use; a contract test drives the gate with a stub that
returns a fabricated metric and asserts it is blocked. Token/cost
ceilings, timeouts, and automatic fallback to mock are included.

**Done when.** With provider=anthropic + key, rewrites and drafts work
and any fabricated fact is caught and rejected by the verification gate
(proven with a stub); provider=mock behaviour is unchanged; all prior
tests remain deterministic and offline.

**Tests added.** Verification-gate blocks fabricated facts (stubbed
provider), mock path unchanged, graceful fallback when key/SDK absent,
AI-touched bullets remain provenance-coloured and gate-checked.

---

## Phase 6 — Production operations & observability

**Why.** Hardening for a real deployment, and the finalizer that also
back-fills the few infra niceties skipped so far (e.g. the Alembic
`script.py.mako` template).

**Scope.** Structured logging with request IDs; the `ExportRecord`
audit table generalised to an `AuditEvent` so every mutating action is
recorded; `/healthz` and `/readyz`; a consistent error envelope and
taxonomy; startup config validation that fails fast with a clear message
on bad environment; Alembic migration discipline and tests; a GitHub
Actions workflow running the full suite on every push; a hardened
container (non-root, healthcheck); and Postgres backup/restore notes.

**Done when.** Startup fails fast on invalid config with a clear
message; every mutating action emits an audit event; the health and
readiness probes work; CI runs the suite green on push; the container
runs as non-root.

**Tests added.** Config-validation failure modes, audit-event emission
on each mutation, migration tests, a CI workflow that executes the
existing suite.

---

## Sequencing and the levers you can pull

The default order — 2 → 3 → 4 → 5 → 6 — moves the real *inputs* in
first, then closes the loop, then makes it multi-user, then adds AI
power, then hardens. It is the lowest-risk path because each pack builds
on the last without large rewrites.

Two reorder levers worth knowing:

- If a **hosted or shared deployment** is needed soon, Phase 4
  (multi-user/auth) moves ahead of 2 and 3 — everything downstream is
  cleaner once data is user-scoped, at the cost of a bigger, more
  cross-cutting first pack.
- If **AI-assisted drafting** is the immediate personal priority for an
  active job hunt, Phase 5 can move directly after Phase 1 — it is
  self-contained and gated, and delivers the most day-to-day leverage
  for tailoring applications, with Phases 2–4 following.

Phase 6 (ops) can also be threaded in incrementally rather than saved
for last if you intend to deploy to a server early; the CI workflow in
particular is cheap to add at any point and protects every later pack.
