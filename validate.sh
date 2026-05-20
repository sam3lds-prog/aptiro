#!/usr/bin/env bash
# Aptiro — Phase 0 validation gate.
#
# Run from the repo root on a clean checkout BEFORE starting feature work.
# Exits non-zero on any failure. Each check prints a clear PASS / FAIL
# line so you can spot exactly what drifted.
#
# Usage:
#   ./validate.sh                # everything (recommended)
#   ./validate.sh --no-docker    # skip docker compose build (slowest step)
#   ./validate.sh --no-frontend  # skip npm install + build
#
# Phase 0 is a TRUTH GATE. Don't start Phase 1 work until this passes.
set -uo pipefail

SKIP_DOCKER=0
SKIP_FRONTEND=0
for arg in "$@"; do
  case "$arg" in
    --no-docker)   SKIP_DOCKER=1 ;;
    --no-frontend) SKIP_FRONTEND=1 ;;
    -h|--help)
      sed -n '1,18p' "$0"; exit 0 ;;
    *) echo "Unknown arg: $arg" >&2; exit 2 ;;
  esac
done

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

FAIL=0
GREEN=$'\033[32m'
RED=$'\033[31m'
DIM=$'\033[2m'
RESET=$'\033[0m'

say()  { printf "%s\n" "$*"; }
head() { printf "\n${DIM}── %s ─────────────────────────────────${RESET}\n" "$*"; }
pass() { printf "  ${GREEN}PASS${RESET}  %s\n" "$*"; }
fail() { printf "  ${RED}FAIL${RESET}  %s\n" "$*"; FAIL=$((FAIL+1)); }

# ---------------------------------------------------------------- structure
head "1. Folder structure matches README"
EXPECTED=(
  "backend/app.py"
  "backend/test_app.py"
  "backend/requirements.txt"
  "backend/alembic"
  "frontend/package.json"
  "frontend/index.html"
  "frontend/src/main.tsx"
  "frontend/src/App.tsx"
  "frontend/vite.config.ts"
  "frontend/tailwind.config.js"
  "frontend/nginx.conf"
  "Dockerfile.backend"
  "Dockerfile.frontend"
  "docker-compose.yml"
  ".env.example"
  "RUN.sh"
  ".github/workflows/ci.yml"
)
for p in "${EXPECTED[@]}"; do
  if [ -e "$p" ]; then pass "$p"; else fail "missing: $p"; fi
done

# Old single-file frontend should not be the active one anymore.
if [ -f "frontend/index.html" ] && head -1 frontend/index.html | grep -qi '<!doctype html>'; then
  if grep -q '/src/main.tsx' frontend/index.html; then
    pass "frontend/index.html is the Vite entry (not the CDN React file)"
  else
    fail "frontend/index.html still looks like the single-file CDN app (Phase 1 not applied)"
  fi
fi

# ---------------------------------------------------------------- tests
head "2. Backend tests (offline, deterministic)"
if [ ! -d backend/.venv ]; then
  say "  ${DIM}creating venv + installing deps (first run only)…${RESET}"
  python3 -m venv backend/.venv
  ./backend/.venv/bin/pip -q install -r backend/requirements.txt || fail "pip install failed"
fi

if (cd backend && rm -f aptiro.db && ./.venv/bin/python -m pytest -q 2>&1 | tee /tmp/aptiro-pytest.log); then
  COUNT=$(grep -oE '[0-9]+ passed' /tmp/aptiro-pytest.log | tail -1 | awk '{print $1}')
  if [ -n "${COUNT:-}" ] && [ "$COUNT" -ge 136 ]; then
    pass "pytest -q reported $COUNT passed (≥136)"
  else
    fail "pytest passed but count is unexpected ($COUNT) — expected ≥136"
  fi
else
  fail "pytest -q did not exit cleanly — see /tmp/aptiro-pytest.log"
fi

# ---------------------------------------------------------------- runtime probes (lightweight)
head "3. RUN.sh boot smoke test"
if [ -x ./RUN.sh ]; then
  pass "./RUN.sh is executable"
else
  fail "./RUN.sh missing or not executable (chmod +x RUN.sh)"
fi

# ---------------------------------------------------------------- frontend
if [ "$SKIP_FRONTEND" -eq 0 ]; then
  head "4. Frontend build"
  if ! command -v npm >/dev/null 2>&1; then
    fail "npm not installed — install Node.js 20+"
  else
    pushd frontend >/dev/null
    if [ ! -d node_modules ]; then
      say "  ${DIM}installing npm deps (first run only)…${RESET}"
      if [ -f package-lock.json ]; then npm ci --no-audit --no-fund >/dev/null 2>&1 || npm install --no-audit --no-fund >/dev/null 2>&1
      else npm install --no-audit --no-fund >/dev/null 2>&1; fi
    fi
    if npm run typecheck >/dev/null 2>&1; then pass "tsc --noEmit clean"
    else fail "tsc --noEmit reported errors (run 'npm run typecheck' to see them)"; fi
    if npm run build >/dev/null 2>&1; then pass "vite build succeeded"
    else fail "vite build failed (run 'npm run build' to see the error)"; fi
    popd >/dev/null
  fi
else
  head "4. Frontend build  (skipped: --no-frontend)"
fi

# ---------------------------------------------------------------- docker
if [ "$SKIP_DOCKER" -eq 0 ]; then
  head "5. Docker compose build"
  if ! command -v docker >/dev/null 2>&1; then
    fail "docker not installed — install Docker Desktop"
  else
    if docker compose build >/dev/null 2>&1; then pass "docker compose build (backend + frontend) succeeded"
    else fail "docker compose build failed (run 'docker compose build' to see the error)"; fi
  fi
else
  head "5. Docker compose build  (skipped: --no-docker)"
fi

# ---------------------------------------------------------------- summary
echo
if [ "$FAIL" -eq 0 ]; then
  printf "${GREEN}ALL CHECKS PASSED${RESET} — Phase 0 gate is clear. Safe to start Phase 1 work.\n"
  exit 0
else
  printf "${RED}%d CHECK(S) FAILED${RESET} — fix drift before proceeding.\n" "$FAIL"
  exit 1
fi
