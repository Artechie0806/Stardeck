# Configuration

All settings are environment variables prefixed with `STARDECK_`. With Docker, put them in `.env`
(see `.env.example`); for local development, in `backend/.env`.

## Model

| Variable | Default | Meaning |
|---|---|---|
| `STARDECK_LLM_BASE_URL` | `http://localhost:11434/v1` | Any OpenAI-compatible endpoint (Ollama, LM Studio, vLLM, llama.cpp, SGLang). |
| `STARDECK_LLM_MODEL` | `qwen3.5:9b` | Model id exactly as the server lists it at `<base_url>/models`. |
| `STARDECK_LLM_API_KEY` | `not-needed` | Sent as a bearer token. Local servers ignore it. |
| `STARDECK_LLM_THINKING` | `off` | `off` disables "thinking" on reasoning models (much faster; Stardeck's calls are short and schema-constrained). `on` allows it. `server` sends no switch, for strict APIs that reject unknown fields. |
| `STARDECK_LLM_MAX_TOKENS` | `4096` | Cap per model reply. |
| `STARDECK_LLM_TEMPERATURE` | `0.1` | Low on purpose: SQL should be deterministic. |
| `STARDECK_LLM_TIMEOUT_S` | `180` | Per request. |
| `STARDECK_LLM_PARALLEL` | `4` | How many AI requests Stardeck sends at once. Match your AI server's parallel setting (LM Studio: *Max Concurrent Predictions*; Ollama: `OLLAMA_NUM_PARALLEL`). One is always kept free for the assistant, so dashboard builds and suggestions use at most one less. Up to 8 more wait their turn: 45 s for the assistant, 2 minutes for builds. |

Common setups:

```bash
# Ollama (bundled in docker-compose)
STARDECK_LLM_MODEL=qwen3.5:9b   # or qwen3.6:35b-a3b with ~24 GB VRAM

# LM Studio on the host, Stardeck in Docker
STARDECK_LLM_BASE_URL=http://host.docker.internal:1234/v1
STARDECK_LLM_MODEL=qwen/qwen3.5-9b

# vLLM / llama.cpp server
STARDECK_LLM_BASE_URL=http://gpu-box:8000/v1
```

## Storage and security

| Variable | Default | Meaning |
|---|---|---|
| `STARDECK_DATA_DIR` | `data` (`/data` in Docker) | Metadata DB, encryption key, uploaded files, demo DB. |
| `STARDECK_DATABASE_URL` | SQLite in the data dir | Any SQLAlchemy URL for Stardeck's own metadata, e.g. Postgres. |
| `STARDECK_SECRET_KEY` | generated into `data/secret.key` | Fernet key that encrypts saved database passwords. |
| `STARDECK_PASSWORD` | unset | Shared password for the whole install. When set, everyone signs in with it, until an admin creates the first account (Settings → Turn on sign-in); from then on everyone signs in with their own account. |
| `STARDECK_SESSION_HOURS` | `336` | How long a sign-in lasts. |
| `STARDECK_SEED_DEMO` | `true` | Add the Retail demo source and dashboard on first start. |

## Maps

| Variable | Default | Meaning |
|---|---|---|
| `STARDECK_MAP_TILES_URL` | `https://tile.openstreetmap.org/{z}/{x}/{y}.png` | Tile server for map widgets (loaded by the browser). |
| `STARDECK_MAP_ATTRIBUTION` | `© OpenStreetMap contributors` | Shown on every map; keep it when using OSM tiles. |
| `STARDECK_GEOCODER` | `nominatim` | Turns place names into coordinates. `off` disables lookups (maps then need latitude/longitude columns). |
| `STARDECK_GEOCODER_URL` | `https://nominatim.openstreetmap.org` | Use a self-hosted Nominatim to keep lookups inside your network. |

Lookups are throttled to Nominatim's 1 request/second policy and cached permanently in Stardeck's
database, so each place is looked up once.

## Data sources

PostgreSQL, MySQL/MariaDB, SQLite and CSV/Excel uploads work out of the box. The other databases
need their driver, an optional extra of the backend. The Docker image includes all of them.
Without Docker, install them with `cd backend && uv sync --extra all`, or only the ones you need:

| Database | Extra | Read-only connection | Notes |
|---|---|---|---|
| PostgreSQL (and Supabase, Neon, TimescaleDB, CockroachDB) | built in | yes | |
| MySQL / MariaDB | built in | yes | |
| SQLite file, CSV / Excel upload | built in | yes | |
| SQL Server / Azure SQL | `sqlserver` | no | Azure SQL needs encryption "require". |
| Oracle 12c+ | `oracle` | yes | Thin driver, no Oracle Client needed. Plain TCP only. |
| ClickHouse | `clickhouse` | yes | The HTTP interface: 8123, or 8443 with HTTPS. |
| DuckDB file | `duckdb` | yes | Queries can't read other files or URLs. No query timeout. |
| Amazon Redshift | built in | no | |
| Snowflake | `snowflake` | no | A password or an unencrypted PKCS#8 key pair. |
| Google BigQuery | `bigquery` | no | A service account key. Queries that would scan more than "most data one query may scan" (10 GB by default) fail instead of being billed. |
| Databricks SQL warehouse | `databricks` | no | A personal access token. |
| Trino / Starburst | `trino` | no | Passwords are only sent over HTTPS. |

Every query is checked to be a single SELECT first, and functions that read files, URLs or other
servers (DuckDB `read_csv`, ClickHouse `url()`, SQL Server `OPENROWSET`, Oracle `UTL_HTTP`...) are
refused. Where the connection can't be opened read-only, sign in with an account that can only
read. Passwords, tokens, private keys and service account keys are stored encrypted.

Hosted mode doesn't offer SQLite or DuckDB files (only uploads). It checks the address of every
network database on each connection; Trino and Databricks connect over HTTPS by name, so for those
the check runs just before connecting rather than on the very address used. BigQuery keys must use
Google's own token server.

Known limits:

- **Sort as the last step of a prepared table:** Trino and BigQuery may not keep that order (a sort
  followed by "Keep top rows" always works).
- **BigQuery and scanning:** reading a new dataset samples every column. On very large tables this can
  reach the "most data one query may scan" cap; that table then shows an error instead of samples.
- **Docker image size:** the drivers make the backend image about 1 GB. Build with
  `--build-arg EXTRAS=sqlserver` (or another extra) to include only what you use.

## Limits

| Variable | Default | Meaning |
|---|---|---|
| `STARDECK_QUERY_TIMEOUT_S` | `30` | Per query, enforced by the database or, for SQLite, by Stardeck. DuckDB queries have no timeout. |
| `STARDECK_MAX_QUERIES_PER_SOURCE` | `4` | Queries Stardeck runs at once against one data source. Lower it for a busy production database. |
| `STARDECK_MAX_UPLOAD_MB` | `200` | Total size of one CSV/Excel upload. |
| `STARDECK_CORS_ORIGINS` | `["http://localhost:5173"]` | Only needed when the frontend is served from another origin. |

## Dates

| Variable | Default | Meaning |
|---|---|---|
| `STARDECK_FISCAL_YEAR_START` | `4` | Month the financial year starts (1 = January, 4 = April). Quarters follow it. Admins can change it in Settings, which takes precedence. |

## Hosted mode

For running Stardeck as a service: many private workspaces on one server, accounts by invitation,
sign-in by email. Self-hosted installs leave all of this unset. See [hosting.md](hosting.md).

| Variable | Default | Meaning |
|---|---|---|
| `STARDECK_HOSTED` | `false` | Turns hosted mode on. Stardeck refuses to start if `STARDECK_PUBLIC_URL` is missing or `STARDECK_PASSWORD` is set. |
| `STARDECK_PUBLIC_URL` | unset | The app's address, e.g. `https://app.example.com`. Invitation links point here, and only requests from this origin may change things. |
| `STARDECK_UPGRADE_URL` | unset | Where "Upgrade" links in the app go: your pricing page or a `mailto:` link. |
| `STARDECK_OPERATOR_EMAILS` | unset | Comma-separated emails of the people who run the service. They see the AI server's details. |
| `STARDECK_SSRF_DENY` | unset | Comma-separated addresses or ranges data sources may never reach (e.g. the server's own public IP). Private and internal ranges are always blocked. |
| `STARDECK_SHARE_URL` | `STARDECK_PUBLIC_URL` | Where public share links point, e.g. `https://share.example.com`. A separate host means sign-in cookies are never sent with a share link. |
| `STARDECK_SITE_URL` | unset | A page about your service, e.g. the project's GitHub page. Shared dashboards and the demo's "Get Stardeck" button link to it, and map lookups name it as the contact. |
| `STARDECK_DEMO_WORKSPACE_ID` | `ws-demo` | The public demo's workspace. `/demo` opens its newest share link (`stardeck-admin demo setup`). |

Hosted mode needs HTTPS: sign-in cookies are marked Secure (browsers accept them over plain HTTP
only on `localhost`). Run exactly one backend process: sign-in limits live in memory.

## Ports

`docker compose` publishes the app on `STARDECK_PORT` (default 3000) on all interfaces, so other
machines on your network can reach it. Turn on sign-in (or set `STARDECK_PASSWORD`) if that's not what you want, or
bind to localhost: `ports: ["127.0.0.1:3000:80"]`.
