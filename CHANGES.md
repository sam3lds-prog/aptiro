# CHANGES — Phase 5: Grounded AI assist (provenance-safe)

Phase 5 is **additive and behavior-preserving**. Every Phase 1–4 +
Delivery 1–4 invariant is untouched: the suite stays green and grew
**109 → 122** (13 new tests, 0 removed, 0 modified). Single-file
`app.py` shape preserved; no schema change, so **no migration**. No new
dependencies — the mock provider stays the default and the whole suite
runs offline with no key. The defining guarantee: **AI may suggest
phrasing, never facts** — every AI output passes a provenance
verification gate before it can touch a package.

## Added

- **Provenance verification gate** (`verify_grounded`). A
  post-generation check applied to *any* AI text before it can be
  stored: it rejects output that introduces a hard fact — a metric, a
  standalone number, or a proper-noun entity — that is not present in
  the linked approved claim's evidence. This is the pre-emptive
  complement to Axiom's existing fabricated-metric block (Axiom catches
  it in the council; the gate stops it ever being written).
- **Grounded AI bullet rewrite** —
  `POST /api/packages/{id}/bullets/{bid}/ai-rewrite`. Prompts the
  provider with *only* the linked claim's evidence, runs the gate on the
  result, and returns `{suggestion, grounded, violations, applied}`. It
  **never auto-applies**: `apply=true` writes the suggestion *only if
  grounded*, preserving `original_text` and setting status `rewritten`.
  A bullet with no linked approved claim cannot be grounded at all.
- **Grounded AI cover letter** —
  `POST /api/packages/{id}/ai-cover-letter`. Drafts strictly from the
  package's *accepted* bullets, gates the draft against the union of
  their evidence, and saves it only if grounded (else returns the
  violations and changes nothing). 409 if there are no accepted bullets.
- **Advisory council narrative** —
  `POST /api/packages/{id}/ai-council-narrative`. A plain-language
  summary of the latest deterministic council run's findings. It is
  clearly labelled advisory and **does not touch the council flow,
  verdicts, readiness, or any bullet** — so the heavily-tested
  deterministic 13-step / 5-agent review is completely unchanged.
- **Hardened provider** (`ai_provider.py`). Anthropic path now has an
  env-tunable timeout and a hard `max_tokens` ceiling
  (`APTIRO_AI_TIMEOUT`, `APTIRO_AI_MAX_TOKENS`); still falls back to the
  deterministic mock on any misconfiguration. A `_ai_provider()`
  indirection in `app.py` is the single seam (tests inject stubs here).
- **Frontend.** A "✦ AI suggest" action on every bullet (loads a
  grounded suggestion into the editor for review; a blocked suggestion
  surfaces the violation and is never applied) and a "✦ AI cover
  letter" action, both clearly labelled as gated.
- **Tests (`test_app.py`, +13):** the gate blocks fabricated
  metrics/entities and passes clean rephrases; mock rewrite is
  deterministic and never auto-applied; a grounded stub applies and
  keeps `original_text`; **the contract test** — a stub fabricating
  "$999M … at Initech" is blocked and never written; unlinked bullets
  can't be grounded; cover letter requires accepted bullets and rejects
  fabricated drafts; mock is default + deterministic; anthropic-without-
  key falls back to mock; an AI-rewritten bullet is still subject to the
  provenance accept-gate (409); council narrative is advisory +
  deterministic and needs a run; Phase-5 health.

## Changed in `app.py` (surgical, additive only)

- New gate + endpoints only; no existing endpoint or model changed. The
  council `_C` helper and the deterministic orchestrator are byte-for-
  byte unchanged. `/api/health` adds an `ai_assist` block;
  `latest_phase` stays `4` and `phases_shipped` stays `[1,2,3]` **on
  purpose** (pinned by earlier tests). The `ai_assist` block is the
  live Phase-5 capability signal.

## Explicitly NOT changed (non-negotiables honored)

- No fabricated content can be stored: the model is never on the trust
  path; the gate is mandatory and pre-emptive; nothing auto-applies.
- Mock stays the default; app + full suite run offline with no key.
- No auto-submit / external submission / network egress beyond the
  optional, env-gated provider call; no scraping; no CAPTCHA bypass.
  Provenance colours, claim controls, bullet
  accept/reject/rewrite/lock, the deterministic council + Axiom block,
  the export trust gate, Phase 2 scoring + semantic signal, Phase 3
  immutable tracker snapshot, and Phase 4 per-user isolation are all
  unchanged.

