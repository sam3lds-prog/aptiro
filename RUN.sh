#!/usr/bin/env bash
# Aptiro - zero-config local run (SQLite, mock AI, no Docker needed).
# Starts the API and the single-file UI together. Ctrl-C stops both.
#
#   ./RUN.sh
#
# Production (Postgres + pgvector) path instead:  docker compose up --build
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

cd "$ROOT/backend"
if [ ! -d .venv ]; then
  echo "Creating virtualenv + installing requirements (first run only)..."
  python3 -m venv .venv
  ./.venv/bin/pip -q install -r requirements.txt
fi

echo "API  -> http://localhost:$API_PORT  (docs: /docs)"
echo "UI   -> http://localhost:$UI_PORT"
echo "DB   -> SQLite (zero-config). AI -> mock (deterministic)."
echo

./.venv/bin/uvicorn app:app --host 0.0.0.0 --port "$API_PORT" &
API_PID=$!
( cd "$ROOT/frontend" && exec python3 -m http.server "$UI_PORT" >/dev/null 2>&1 ) &
UI_PID=$!

cleanup() { echo; echo "Shutting down..."; kill "$API_PID" "$UI_PID" 2>/dev/null || true; }
trap cleanup INT TERM EXIT
wait "$API_PID"
