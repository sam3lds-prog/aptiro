# AGENTS.md

Guidance for AI agents working in this repository.

## Cursor Cloud specific instructions

### Product and services

**Aptiro** is a provenance-first job-application assistant: `backend/` (FastAPI + SQLModel) and `frontend/` (Vite + React + TypeScript). See [README.md](README.md) for the full 8-step product flow.

| Service | Required for dev UI/API? | Notes |
|--------|---------------------------|--------|
| Backend (uvicorn `:8000`) | Yes | SQLite by default; no Postgres needed for `./RUN.sh` |
| Frontend (Vite `:5173`) | Yes | Proxies `/api` → backend in dev |
| PostgreSQL (`db` in compose) | No for `./RUN.sh` | Only when using `docker compose up` |

External APIs (Anthropic, OpenAI, SMTP, Twilio) are optional; defaults use deterministic mocks (no keys, no network for tests).

### One-time VM prerequisites (Debian/Ubuntu)

If `python3 -m venv` fails with “ensurepip is not available”, install the venv package once (not in the update script):

```bash
sudo apt-get install -y python3.12-venv
```

Requires **Python 3.11+** and **Node.js 20+** (`npm`).

### Starting the stack

Prefer the repo helper (starts backend + Vite; installs deps on first run):

```bash
./RUN.sh
```

- API: http://localhost:8000 (OpenAPI at `/docs`)
- UI: http://localhost:5173

Use **tmux** for long-running dev servers in Cloud Agent VMs (see system tmux config at `/exec-daemon/tmux.portal.conf`). Example session name: `aptiro-run`.

Manual split terminals: [README.md](README.md) “Or run them manually”.

### Lint / test / build

There is no dedicated ESLint or Ruff step in CI. Use:

| Check | Command |
|-------|---------|
| Backend tests | `cd backend && . .venv/bin/activate && pytest -q` (offline; no services) |
| Frontend types | `cd frontend && npm run typecheck` |
| Frontend production build | `cd frontend && npm run build` (runs `tsc` then `vite build`) |
| Full repo gate | `./validate.sh` or `./validate.sh --no-docker` |

**Known drift:** As of this writing, `npm run typecheck` / `npm run build` can fail due to mismatches between `frontend/src/lib/types.ts` and page components, while `npm run dev` still serves the UI. Backend `pytest` is the reliable CI gate (218+ tests). Do not “fix” types during environment-only tasks unless explicitly asked.

### Hello-world smoke (core API)

With `./RUN.sh` running and default `APTIRO_AUTH=off`:

```bash
curl -s http://localhost:8000/api/health | python3 -m json.tool
curl -s -X POST http://localhost:8000/api/sources \
  -H 'Content-Type: application/json' \
  -d '{"source_type":"resume","label":"demo","raw_text":"Senior PM. Built AI chatbot with $3.4M impact."}'
curl -s http://localhost:8000/api/claims | python3 -m json.tool
```

### Ports and env

- Override ports: `APTIRO_API_PORT`, `APTIRO_UI_PORT` before `./RUN.sh`.
- Copy [.env.example](.env.example) to `.env` only when changing providers or Postgres; zero-config dev does not require it.

### Docker path (optional)

`docker compose up --build` — Postgres + pgvector + nginx-built UI. Slower; use when testing the production compose path, not for everyday agent edits.
