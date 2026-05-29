# Aptiro — Upgrade Phase 11: Real multi-board job providers

**Slice:** real, ToS-friendly job data from Greenhouse, Lever, Ashby, and
Adzuna — slotted behind the existing job-source seam, mock-default and
fully reversible. One vertical addition; no behavior change without
configuration.

## Added
- `backend/app/modules/jobs/real_providers.py` — live fetch for four
  providers, each normalizing into the existing `JobPosting` shape:
  - **Greenhouse** — `boards-api.greenhouse.io/v1/boards/{token}/jobs`
  - **Lever** — `api.lever.co/v0/postings/{token}`
  - **Ashby** — `api.ashbyhq.com/posting-api/job-board/{token}`
  - **Adzuna** — `api.adzuna.com/v1/api/jobs/{country}/search/1` (keyed)
  Public funcs: `fetch_real(provider, query, limit)`, `is_configured(provider)`,
  `configured_providers()`. All app imports are lazy (inside functions) so the
  module is safe to import during `legacy.py` load — no circular import.
- A wrapper appended to the file that defines `fetch_jobs_from_source`. It
  tries the live providers first and falls back to the original (mock +
  Remotive) path. The `/api/job-sources/fetch` endpoint and the saved-search
  runner pick up live data automatically because they resolve the name at
  call time.
- `backend/test_phase11_real_providers.py` — 10 offline tests (httpx stubbed,
  no keys): per-provider parsing/normalization, HTML stripping, work-mode
  inference, salary/date mapping, per-company query filter, limit, graceful
  fallback on error/unconfigured, and the `is_configured`/`configured_providers`
  contract.

## Provider model (important)
- **Greenhouse / Lever / Ashby are per-company board APIs.** They return one
  company's postings, keyed by that company's board token/slug. Configure the
  tokens via env (comma-separated); `query` is applied as a text filter over
  the returned postings since these APIs take no search term.
- **Adzuna is a search API.** It takes the query directly and needs a key.

## Configuration (all optional → absent means "stay on mock")
```
APTIRO_GREENHOUSE_BOARDS   e.g. stripe,airbnb,figma
APTIRO_LEVER_BOARDS        e.g. netflix,plaid
APTIRO_ASHBY_BOARDS        e.g. ramp,linear
APTIRO_ADZUNA_APP_ID
APTIRO_ADZUNA_APP_KEY
APTIRO_ADZUNA_COUNTRY      default us
APTIRO_JOB_FETCH_TIMEOUT   seconds, default 15
```

## Explicitly NOT changed
- **Mock stays the default.** Every provider returns `[]` on any error or when
  unconfigured, so the offline suite and existing behavior are untouched. With
  no new env vars set, `fetch_jobs_from_source` behaves exactly as before.
- **Remotive** keeps its existing tested path — it is intentionally not handled
  by the new module.
- **No scraping, no auth-walled sources, no LinkedIn.** These are the vendors'
  own public posting APIs.
- The **deterministic weighted score** remains the single source of truth;
  this slice only produces normalized rows for it to score.
- No migrations, no schema changes (rows use existing `JobPosting` fields).

## Validation
- New module: 10/10 tests pass; clean `py_compile`; no warnings.
- Wrapper-rebind verified end-to-end: a stand-in endpoint caller returns LIVE
  data when a provider is configured and falls back to MOCK otherwise;
  Remotive path unaffected.
- Patcher verified against a fake tree: dry-run, apply, idempotent re-run, and
  `--revert` (strips the block, deletes the module, leaves the file importable).

## Deployment
1. Download flat to `~/Downloads`: `patch_phase11_real_providers.py`,
   `test_phase11_real_providers.py`, `CHANGES_PHASE11.md` (and
   `real_providers.py` for review — the patcher embeds it, so it is optional).
2. `bash deploy_phase11.sh` — copies the test + CHANGES, runs the patcher
   (dry-run then apply), runs `pytest -q`, and prints verification.
3. Set provider env vars to go live. Undo anytime with
   `python3 ~/Downloads/patch_phase11_real_providers.py --dst <root> --revert`.

## Stop-and-report
This is the additive half of "real inputs." The remaining substrate item —
**Phase 10, finishing the `legacy.py` modularization (PR-13 research, then
14/15/16)** — is a verbatim refactor and needs the live `legacy.py` to keep the
tests-green guarantee. Paste that file and PR-13 ships next.