## Validation

`pytest -q` → **122 passed**. End-to-end with a real résumé: mock
rewrite is deterministic and not auto-applied; a grounded stub applies
and preserves the original; a stub fabricating "$999M ARR … at Initech"
is **blocked with 6 violations and never written**; a fabricated cover-
letter draft ("$880M at Hooli") is not saved; the council narrative is
deterministic and leaves readiness untouched; health reports the
`ai_assist` block with `grounding_gate true`, `auto_apply false`.

---

# CHANGES — Phase 4: Multi-user, auth & data isolation

Phase 4 is **additive and behavior-preserving** via a default-user
shim. Every Phase 1–3 + Delivery 1–4 invariant is untouched: the suite
stays green and grew **99 → 109** (10 new tests, 0 removed, 0 modified).
Single-file `app.py` shape preserved; no new dependencies (auth is
stdlib `pbkdf2_hmac` + `secrets`). With auth **off** (the default),
every request runs as the built-in `local` user and the app behaves
exactly as it did in Phase 3.

## Added

- **`User` model + `/api/auth`** (`register`, `login`, `me`). Passwords
  are salted PBKDF2-HMAC-SHA256 (120k rounds, stdlib); login issues an
  opaque bearer token. No new packages, no external service.
- **`owner_id` on the six owner-rooted tables** (`source`,
  `profileclaim`, `strategy`, `jobposting`, `applicationpackage`,
  `application`), defaulted to the `local` user. Child tables
  (bullets, refs, runs, …) are reachable only through an owned parent,
  so scoping the roots gives complete isolation.
- **Request-scoped identity.** An ASGI middleware resolves the bearer
  token to a user id into a `ContextVar` (default = `local`). It uses
  the *same* session the request will use (honouring test overrides),
  so token resolution and data are always consistent.
- **Per-user scoping.** `_scoped()` / `_get_owned()` filter every list
  and get-by-id path for the owned resources; cross-user access returns
  **404 with no existence leak**. Creates stamp `owner_id` from the
  caller. Dedupe, the active-strategy lookup, matches, and the privacy
  export/wipe are all per-user.
- **Auth enforcement (opt-in).** With `APTIRO_AUTH=on`, mutating methods
  (POST/PUT/PATCH/DELETE) require a valid token (401 otherwise); reads
  and `/api/auth/*` + `/api/health` stay open. Default is off, so the
  full prior suite and existing single-user data are unaffected.
- **Self-heal + migration.** The additive column self-heal now also
  adds `owner_id`, and a backfill assigns every pre-Phase-4 row to the
  `local` user (no data loss). Alembic
  `0004_phase4_multiuser_auth` creates the `user` table, adds the
  columns + indexes, backfills, and seeds the local user — reversible.
- **Frontend.** A login/register screen that appears **only** when the
  server reports auth enabled (otherwise the app loads straight through,
  unchanged); the token is attached to every API call; a "Log out"
  control shows the signed-in account.
- **Tests (`test_app.py`, +10):** register/login/me roundtrip; dupe +
  weak-input rejection; password is hashed not stored; cross-user
  source/job isolation incl. 404-no-leak; cross-user package/claims
  isolation incl. can't-build-from-others'-job; per-user privacy export
  and scoped wipe; default-user back-compat with no token; auth
  enforced on mutations when enabled (401 without / 201 with / 401 bad
  token); Phase-4 health; migration reversibility.

## Changed in `app.py` (surgical, additive only)

- Six models gain a defaulted `owner_id`; `extract_claims` propagates
  the source's owner to its claims. List/get/create paths for the owned
  resources go through `_scoped`/`_get_owned` and stamp `owner_id` — in
  AUTH-off mode the current user owns everything, so results are
  identical to before.
- `/api/health` adds a non-pinned `latest_phase: 4` and an `auth`
  block. `phase` stays `2` and `phases_shipped` stays `[1,2,3]` **on
  purpose** — both are pinned by earlier tests, and "behavior-preserving,
  tests only grow" outranks cosmetics. `latest_phase` is the live
  indicator going forward.

## Explicitly NOT changed (non-negotiables honored)

- Default behavior is single-user and auth-free — zero friction, no
  forced accounts, existing data fully visible to the `local` user.
