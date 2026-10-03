# Running Stardeck as a service

Hosted mode turns one Stardeck server into many private workspaces, one per organisation.

- People join by invitation and sign in with their email.
- Each workspace only ever sees its own data sources, dashboards and people.
- You, the operator, manage everything with the `stardeck-admin` command on the server.

Self-hosted installs don't need any of this. To put it on a server, with the landing site, share
links and backups, follow [deploy/README.md](../deploy/README.md).

## Configuration

Set these in the backend's environment (see [configuration.md](configuration.md#hosted-mode-stardeck-cloud)):

```bash
STARDECK_HOSTED=true
STARDECK_PUBLIC_URL=https://app.example.com
STARDECK_OPERATOR_EMAILS=you@example.com
STARDECK_SECRET_KEY=...            # keep a copy in your password manager
STARDECK_LLM_BASE_URL=http://100.x.y.z:1234/v1   # your AI server, e.g. over Tailscale
STARDECK_LLM_MODEL=qwen/qwen3.5-9b
```

Two rules for the server:

- **Serve the app over HTTPS.** Sign-in cookies are Secure.
- **Run one backend process.** Don't add uvicorn `--workers`.

What hosted mode changes, compared with self-hosted:

- **Accounts:** they come only from invitations. There's no open mode and no shared password.
- **Data:** data sources are network databases or uploads, never file paths on the server. Network databases must be on the public internet: private and internal addresses, including Tailscale's, are refused.
- **Sign-in:** limited to 20 attempts per address and 8 failures per account in 15 minutes.
- **Links:** invitation and reset links work once. Invitations last 72 hours, resets 24.
- **AI details:** only operators see the AI server's address and errors.

## Share links

Editors share a dashboard from its Share button. A link opens a read-only view for anyone who has it,
without signing in.

What people with a link can do:

- **See the charts,** but never the SQL behind them.
- **Change the view:** pick dates, choose values in the dashboard's saved filters, and click a chart to filter the rest.
- **Nothing else:** no editing, no assistant, no other dashboards.

They see what the dashboard's editors see. Data access rules for people in the workspace don't
apply to links, and the Share dialog says so.

How the public side is protected:

- **Narrow requests:** it accepts only saved filters and clicks on the dashboard's own charts. The server traces each click from the chart's SQL, so nothing sent from the browser becomes SQL.
- **No AI:** chart labels come from the cache, and maps only show places already looked up.
- **Rate limits:** 300 calls and 40 dashboard runs a minute per address, and 6 shared dashboards running at once across the server.
- **No cookies:** in hosted mode, links live on `STARDECK_SHARE_URL`, where Caddy forwards only `/api/public/*`. Sign-in cookies never reach that host.

More about links:

- **Expiry:** links can expire after 7, 30 or 90 days, or never. Editors turn them off in the Share dialog. A dashboard can have 10 links at most.
- **Badge:** Community links show a small "Made with Stardeck" badge.
- **Operator controls:** `stardeck-admin share list [--workspace ws-…]` shows every working link and its views. `stardeck-admin share revoke <token>` turns one off, for example after an abuse report.

## The public demo and the waitlist

```bash
stardeck-admin demo setup
```

This creates (or refreshes) the `ws-demo` workspace. The workspace has the sample data, City and
Category filters on its dashboard, and a share link. `/demo` on the share host always opens the
newest demo link, with a four-step tour. The demo shows a "Join the waitlist" bar instead of the
badge.

The website's form posts to `/api/public/waitlist`:

- **Limits:** 5 sign-ups an hour per address, and 300 a day overall.
- **Same answer:** it responds the same way whether or not the email was already listed.
- **Bots:** a hidden field catches them.
- **Pings:** set `STARDECK_WAITLIST_NTFY_URL` or the Telegram settings to get a message for each new sign-up.

## Plans

Each workspace is on Community (free) or Enterprise. The server enforces the limits and the app
locks things up front, so nobody gets stuck halfway through.

