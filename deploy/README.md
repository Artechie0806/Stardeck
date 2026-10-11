# Running a hosted Stardeck

One small VPS runs Caddy and the backend. The AI runs on your own GPU at home, reached over
Tailscale. When that PC is off, the AI pauses; dashboards, filters and share links keep working.

```
browser ──HTTPS──▶ Caddy (VPS) ──▶ backend (VPS) ──Tailscale──▶ LM Studio (your PC)
                   app.stardeck.example    the app
                   share.stardeck.example  public share links (public API only)
```

See [docs/hosting.md](../docs/hosting.md) for plans and the `stardeck-admin` commands.

## 1. Domain

Point `app.stardeck.example` and `share.stardeck.example` (A/AAAA records) at the VPS.

## 2. The VPS

- **Size and system:** Ubuntu 24.04, 2 vCPU / 4 GB, close to your users.
- **Access:** SSH keys only, and `unattended-upgrades` on.
- **Firewall:** `ufw` allowing only 22, 80 and 443 (and 443/udp).
- **Memory:** a 2 GB swap file.
- **Docker:** the Docker Engine with the compose plugin. Log rotation is already set per service.

## 3. Tailscale

1. **Install:** install Tailscale on the VPS and on the PC.
2. **Tags:** tag them `tag:vps` and `tag:gpu`.
3. **ACL:** let only the VPS reach the AI:
   ```json
   "acls": [{ "action": "accept", "src": ["tag:vps"], "dst": ["tag:gpu:1234"] }]
   ```
4. **Check:** once the stack is up (step 6), run this from the VPS:
   ```
   docker compose -f deploy/docker-compose.hosted.yml exec backend \
     python -c "import urllib.request; print(urllib.request.urlopen('http://100.x.y.z:1234/v1/models').read()[:200])"
   ```

## 4. LM Studio on the PC

- **Serving:** serve on the local network, with the Windows/Linux firewall limiting port 1234 to the Tailscale range (100.64.0.0/10).
- **Model:** keep `qwen/qwen3.5-9b` loaded and never unloaded, with full GPU offload and about 16k context.
- **Parallel requests:** set Max Concurrent Predictions to 3 (2 if VRAM goes above 11 GB). Set `STARDECK_LLM_PARALLEL` to the same number.
- **Startup:** start the server on login, and turn request logging off.
- **Power and disk:** the PC never sleeps, and its disk is encrypted.

## 5. Settings

```
cp deploy/.env.example deploy/.env
```

Fill in every value. `deploy/.env` is ignored by git, so never commit it.

- **Secret key:** make one as the file describes, and keep a copy in your password manager. Without it, stored database passwords can't be read.
- **Your own terms:** if other people will use your server, publish a privacy policy and terms that fit where you and they are.

## 6. Start

```
docker compose -f deploy/docker-compose.hosted.yml up -d --build
docker compose -f deploy/docker-compose.hosted.yml exec backend uv run --frozen --no-dev stardeck-admin demo setup
```

`demo setup` prints the demo's share link. `https://share.stardeck.example/demo` always opens the newest one, so it keeps working after the demo is rebuilt.

Then create a workspace and invite its first admin:

```
alias sd='docker compose -f deploy/docker-compose.hosted.yml exec backend uv run --frozen --no-dev stardeck-admin'
sd workspace create "Acme" --plan community
sd invite asha@acme.in --workspace ws-…   # prints an invitation email to send
```

## 7. Backups

`deploy/backup.sh` copies the metadata database and uploads, encrypts them with
[age](https://age-encryption.org), and copies them off the server with rclone (e.g. Cloudflare R2
or Backblaze B2).

1. **Encryption key:** make an age key pair on your own computer. Only the public key goes on the server.
2. **Settings:** put `AGE_RECIPIENT`, `RCLONE_REMOTE` and (optionally) `HEALTHCHECK_URL` in `/etc/stardeck-backup.env`.
3. **Schedule:** run it from cron every 6 hours:
   ```
   0 */6 * * * /opt/stardeck/deploy/backup.sh >> /var/log/stardeck-backup.log 2>&1
   ```
4. **Restore drill, once a month:** download a backup, decrypt it, and start a scratch copy on your PC with the files in its data folder.

The secret key isn't in the backups. Keep it separately, as above.

## 8. Monitoring

- **Uptime:** watch `https://app.stardeck.example/api/ping` and `https://share.stardeck.example/demo` (e.g. UptimeRobot).
- **AI:** alert only after 30 minutes offline. Short pauses are normal when the PC restarts.
- **Backups:** healthchecks.io via `HEALTHCHECK_URL`.

## Updating

```
git pull
docker compose -f deploy/docker-compose.hosted.yml up -d --build
```

The backend backs up its database before any migration (`/data/backups`).