- No auto-submit / external submission / network egress; no crawling,
  LinkedIn/auth-walled scraping, or CAPTCHA bypass; no fabricated
  content. Provenance gate, claim controls, bullet
  accept/reject/rewrite/lock, the council, the Axiom block, the export
  trust gate, the Phase 2 deterministic score + secondary semantic
  signal, and the Phase 3 immutable tracker snapshot are all unchanged.
- Credentials are never logged or exported; the `user` table is not in
  the privacy bundle (no cross-account credential leak).

## Validation

`pytest -q` → **109 passed**. End-to-end with a real résumé: two users
register; Sam uploads his résumé (62 owner-stamped claims); Bob sees 0
sources / 0 claims and gets **404** on every one of Sam's resources
incl. building a package from Sam's job id; privacy export and wipe are
per-user (Bob's wipe leaves Sam's data intact); with no token the app
is the single `local` user exactly as in Phase 3; health reports
`latest_phase 4`, `auth.enabled false` by default.

---

# CHANGES — Phase 3: Application tracker (close the loop, human-in-loop only)

Phase 3 is **additive and behavior-preserving**. Every Phase 1 + 2 +
Delivery 1–4 invariant is untouched: the suite stays green and grew
**83 → 99** (16 new tests, 0 removed, 0 modified). Single-file `app.py`
shape preserved; no new modules. The defining non-negotiable of this
phase: **Aptiro never submits an application anywhere** — there is no
network egress on any tracker code path, and a test asserts it.

## Added

- **`Application` model + `ApplicationStatus` enum.** The post-submission
  tracker, distinct from the existing pre-submission `ApplySession`
  guard. Lifecycle: `drafted → exported → submitted_by_user →
  interviewing → offer → rejected → withdrawn`.
- **Audited, human-only state machine** (`_APP_TRANSITIONS`). Every
  transition is an explicit `POST /transition` call, recorded in an
  append-only `history` audit log with from/to/note/timestamp. An
  illegal transition returns **HTTP 409** and changes nothing;
  `rejected` and `withdrawn` are terminal.
- **Immutable submit snapshot.** The moment the user marks an
  application `submitted_by_user`, the exact provenance-filtered export
  model (same one `/export` produces) plus the cover letter is frozen
  into `snapshot`, with a SHA-256 (`snapshot_sha`) for tamper-evidence.
  It is written exactly once and never rewritten — advancing through
  later states leaves it byte-identical (asserted).
- **Deterministic offline reminders.** On submit, a fixed 3 / 7 / 14-day
  follow-up cadence is generated by a pure function (`_make_reminders`)
  — same `submitted_at` ⇒ identical reminders. No scheduler, nothing is
  ever sent; a row can be marked done by an explicit user action.
- **ATS-safe export profile.** A single-column, ASCII-only, no-table,
  no-markup plain-text resume — the layout ATS parsers read most
  reliably. Reached via `GET …/export?format=ats`. Deliberately **not**
  added to `exporting.FORMATS`, so the pinned format/health contract and
  the Phase 1/2 tests are unchanged; it runs through the same
  provenance/exclusion gate as every other export.
- **Tracker in the privacy bundle.** `Application` is registered in
  `_all_models()`, so it is automatically included in
  `GET /api/privacy/export` (JSON) and the data-wipe. Plus a dedicated
  `GET /api/applications/export.csv` for the CSV view.
- **Endpoints** under `/api/applications`: create-from-package, list,
  get, `/transition`, `/snapshot`, `/reminders/{id}/done`,
  `/export.csv`, delete. Every response repeats the no-auto-submit
  guarantees.
- **Frontend.** A new **Tracker** tab (status pills, legal-transition
  buttons, audit history, reminders, immutable-snapshot panel) and a
  "Track this application" action plus an **ATS-safe** option in the
  package export selector.
- **Alembic `0003_phase3_application_tracker`.** Creates the new
  `application` table for existing Postgres deployments (a fresh deploy
  gets it from live metadata via `0001`; existing SQLite gets it from
  `create_all`, which creates missing tables).
- **Tests (`test_app.py`, +16):** create-from-package; the full legal
  path; illegal transition → 409 and state-unchanged; terminal-state
  rejection; snapshot frozen on submit and **immutable** across later
  transitions; snapshot 404 before submit; reminder determinism;
  reminders attached and completable; tracker in the privacy JSON
  bundle and CSV; an explicit source-level assertion that **no network
  egress symbol** exists in the tracker; a behavioural test that submit
  performs zero HTTP even with the network layer rigged to explode;
  ATS export is plain single-column ASCII with no HTML/tables; the ATS
  profile does not change the format contract; Phase-3 health.

