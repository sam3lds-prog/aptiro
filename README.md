# Aptiro — Trust + Discovery + Tracker + Multi-user (Phase 4)

Aptiro turns your own résumé/profile into **provenance-tracked claims**,
discovers real jobs (paste a description or import a public posting URL
you supply), scores each one with an **explainable, evidence-cited
breakdown**, builds an application package where **every bullet traces
back to an approved source**, exports it — with rejected / unsupported
content **excluded by default** — and tracks the application's lifecycle
**without ever submitting anything for you**.

Nothing is fabricated. Nothing is auto-submitted. No crawling, no
LinkedIn/auth-walled scraping, no CAPTCHA circumvention.

This build is **Phase 1 (Trust + Export)** + **Phase 2 (real job
discovery & explainable matching)** + **Phase 3 (the human-in-loop
application tracker)**: an audited state machine, an immutable SHA-256
"what I sent" snapshot frozen the moment *you* mark an application
submitted, deterministic follow-up reminders, an ATS-safe plain-text
export profile, and the tracker in the privacy export — with an explicit
test proving no code path can submit anything anywhere. Built on the
Delivery 1–4 contract without rewriting the app.

**Phase 4 (multi-user, auth & data isolation)** adds optional accounts:
register/login with stdlib-hashed passwords and bearer tokens, complete
per-user data isolation (cross-user access returns 404 with no
existence leak), per-user privacy export/wipe, and an Alembic migration
that backfills existing data to a built-in `local` user. Auth is
**off by default** — with no token the app is the same single-user tool
as Phase 3, so nothing existing breaks and no account is forced.

---

## Quick start (zero config — SQLite, mock AI)

```bash
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload          # API → http://localhost:8000  (/docs)
```

In another terminal, serve the single-file UI:

```bash
cd frontend && python3 -m http.server 5173    # UI → http://localhost:5173
```

Or both together with the helper (port preflight + Ctrl-C cleanup):

```bash
./RUN.sh
```

## Production path (Docker — Postgres + pgvector)

```bash
docker compose up --build
# UI  → http://localhost:5173
# API → http://localhost:8000   (Postgres + pgvector; alembic migrates on boot)
```

## Run the tests

```bash
cd backend && . .venv/bin/activate
pytest -q          # 109 tests, all green (P1 63 + P2 20 + P3 16 + P4 10; 0 removed)
```

Tests are deterministic and offline: in-memory SQLite, mock AI, no network.

---

## The 8-step deliverable, end to end

1. **Start** — `./RUN.sh` (or docker compose).
2. **Upload or paste** — Profile Vault → upload a PDF/DOCX/TXT/Markdown
   résumé, or paste text.
3. **Extract with evidence** — claims appear with source snippet, section,
   page (PDF), confidence, and a provenance colour.
4. **Approve** — approve / reject / edit / do-not-use per claim.
5. **Build a package** — Packages → pick a job → Build.
6. **Export** — Export panel → choose Markdown / HTML / DOCX / PDF and
   résumé / cover letter / both → Download.
7. **Verify the gate** — “Preview gate” lists exactly what was excluded
   and why; rejected/unsupported content never appears unless you tick
   the explicit override.
8. **Test** — `pytest -q`.

---

## Architecture

```
backend/
  app.py           FastAPI + SQLModel. THE module (tests do `import app`).
                   config → models → db → parsing → extraction/provenance
                   → job import → scoring → packages → council → apply →
                   notifications → privacy → export → seed → app.
  ingestion.py     NEW. PDF/DOCX/TXT/Markdown → normalized, structure-
                   preserving text + parse_meta (format/pages/page_map).
  exporting.py     NEW. Export model → Markdown/HTML/DOCX/PDF.
  ai_provider.py   NEW. Pluggable AI; deterministic mock is the default.
  test_app.py      D1–D4 invariants + ingestion/export/exclusion tests.
  alembic/         Migration scaffolding for the Postgres path.
frontend/index.html  Single-file React 18 (CDN). Vault upload + Package
                     Workspace export buttons are the new slice UI.
docker-compose.yml / Dockerfile.backend / .env.example / RUN.sh
```

- **Database**: SQLite by default (zero config). Production target is
  Postgres + pgvector via `APTIRO_DATABASE_URL` (docker-compose provides
  it; alembic creates the schema + extension). Tests always use in-memory
  SQLite.
- **AI provider** (Decision 2): deterministic **mock by default**. The
  app and tests need **no key and no network**. Anthropic is used only
  when `APTIRO_AI_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` + SDK are all
  present; otherwise it transparently falls back to the mock. AI is *not*
  on the trust path — extraction and export stay deterministic and
  grounded.
- **Limited modular split**: only `ingestion.py`, `exporting.py`,
  `ai_provider.py` were added. The parser, extraction, provenance rules,
  confidence, scoring, package builder, per-bullet controls, and the
  13-step / 5-agent council are unchanged in contract.

## Provenance colours (unchanged)

blue = grounded résumé truth · purple = profile-derived · green = public
context · orange = AI-suggested connective · **red = unsupported — never
exported**.

## The export trust gate (non-negotiable)

`GET /api/packages/{id}/export?format=md|html|docx|pdf&artifact=resume|cover_letter|both`

Before any renderer runs, the export model drops every bullet that is
**rejected**, **do-not-use**, **red / live-unsupported**, or carries an
**unsupported metric**. `include_unsupported=true` is an explicit,
clearly-warned user override. `…/export/preview` shows precisely what is
included vs excluded and why.

## Known limitation (documented, not a blocker)

Résumés exported through Pandoc as **grid tables** (e.g. an EDUCATION
block rendered as `+----+----+`) fragment into partial cell rows. Markup
artifacts (`[x]{.underline}`, `\$`, table rules) are stripped and the
**summary/experience** content that actually feeds packages extracts
cleanly; deep grid-table reconstruction is a parser-depth item for a
later phase, not Trust + Export.
