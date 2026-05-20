#!/usr/bin/env bash
# Aptiro — zero-config local run (SQLite, mock AI, no Docker needed).
# Starts the FastAPI backend and the Vite dev server together.
# Ctrl-C stops both.
#
#   ./RUN.sh
#
# Production (Postgres + pgvector + built frontend) path instead:
#   docker compose up --build
#
# Phase 7 (frontend foundation): the UI is now a real Vite + React + TS
# app. On first run this script will install node_modules; that takes
# ~30s. Subsequent runs are instant.
set -euo pipefail

API_PORT="${APTIRO_API_PORT:-8000}"
UI_PORT="${APTIRO_UI_PORT:-5173}"
ROOT="$(cd "$(dirname "$0")" && pwd)"

port_busy() { (exec 3<>"/dev/tcp/127.0.0.1/$1") 2>/dev/null && return 0 || return 1; }
for p in "$API_PORT" "$UI_PORT"; do
  if port_busy "$p"; then
    echo "Port $p is already in use. Set APTIRO_API_PORT / APTIRO_UI_PORT and retry." >&2
    exit 1
  fi
done

# --- Backend deps --------------------------------------------------------
cd "$ROOT/backend"
if [ ! -d .venv ]; then
  echo "[backend] creating virtualenv + installing requirements (first run only)..."
  python3 -m venv .venv
  ./.venv/bin/pip -q install -r requirements.txt
fi

# --- Frontend deps -------------------------------------------------------
cd "$ROOT/frontend"
if ! command -v npm >/dev/null 2>&1; then
  echo "npm is required (install Node.js 20+ from https://nodejs.org)." >&2
  exit 1
fi
if [ ! -d node_modules ]; then
  echo "[frontend] installing npm dependencies (first run only)..."
  if [ -f package-lock.json ]; then
    npm ci --no-audit --no-fund
  else
    npm install --no-audit --no-fund
  fi
fi

cd "$ROOT"
echo
echo "API  -> http://localhost:$API_PORT  (docs: /docs)"
echo "UI   -> http://localhost:$UI_PORT"
echo "DB   -> SQLite (zero-config). AI -> mock (deterministic)."
echo

# --- Start backend -------------------------------------------------------
cd "$ROOT/backend"
./.venv/bin/uvicorn app:app --host 0.0.0.0 --port "$API_PORT" &
API_PID=$!

# --- Start Vite dev server ----------------------------------------------
cd "$ROOT/frontend"
APTIRO_API="http://localhost:$API_PORT" \
  npm run dev -- --host 0.0.0.0 --port "$UI_PORT" >/dev/null 2>&1 &
UI_PID=$!

cleanup() {
  echo
  echo "Shutting down..."
  kill "$API_PID" "$UI_PID" 2>/dev/null || true
  wait "$API_PID" 2>/dev/null || true
  wait "$UI_PID" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

wait "$API_PID"
