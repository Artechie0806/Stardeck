#!/usr/bin/env bash
# Run backend and frontend dev servers together; Ctrl+C stops both.
set -euo pipefail
cd "$(dirname "$0")/.."

(cd backend && uv sync -q --all-extras && uv run uvicorn stardeck.main:app --reload --reload-dir src --port 8000) &
BACKEND=$!
trap 'kill $BACKEND 2>/dev/null' EXIT

cd frontend
[ -d node_modules ] || npm install
npm run dev