| | Community | Enterprise |
|---|---|---|
| Data sources (the sample data doesn't count) | 1 | unlimited |
| Dashboards (the sample doesn't count) | 2 | unlimited |
| Data sources per dashboard | 1 | 10 |
| People, roles and data access rules | – | ✓ |
| Measures, editing table links, data preparation | – | ✓ |
| Upload size | 25 MB | 100 MB |
| AI each day: dashboard builds / assistant messages (AI suggestions count as messages) | 3 / 5 | 30 / 60 |
| "Made with Stardeck" on shared dashboards | ✓ | – |

Some things to know about the limits:

- **Changing them:** they're defined in one place, `backend/src/stardeck/plans.py`.
- **AI allowances:** they reset at midnight, India time. Each dashboard build or assistant message counts once, however many AI calls it takes. Nothing counts when the failure was on our side: the AI offline or busy, or the page closed before the build got going. The app shows how many are left today.
- **Downgrades:** they never break anything. Existing measures, prepared tables and multi-source dashboards keep working; they just can't be changed until the workspace upgrades.
- **Upgrade links:** set `STARDECK_UPGRADE_URL` to your pricing page or a `mailto:` link.

## The AI server

Every workspace shares one AI server, so Stardeck queues the work instead of letting it pile up:

- **How many at once:** set `STARDECK_LLM_PARALLEL` to your AI server's concurrent-request setting (3 suits a 9B model on a 12 GB GPU in LM Studio). One request is always kept free for the assistant, so builds and suggestions can't crowd out chat.
- **When it's busy:** up to 8 requests wait their turn. Past that, or after 45 s (chat) or 2 minutes (builds), people see "The AI is busy. Try again in a minute."
- **Dashboard builds:** at most 2 at once across the server and 1 per workspace. Closing the page stops the build; anything not yet started is skipped and nothing is saved.
- **Chart labels:** shortened by the AI only within a separate daily allowance and when it's free within 3 s; otherwise they're shortened without it.
- **When it's off:** Stardeck notices on the first failed call and answers at once for the next 20 s instead of waiting on timeouts. The app checks every minute and says "AI is paused"; dashboards, filters and sharing keep working.

## Day to day

Run the commands where the server's data lives. With Docker, prefix them with:

```bash
docker compose exec backend uv run stardeck-admin ...
```

### Waitlist and invitations

```bash
stardeck-admin waitlist import form-export.csv      # an "email" column, plus name, company, role, use_case
stardeck-admin waitlist add asha@acme.in --name "Asha Rao" --company "Acme"
stardeck-admin waitlist list --status new
stardeck-admin waitlist approve asha@acme.in --plan community   # creates the workspace and prints the invitation
stardeck-admin waitlist reject someone@example.com
```

`approve` creates a workspace with sample data, named after their company. It prints the invitation
link and an email you can paste. Opening the link lets them choose a name and password, and then
they're signed in as the workspace's admin.

To add someone else to an existing workspace:

```bash
stardeck-admin invite ravi@acme.in --workspace ws-1a2b3c4d5e6f7a8b --role editor
```

Workspace admins can also add people themselves on the People page. The page shows the sign-in
details to send them.

### Accounts

```bash
stardeck-admin user reset-link asha@acme.in   # a link to choose a new password (signs out other browsers)
stardeck-admin user disable asha@acme.in      # can't sign in; signed out now
stardeck-admin user enable asha@acme.in
stardeck-admin sessions revoke asha@acme.in   # sign out everywhere
```

People can also change their own password from the key button next to their name.

### Workspaces

```bash
stardeck-admin workspace list                    # plan, status, sources, dashboards, people, last sign-in
stardeck-admin workspace create "Acme" --plan enterprise
stardeck-admin workspace plan ws-1a2b... enterprise
stardeck-admin workspace limits ws-1a2b... '{"sources": 3, "features": ["measures"]}'   # an exception (--clear to remove)
stardeck-admin workspace limits ws-1a2b... '{"ai_messages_per_day": 60}'                 # e.g. a busy trial
stardeck-admin workspace suspend ws-1a2b...      # nobody in it can sign in (resume to undo)
stardeck-admin workspace delete ws-1a2b... --yes # its data, uploaded files and people, for good
```

Changes reach the running server within 30 seconds.

### Backups

```bash
stardeck-admin backup /backups
```

This takes a consistent copy of the metadata database plus every uploaded file. The encryption
key isn't included: keep `STARDECK_SECRET_KEY` (or `data/secret.key`) somewhere separate.

Upgrades back up the database by themselves before changing it, into `data/backups/`.