## Changed in `app.py` (surgical, additive only)

- `_all_models()` gains `Application` (first, so it exports/wipes
  cleanly). The export endpoint special-cases `format=ats` *before* the
  `FORMATS` check, so existing formats and the pinned health/test
  contract are untouched.
- `/api/health` gains `phases_shipped: [1,2,3]`, `export_profiles:
  ["ats"]`, and an `application_tracker` block. `phase` stays `2` and
  `export_formats` stays the four-format list **on purpose** — both are
  pinned by Phase 2 tests, and "behavior-preserving, tests only grow"
  outranks cosmetics. `phases_shipped` is the live truth.

## Explicitly NOT changed (non-negotiables honored)

- **No auto-submit, no external submission, no network egress** on any
  tracker path. `submitted_by_user` is a state the user asserts after
  applying on the employer's own site; a test proves the code can't
  reach the network even if it tried.
- No crawling / LinkedIn / auth-walled scraping; no CAPTCHA bypass; no
  fabricated content. The provenance gate, claim controls, bullet
  accept/reject/rewrite/lock, the 13-step / 5-agent council, the Axiom
  fabricated-metric block, the export trust gate, and the Phase 2
  deterministic score + secondary semantic signal are all unchanged.

## Validation

`pytest -q` → **99 passed**. End-to-end with a real résumé: create
tracker from a package (`drafted`); `drafted → offer` correctly **409**;
`exported → submitted_by_user` freezes the SHA-256 snapshot, sets
`submitted_at`, attaches 3 reminders; advancing to `interviewing` leaves
the snapshot byte-identical; ATS export is 868 bytes of pure ASCII with
no `<` or `|`; the privacy JSON bundle and CSV both include the tracker;
health reports `phases_shipped [1,2,3]`, `auto_submit false`, and the
four-format `export_formats` unchanged.

---

# CHANGES — Phase 2: Real job discovery & explainable matching

Phase 2 is **additive and behavior-preserving**. Every Phase 1 +
Delivery 1–4 invariant is untouched: the test suite stays green and
grew **63 → 83** (20 new tests, 0 removed, 0 changed). Single-file
`app.py` shape preserved; the one new module is a top-level sibling
(`embeddings.py`) following the proven `ai_provider.py` pattern. All
non-negotiables hold — no crawling, no LinkedIn/auth-walled scraping, no
CAPTCHA bypass, no fabricated claims, and the deterministic 0–100 score
remains the single source of truth for ranking.

## Added

- **`backend/embeddings.py`** (new sibling module). Pluggable embedding
  provider, deterministic **mock by default** (hashed bag-of-tokens →
  fixed unit vector; identical input → identical vector). A real model
  (`openai`) is used only behind `APTIRO_EMBEDDING_PROVIDER=openai` +
  key + SDK, with automatic fallback to mock. Same contract as the AI
  provider: the app and tests need no key and no network.
- **Structured requirement extraction** (`_structured_requirements`).
  Splits a JD into `must_have` / `nice_to_have` (section headers +
  inline “a plus / preferred” cues), plus `min_years`,
  `seniority_rank`, `skills`, `domains`. Reuses the existing
  deterministic parser primitives (`_REQ_LINE`, `_extract_skills`,
  `_seniority_rank`, `_domains_in`) so behaviour is consistent with the
  rest of the pipeline. The flat `requirements` list is still produced
  and stored unchanged.
- **User-supplied URL import** (`fetch_url_text` +
  `POST /api/jobs/import-url`). Server-side fetch of the **one** URL the
  user pastes — not a crawler. Guardrails: http/https only, denylist of
  login/auth-walled hosts (LinkedIn, Indeed, Glassdoor, …), robots.txt
  honoured (fail-closed on an explicit `Disallow`), timeout + response
  size cap (both env-tunable), HTML/text content-type only, scripts and
  styles stripped, reduced to text, fed through the same `import_job`
  normaliser. Failures map to a clear HTTP 422.
- **Dedupe + archival**. New jobs are de-duplicated on normalised
  (company, title, source_url): a duplicate paste/URL returns the
  existing job with HTTP 200 and `deduplicated: true` instead of a copy.
  `POST /api/jobs/{id}/archive` and `/unarchive` toggle visibility;
  archived jobs already drop out of `/api/jobs` and `/api/matches`.
