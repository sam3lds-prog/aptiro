# Aptiro — provenance-first job-application assistant (Phases 1–7 complete)

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
application tracker)** + **Phase 4 (multi-user, auth & data isolation)**
+ **Phase 5 (grounded AI assist)** + **Phase 6 (production ops &
observability)** + **Phase 7 (real frontend foundation)** — every phase
additive and behavior-preserving, 136 backend tests green.

**Phase 7 (frontend foundation)** is the first phase of the publishable
product upgrade. It replaces the single-file CDN React app with a real
**Vite + React 18 + TypeScript + Tailwind** application:

- React Router v6, TanStack Query, Zustand state.
- Typed API client modelled on the FastAPI schemas.
- Reusable UI primitives: Button / Card / Input / Modal / Badge /
  Toaster / ConfirmModal / EmptyState / Skeleton / ErrorBoundary.
- Distinctive editorial type pairing — Fraunces (display) + IBM Plex
  Sans (body) — and a refined dark palette built on CSS variables.
- Polished empty / loading / error / confirmation states across every
  page; responsive layout shell with seven product areas + Settings.
- Multi-stage Docker build (Node → nginx) reverse-proxying `/api/*` to
  the backend service so the UI talks to it through a single origin.

Backend behavior, contracts, and all 136 tests are untouched.

---

## Quick start (zero config — SQLite, mock AI)

Requires **Python 3.11+** and **Node.js 20+**.

```bash
# First run installs the venv + node_modules automatically (~30s).
./RUN.sh
# UI  → http://localhost:5173    (Vite dev server with hot reload)
# API → http://localhost:8000    (docs at /docs)
```

Or run them manually:

```bash
# Backend
cd backend
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt
uvicorn app:app --reload          # API → http://localhost:8000  (/docs)

# Frontend (in another terminal)
cd frontend
npm install
npm run dev                       # UI → http://localhost:5173
```

## Production path (Docker — Postgres + pgvector + built frontend)

```bash
docker compose up --build
# UI  → http://localhost:5173     (nginx-served Vite build)
# API → http://localhost:8000     (Postgres + pgvector; alembic migrates on boot)
```

## Run the tests

```bash
cd backend && . .venv/bin/activate
pytest -q          # 136 tests, all green (offline, no key)
```

## Phase 0 — Validate a clean checkout

Before starting any feature work (or after each phase lands), run the
validation gate:

```bash
./validate.sh                # structure + tests + frontend build + docker build
./validate.sh --no-docker    # skip docker (fastest check)
./validate.sh --no-frontend  # skip npm install + build
```

Each check prints PASS / FAIL with the exact problem. Exits non-zero on
any failure. Do not advance to the next phase until this is clean.

---

## The 8-step deliverable, end to end

1. **Start** — `./RUN.sh` (or `docker compose up --build`).
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
8. **Test** — `pytest -q` (backend) and `npm run typecheck && npm run
   build` (frontend).

---

## Architecture

```
backend/
  app.py             FastAPI + SQLModel. THE module (tests do `import app`).
  ingestion.py       PDF/DOCX/TXT/Markdown → normalized text + parse_meta.
  exporting.py       Export model → Markdown/HTML/DOCX/PDF.
  ai_provider.py     Pluggable AI; deterministic mock is the default.
  embeddings.py      Pluggable embedding provider (secondary signal only).
  test_app.py        Full test suite (136 tests; offline; mock providers).
  alembic/           Migration scaffolding for the Postgres path.

frontend/            NEW in Phase 7 — Vite + React 18 + TypeScript + Tailwind.
  index.html         Vite entry HTML (loads /src/main.tsx).
  package.json
  vite.config.ts     /api proxied to :8000 in dev.
  tailwind.config.js Design tokens + provenance palette + type families.
  nginx.conf         Used in prod (multi-stage Docker build → nginx serve).
  src/
    main.tsx         QueryClient + Router + ErrorBoundary + Toaster.
    App.tsx          Health/auth bootstrap; routes to AppLayout pages.
    index.css        Palette tokens, Fraunces + IBM Plex Sans, subtle grain.
    lib/
      api.ts         Typed fetch with bearer token; ApiError class.
      types.ts       TypeScript types modelled on FastAPI schemas.
      cn.ts          Tiny classNames helper.
    stores/
      auth.ts        Zustand: token + me, persisted to localStorage.
      toast.ts       Zustand: toast queue + useNotify() helper.
    components/
      ui/            Button, Card, Input/Textarea/Select/Label, Badge,
                     Modal, Skeleton/Spinner/EmptyState, Toaster.
      ErrorBoundary, ConfirmModal, ProvenanceBadge, PageHeader.
    layouts/
      AppLayout.tsx  Sidebar + main shell.
      Nav.tsx        Brand + navigation + logout (when authed).
    pages/
      Dashboard, Vault, Strategy, Jobs, Matches, Packages, Tracker,
      Apply, Activity, Privacy, Auth, NotFound.

Dockerfile.backend / Dockerfile.frontend / docker-compose.yml
.env.example / RUN.sh / validate.sh / .github/workflows/ci.yml
```

- **Database**: SQLite by default (zero config). Production target is
  Postgres + pgvector via `APTIRO_DATABASE_URL` (docker-compose provides
  it; alembic creates the schema + extension). Tests always use in-memory
  SQLite.
- **AI provider**: deterministic **mock by default**. The app and tests
  need **no key and no network**. Anthropic is used only when
  `APTIRO_AI_PROVIDER=anthropic` + `ANTHROPIC_API_KEY` + SDK are all
  present; otherwise it transparently falls back to the mock.
- **Frontend → backend**: in dev, Vite proxies `/api/*` to the FastAPI on
  `:8000`. In prod, nginx (inside the frontend image) reverse-proxies
  `/api/*` to the `backend` service over the compose network.

## Provenance colours (unchanged)

blue = grounded résumé truth · purple = profile-derived · green = public
context · orange = AI-suggested connective · **red = unsupported — never
exported**.

## The export trust gate (non-negotiable, unchanged)

`GET /api/packages/{id}/export?format=md|html|docx|pdf&artifact=resume|cover_letter|both`

Before any renderer runs, the export model drops every bullet that is
**rejected**, **do-not-use**, **red / live-unsupported**, or carries an
**unsupported metric**. `include_unsupported=true` is an explicit,
clearly-warned user override. `…/export/preview` shows precisely what is
included vs excluded and why — and the new frontend renders that preview
side-by-side with the included content.

---

## Operations (Phase 6)

Observability: every request emits one structured JSON log line and an
`X-Request-ID` header (a client-supplied id is honoured). Successful
mutations are recorded in an append-only, owner-scoped audit trail at
`GET /api/audit` (and the **Activity** tab). Probes: `/healthz`
(liveness, no dependencies) and `/readyz` (returns 503 until the
database is reachable) — wire these into your orchestrator.
