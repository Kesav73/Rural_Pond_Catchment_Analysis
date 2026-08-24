#!/usr/bin/env bash
# Runs the backend (FastAPI) and frontend (static Leaflet app) together for local dev.
# Ctrl+C stops both.

set -e

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"

if [ ! -d "$BACKEND_DIR/.venv" ]; then
  echo "Backend venv not found at backend/.venv — create it first:"
  echo "  cd backend && python3.12 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt"
  exit 1
fi

if [ ! -f "$BACKEND_DIR/.env" ]; then
  echo "backend/.env not found — copy backend/.env.example to backend/.env and fill in DATABASE_URL first."
  exit 1
fi

cleanup() {
  echo ""
  echo "Stopping..."
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null
  wait "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null
}
trap cleanup EXIT INT TERM

(
  cd "$BACKEND_DIR"
  source .venv/bin/activate
  uvicorn app.main:app --reload --port 8000
) &
BACKEND_PID=$!

(
  cd "$FRONTEND_DIR"
  python3 -m http.server 5500
) &
FRONTEND_PID=$!

echo "Backend:  http://127.0.0.1:8000  (docs at /docs)"
echo "Frontend: http://127.0.0.1:5500/index.html"
echo "Press Ctrl+C to stop both."

wait "$BACKEND_PID" "$FRONTEND_PID"