- **Explainable evidence**. `_candidate_profile` now records which
  approved claim earned which signal (skills, domains, AI terms,
  leadership, seniority, overall evidence). Every `ScoreComponent` in a
  match now carries an `evidence` list of `{claim_id, snippet}` so each
  point traces back to the exact approved claim that earned it.
- **Secondary semantic signal**. Each match carries a `semantic` block
  (provider, cosine similarity, `affects_score: false`, an `agreement`
  hint and an explicit note). It is computed from the embedding
  provider (mock by default → deterministic) and is **never** mixed into
  the 0–100 score or the ranking — tests assert ranking is by the
  deterministic score alone.
- **Safe additive schema self-heal** (`_ensure_additive_columns`) +
  Alembic `0002_phase2_job_structured_requirements`. `create_all()`
  never ALTERs an existing table, so a pre-Phase-2 DB would be missing
  the new column. The self-heal applies only additive `ADD COLUMN`s for
  columns already on the model (no drops, no type changes, no data
  loss); Postgres production still migrates via Alembic. This fixes the
  upgrade path for any DB created before Phase 2.
- **Tests (`test_app.py`, +20)**: requirement must/nice split &
  attribution; URL fetch over a mocked transport (success, bad scheme,
  LinkedIn denied, robots `Disallow`, non-HTML, oversized, timeout) and
  the endpoint’s 422 mapping; dedupe and archive/unarchive; score
  components sum to `earned_points` and cite real claim evidence;
  semantic signal deterministic offline and never reorders ranking; the
  additive self-heal preserves existing rows; Phase-2 health.

## Changed in `app.py` (surgical, additive only)

- `JobPosting` gains `structured_requirements: dict` (JSON, default
  `{}`). The flat `requirements` list is unchanged.
- `import_job(...)` gains a `source` argument (default
  `"manual_import"`; URL import passes `"url_import"`) and now also
  populates `structured_requirements`.
- `JobRead` gains `structured_requirements`, `is_archived`,
  `deduplicated`; `_job_read` fills them. `ScoreComponent` gains
  `evidence`; `JobMatchOut` gains `structured_requirements` and
  `semantic`. All new fields are optional/defaulted, so existing clients
  and the existing tests are unaffected.
- `score_job(...)`’s return dict gains `structured_requirements` and
  `semantic`; every existing key is unchanged, so the council, package
  builder and apply paths that consume `score_job` see no behavioural
  difference.
- `init_db()` now also runs the additive self-heal. `/api/health` adds
  `phase`, the embedding provider, `job_import`, and `semantic_signal`
  **without** changing `slice` (the field the health test pins).

## Explicitly NOT changed (non-negotiables honored)

- No crawling — exactly one user-supplied URL is fetched. No LinkedIn /
  auth-walled scraping (host denylist). robots.txt respected. No CAPTCHA
  or anti-bot circumvention.
- The deterministic weighted 0–100 score is still the single source of
  truth; the semantic signal is secondary and never affects score or
  ranking.
- No fabricated content; the embedding model is never on the trust path
  and never produces grounded claims.
- Provenance colours, the claim approval gate, bullet
  accept/reject/rewrite/lock, the 13-step / 5-agent council, the Axiom
  fabricated-metric block, and the export trust gate are all unchanged.

## Validation

`pytest -q` → **83 passed**. End-to-end with a real résumé
(Pandoc-exported Markdown): paste import → structured must/nice +
min_years 6 + seniority rank; identical re-import → HTTP 200,
`deduplicated`, still one job; URL import → `source=url_import` with the
source URL preserved; archive hides it from list/matches, unarchive
restores; the match breakdown’s component `earned` values sum exactly to
`earned_points`, 5 components cite 21 real claim snippets; the semantic
signal is the deterministic mock, `affects_score=false`, and ranking is
unchanged by it.

---

# CHANGES — Trust + Export slice (the diff, explained)

This slice is **additive and behavior-preserving**. The Delivery 1–4
contract (extraction & provenance, explainable scoring, package builder
+ per-bullet controls, 13-step / 5-agent council, apply scaffolding,
privacy) is unchanged; the test suite stays green and grew from 54 → 63.

> Honest framing: in this environment the prototype's source is not an
> editable file, so this repo is a faithful, behavior-preserving rebuild
> of the prototype **plus** the slice. The D1–D4 invariants are covered
> by `test_app.py` (same guarantees), and the new ingestion/export/
> exclusion behavior is covered on top.

## Added (new files — the "limited modular split")

