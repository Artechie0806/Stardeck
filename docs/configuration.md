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
| `STARDECK_PASSWORD` | unset | Shared password for the whole install. When set, everyone signs in. |
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

## Limits

| Variable | Default | Meaning |
|---|---|---|
| `STARDECK_QUERY_TIMEOUT_S` | `30` | Per query, enforced by the database (Postgres, MySQL) or by Stardeck (SQLite). |
| `STARDECK_MAX_UPLOAD_MB` | `200` | Total size of one CSV/Excel upload. |
| `STARDECK_CORS_ORIGINS` | `["http://localhost:5173"]` | Only needed when the frontend is served from another origin. |

## Ports

`docker compose` publishes the app on `STARDECK_PORT` (default 3000) on all interfaces, so other
machines on your network can reach it. Set `STARDECK_PASSWORD` if that's not what you want, or
bind to localhost: `ports: ["127.0.0.1:3000:80"]`.
