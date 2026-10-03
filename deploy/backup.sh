#!/usr/bin/env bash
# Back up Stardeck Cloud: the metadata database and every uploaded file, encrypted with age and
# copied off the server with rclone. Run from cron every 6 hours (see README.md).
#
# Needs: age, rclone, and these in the environment (or in /etc/stardeck-backup.env):
#   AGE_RECIPIENT   public key to encrypt to (keep the private key OFF this server)
#   RCLONE_REMOTE   e.g. r2:stardeck-backups
#   HEALTHCHECK_URL optional: pinged after a good backup (e.g. healthchecks.io)
set -euo pipefail
[ -f /etc/stardeck-backup.env ] && . /etc/stardeck-backup.env
: "${AGE_RECIPIENT:?set AGE_RECIPIENT}" "${RCLONE_REMOTE:?set RCLONE_REMOTE}"

cd "$(dirname "$0")"
compose=(docker compose -f docker-compose.hosted.yml)
stamp=$(date -u +%Y%m%d-%H%M%S)
local_dir=/var/backups/stardeck
mkdir -p "$local_dir"

# A consistent copy inside the container (SQLite's backup API), then out of it.
"${compose[@]}" exec -T backend uv run --frozen --no-dev stardeck-admin backup "/data/backups/$stamp"
"${compose[@]}" cp "backend:/data/backups/$stamp" "$local_dir/$stamp"
"${compose[@]}" exec -T backend rm -rf "/data/backups/$stamp"

tar -C "$local_dir" -cz "$stamp" | age -r "$AGE_RECIPIENT" > "$local_dir/stardeck-$stamp.tar.gz.age"
rm -rf "${local_dir:?}/$stamp"
rclone copy "$local_dir/stardeck-$stamp.tar.gz.age" "$RCLONE_REMOTE"
find "$local_dir" -name 'stardeck-*.tar.gz.age' -mtime +7 -delete   # the remote keeps the history

if [ -n "${HEALTHCHECK_URL:-}" ]; then curl -fsS -m 10 --retry 3 "$HEALTHCHECK_URL" > /dev/null; fi
echo "Backed up stardeck-$stamp"