- **`backend/ingestion.py`** — production PDF/DOCX/TXT/Markdown
  extraction. Returns normalized, structure-preserving text + a
  `parse_meta` blob (`format`, `pages`, `page_map`). Exotic bullet
  glyphs are normalized; Pandoc/DOCX-export artifacts (`[x]{.underline}`,
  nested `[[x]{.underline}](url)`, `{.attr}`, `\$ \( \)` escapes, hard
  breaks, pure grid-table rule lines) are stripped so a real résumé
  flows into the Vault cleanly. **It only changes how raw text is
  produced** — the same `parse_document` / `extract_claims` pipeline
  derives claims, so snippets, sections, provenance, confidence and the
  approval gate are untouched.
- **`backend/exporting.py`** — renders the export model to Markdown →
  HTML → DOCX → PDF. Markdown/HTML are stdlib-only (always work); DOCX
  (`python-docx`) and PDF (`reportlab`) raise a graceful
  `ExportUnavailable` → HTTP 501 if the optional lib is absent, so the
  app never hard-crashes.
- **`backend/ai_provider.py`** — pluggable provider. **Mock is the
  default** (deterministic, offline). Anthropic only behind
  `APTIRO_AI_PROVIDER=anthropic` (+ key + SDK), with automatic fallback
  to mock. AI is deliberately *not* on the trust path.
- **`backend/test_app.py`** — D1–D4 invariants preserved + new tests:
  ingestion (txt/md/pandoc-artifacts/docx-roundtrip/pdf-page-map/
  unsupported/empty/size-limit/415), export (md/html/docx-valid-zip/
  pdf-signature/400/404/preview/cover-letter-only/direct), and the
  non-negotiable exclusion gate (rejected excluded, unsupported
  excluded, explicit-override re-includes).
- **Infra**: `requirements.txt`, `alembic.ini` + `alembic/` (env binds
  SQLModel metadata, resolves `APTIRO_DATABASE_URL`; `0001_initial`
  enables pgvector + builds the schema from live metadata),
  `docker-compose.yml` (pgvector/pg16 + backend + static UI),
  `Dockerfile.backend` (migrate then serve), `.env.example`, `RUN.sh`,
  `README.md`, `frontend/index.html`.

## Changed in `backend/app.py` (minimal, surgical)

- `Source.parse_meta` (JSON) and `SourceRef.page` added so ingestion can
  attach format/page metadata. Extraction logic and the claim model are
  otherwise unchanged; identical-bullet de-dupe within a source added.
- `POST /api/sources/upload` now does **real** ingestion via
  `ingestion.extract(...)` (multipart; 413 over size limit, 415
  unsupported, 422 unreadable). The pasted-text path is unchanged.
- `parse_document(text, page_map=None)` accepts an optional page map
  (used only for PDF/DOCX). With no map it behaves exactly as before.
- New export endpoints: `GET /api/packages/{id}/export` and
  `…/export/preview`, plus `_export_model()` which applies the
  provenance/exclusion gate before any renderer runs.
- **Bug fixed**: `app.include_router(packages_router)` was called
  *before* the export routes were defined, so FastAPI silently dropped
  `/export` and `/export/preview` (they 404'd). The include was moved to
  *after* all package routes are defined; both routes now register.

## Explicitly NOT changed (non-negotiables honored)

- No auto-apply / external submission; apply stays guarded scaffolding.
- No LinkedIn scraping; no CAPTCHA / anti-bot circumvention.
- No fabricated claims; AI never produces grounded content.
- Provenance colours, claim controls, and package bullet
  accept/reject/rewrite/lock behavior preserved.
- The 13-step orchestrator and 5-agent council behavior preserved
  (Axiom still blocks fabricated metrics).

## Validation

`pytest -q` → **63 passed**. Validated end-to-end with a real résumé
(Pandoc-exported Markdown, 9.5 KB): upload → 62 claims with provenance /
snippets / confidence (all `blue` grounded-résumé, all pending, every
claim carries a source ref) → approve 8, reject 1 → strategy + job →
package (draft, fit 84/100, 13 bullets across summary / experience /
skills / cover_letter) → 5-agent council ready → all four exports return
HTTP 200 (MD 1.1 KB, HTML 1.9 KB, valid DOCX zip 37 KB, `%PDF-` 2.6 KB)
→ the rejected claim's text is absent from the default export and only
re-appears under the explicit `include_unsupported=true` override. The
bullet-level unsupported-metric gate is additionally covered by
dedicated tests in `test_app.py`.
