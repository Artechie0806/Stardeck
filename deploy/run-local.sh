#!/usr/bin/env bash
# Stardeck Cloud on this computer, as on the server (minus HTTPS): the website, the app and share
# links behind Caddy, a hosted-mode backend with its own data (deploy/.local), and the public demo.
# Your own dev setup (Vite on 5173, the backend on 8000) isn't touched.
#
#   deploy/run-local.sh          start; Ctrl+C stops everything
#   deploy/run-local.sh stop     stop a copy that was started in the background
#
# The AI is whatever backend/.env points at (e.g. LM Studio). Ports and the test account can be
# changed with SITE_PORT, APP_PORT, SHARE_PORT, API_PORT, LOCAL_EMAIL and LOCAL_PASSWORD.
set -euo pipefail

here=$(cd "$(dirname "$0")" && pwd)
repo=$(dirname "$here")
state_dir="$here/.local"
SITE_PORT=${SITE_PORT:-4000}
APP_PORT=${APP_PORT:-4001}
SHARE_PORT=${SHARE_PORT:-4002}
API_PORT=${API_PORT:-8001}
LOCAL_EMAIL=${LOCAL_EMAIL:-you@example.com}
LOCAL_PASSWORD=${LOCAL_PASSWORD:-stardeck-local}

if [ "${1:-}" = stop ]; then
  if [ -f "$state_dir/run.pid" ] && kill "$(cat "$state_dir/run.pid")" 2>/dev/null; then echo "Stopped."; else echo "Not running."; fi
  exit 0
fi

mkdir -p "$state_dir/bin" "$state_dir/data"
caddy=$(command -v caddy || true)
if [ -z "$caddy" ]; then
  caddy="$state_dir/bin/caddy"
  if [ ! -x "$caddy" ]; then
    os=$(uname -s | tr '[:upper:]' '[:lower:]')
    arch=$(uname -m | sed -e 's/x86_64/amd64/' -e 's/aarch64/arm64/')
    echo "Downloading Caddy ($os/$arch)…"
    curl -fsSL "https://caddyserver.com/api/download?os=$os&arch=$arch" -o "$caddy.part"
    mv "$caddy.part" "$caddy"
    chmod +x "$caddy"
  fi
fi

echo "Building the app…"
(cd "$repo/frontend" && npm run build > "$state_dir/build.log" 2>&1) || { cat "$state_dir/build.log"; exit 1; }

export STARDECK_HOSTED=true
export STARDECK_DATA_DIR="$state_dir/data"
export STARDECK_PUBLIC_URL="http://localhost:$APP_PORT"
export STARDECK_SHARE_URL="http://localhost:$SHARE_PORT"
export STARDECK_SITE_URL="http://localhost:$SITE_PORT"
export STARDECK_OPERATOR_EMAILS="$LOCAL_EMAIL"
export LOCAL_EMAIL LOCAL_PASSWORD
cd "$repo/backend"

# First run: a Community workspace with an account to sign in with, and the public demo.
uv run --quiet python - <<'EOF'
import os
from stardeck.config import Settings
from stardeck.deps import AppState

state = AppState.create(Settings())
email = os.environ["LOCAL_EMAIL"]
if state.users.find(email) is None:
    ws = state.create_workspace("My company", "community")
    with state.db.new_session(ws.id) as db:
        state.users.create(db, email, "You", os.environ["LOCAL_PASSWORD"], "admin")
    print(f"Created a Community workspace for {email}.")
EOF
uv run --quiet stardeck-admin demo setup > /dev/null

uv run --quiet uvicorn stardeck.main:app --port "$API_PORT" > "$state_dir/backend.log" 2>&1 &
api=$!
SCHEME=http BACKEND="127.0.0.1:$API_PORT" SITE_ROOT="$repo/site" APP_ROOT="$repo/frontend/dist" \
  SITE_DOMAIN="localhost:$SITE_PORT" APP_DOMAIN="localhost:$APP_PORT" SHARE_DOMAIN="localhost:$SHARE_PORT" \
  ACME_EMAIL=local@example.com CONTACT_EMAIL=hello@example.com OPERATOR_NAME="Stardeck (local)" OPERATOR_CITY="your city" \
  XDG_DATA_HOME="$state_dir/caddy" XDG_CONFIG_HOME="$state_dir/caddy" \
  "$caddy" run --config "$here/Caddyfile" --adapter caddyfile > "$state_dir/caddy.log" 2>&1 &
web=$!
echo $$ > "$state_dir/run.pid"
stop() {
  trap - EXIT INT TERM
  kill "$api" "$web" 2> /dev/null || true
  wait 2> /dev/null || true
  rm -f "$state_dir/run.pid"
}
trap stop EXIT INT TERM

for _ in $(seq 1 60); do
  curl -fs "http://localhost:$APP_PORT/api/ping" > /dev/null 2>&1 && break
  if ! kill -0 "$api" 2> /dev/null || ! kill -0 "$web" 2> /dev/null; then
    echo "Something didn't start. Logs: $state_dir/backend.log, $state_dir/caddy.log"
    exit 1
  fi
  sleep 0.5
done

cat <<EOF

Stardeck Cloud is running on this computer:
  Website      http://localhost:$SITE_PORT
  Live demo    http://localhost:$SITE_PORT/demo   (opens the share host, with the tour)
  App          http://localhost:$APP_PORT          sign in as $LOCAL_EMAIL / $LOCAL_PASSWORD
  Share links  http://localhost:$SHARE_PORT

Data: $state_dir/data. Logs: $state_dir/backend.log and caddy.log.
Ctrl+C stops it (or: deploy/run-local.sh stop).
EOF
wait
