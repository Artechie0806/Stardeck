<h1>
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/images/logo-dark.png">
    <img src="docs/images/logo-light.png" alt="Stardeck" height="56">
  </picture>
</h1>

**AI-built dashboards for your database, running entirely on your own hardware.**
**A free, open-source, local-AI alternative to cloud AI dashboard tools.**

[![CI](https://github.com/Artechie0806/Stardeck/actions/workflows/ci.yml/badge.svg)](https://github.com/Artechie0806/Stardeck/actions/workflows/ci.yml)
[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Version](https://img.shields.io/badge/version-2.5.0-informational.svg)](CHANGELOG.md)

Connect a database or upload a spreadsheet, and Stardeck's local AI builds a dashboard for it.
Then refine it by chatting: *"add revenue by month"*, *"only show the top 5"*, *"sts 9 means refunded"*.
Your data and your AI never leave your network.

![A dashboard Stardeck generated](docs/images/generated-dashboard.png)

**New in 2.5:** Stardeck is open source, and it now connects to SQL Server, Oracle, Snowflake, BigQuery,
Redshift, Databricks, ClickHouse, Trino and DuckDB. See the [changelog](CHANGELOG.md).

## Why Stardeck?

Cloud AI assistants can now build dashboards from your data too. Stardeck does the same job in a
different way:

| | Stardeck | Cloud AI dashboard tools |
|---|---|---|
| **Cost** | Free and open source (Apache-2.0); no per-seat plan | Paid subscription |
| **Where the AI runs** | Your own GPU or server (Ollama, LM Studio, vLLM, llama.cpp) or any OpenAI-compatible endpoint you choose | The vendor's cloud |
| **Where your data goes** | Nowhere you didn't choose: with a local model, schemas and results stay on your network | Sent to the vendor's model to build and answer |
| **Offline / air-gapped** | Works with no internet (maps aside) | Needs an internet connection and an account |
| **Databases** | 12 databases and warehouses, plus CSV and Excel uploads | Mostly cloud warehouses, through the vendor's connectors |
| **Control** | Read the code, change the prompts, pick and swap the model | Fixed by the vendor |

If your data can't leave the building, or you don't want to pay per seat for AI dashboards,
Stardeck is built for you.

## Features

- **One-click dashboards.** An AI planner picks 6–9 useful widgets (KPIs, trends, breakdowns,
  top-N tables), builds each one, and lays them out. About 30–40 s with a 9B model.
- **A dashboard assistant with two agents.** The *dashboard builder* adds, changes, renames, removes or explains
  widgets in plain English. The *data analyst* answers questions ("which city grew fastest?") with real
  queries, shows its numbers and working, and can turn an answer into a chart.
  Every change is shown as a preview you confirm; nothing is applied behind your back.
- **Explain your data once.** Cryptic columns like `col1`, codes like `sts = 9` and units like
  paise are flagged automatically. Describe them on the Definitions page or in chat (or let the AI
  guess and review its guesses), and every chart gets them right. In our evals this lifts accuracy
  from 9/19 to 19/19.
- **Built for non-technical users, honest with technical ones.** Every widget has a
  plain-English summary, and the exact SQL is one click away, editable, with a live preview.
- **Read-only by design.** Only single `SELECT` statements are allowed, functions that read files,
  URLs or other servers are refused, and queries have a timeout. Where the database supports it,
  the connection itself is also opened read-only
  ([which ones](docs/configuration.md#data-sources)).
- **Filters and cross-filtering, like Power BI.** Add a filter for any column, or click a bar or slice
  to filter the whole dashboard by it. Filters follow the links between tables (customers → orders →
  order items), and a chart a filter can't reach says so.
- **Dates that can't be hallucinated.** Financial year (April–March by default), quarters, recent
  periods or a custom range, applied by editing the SQL syntax tree, never by the model. KPIs compare
  against the previous period.
- **Measures.** Define "Revenue" or "Refund rate" once; every chart, the assistant and the analyst use
  the same formula, so the numbers always agree.
- **People, roles and row-level security.** Viewers, editors and admins with their own sign-in. Limit
  a viewer to their rows (e.g. their branch or city); the rule is enforced in the SQL of every query
  they run.
- **Several data sources on one dashboard.** Put your sales database and a targets spreadsheet side by
  side; each chart uses the right one.
- **Transform data like Power Query.** Build *prepared tables* step by step: filter, rename, change
  types, replace codes with labels, fill blanks, calculated and conditional columns, split columns,
  date parts, merge and append tables, group and summarize, pivot and unpivot. A live preview shows each step, column headers have quick
  actions, and you can describe a change in plain English for the AI to turn into a step. Nothing is
  written to your database: recipes compile to SQL and run live, on every connector.
- **20+ chart types, like Power BI and Tableau.** Column/bar (clustered, stacked, 100% stacked),
  line, area (stacked), waterfall, pie, donut, treemap, funnel, scatter, bubble, heatmap, matrix
  (pivot with totals), radar, Sankey, histogram, box plot, gauge, number cards, tables, and maps on
  OpenStreetMap. The chart menu only offers types that fit the widget's data, and the AI picks
  sensible ones on its own (and fixes ones that don't fit).
- **Share and export.** Public read-only links (no sign-in, no SQL, can expire), PDF export that keeps every
  chart on one page, CSV download per widget.
- **Your databases and files.** PostgreSQL, MySQL/MariaDB, SQL Server, Oracle, Snowflake, BigQuery,
  Redshift, Databricks, ClickHouse, Trino, DuckDB, SQLite, CSV and Excel (see
  [data sources](docs/configuration.md#data-sources)). Charts load one by one as their queries finish, and each
  source runs a limited number of queries at once. Light and dark themes. Works on phones.
- **Run it for others.** Hosted mode gives every organisation its own private workspace, with
  invitation links, sign-in by email and an operator CLI. See [docs/hosting.md](docs/hosting.md), and
  [deploy/README.md](deploy/README.md) to run it on a server with HTTPS and backups.

![Some of the chart types](docs/images/chart-gallery.png)

| Definitions | Assistant | Phone |
|---|---|---|
| ![](docs/images/definitions.png) | ![](docs/images/assistant.png) | ![](docs/images/mobile.png) |

## Quick start

You need [Docker](https://docs.docker.com/get-docker/) and a machine that can run a local model
(the defaults suit a GPU with 8 GB+ VRAM, or Apple Silicon).

```bash
git clone https://github.com/Artechie0806/Stardeck.git stardeck && cd stardeck
cp .env.example .env            # pick a model; optionally set STARDECK_PASSWORD
docker compose up -d
docker compose logs -f ollama-pull   # the first start downloads the model
```

Open <http://localhost:3000>. A **Retail demo** data source and dashboard are included so you can
try everything before connecting your own data.

### Already running LM Studio, vLLM or llama.cpp?

Stardeck talks to any OpenAI-compatible endpoint. Point it at yours in `.env`:

```bash
STARDECK_LLM_BASE_URL=http://host.docker.internal:1234/v1   # LM Studio
STARDECK_LLM_MODEL=qwen/qwen3.5-9b
```

See [docs/configuration.md](docs/configuration.md) for every setting.

## Choosing a model

Measured on the bundled eval suite (19 dashboard questions over the demo database):

| Model | With definitions | Without | Avg. time per widget |
|---|---|---|---|
| Qwen 3.5 9B | 19/19 | 9/19 | ~4 s |
| Qwen 3.6 35B-A3B | 19/19 | 9/19 | ~7 s |

Measured on an RTX 5070 (12 GB) with LM Studio; every chart type was also judged sensible (13/13).
Small models vary a little between runs: on the current demo data the 9B scores 18–19/19, the
occasional miss being a unit slip (dividing an amount that is already in rupees by 100).
The 9B model is the recommended default: as accurate here, faster, and fits in less memory. Writing definitions for
cryptic columns matters far more than model size. Run the evals on your own hardware with
`uv run python evals/run_eval.py --model <name>` (see [docs/architecture.md](docs/architecture.md#evals)).

## Development

```bash
./scripts/dev.sh        # backend on :8000, frontend on :5173 (reachable from your LAN)
```

or separately:

```bash
cd backend && uv sync --all-extras && uv run uvicorn stardeck.main:app --reload --reload-dir src
cd frontend && npm install && npm run dev
```

`--all-extras` installs the drivers for every database. To keep the install small, add only the
ones you need, e.g. `uv sync --extra snowflake --extra bigquery`. The Docker image includes them all.

Tests: `cd backend && uv run pytest` · Lint: `uv run ruff check .` and `cd frontend && npm run lint`.

More: [architecture](docs/architecture.md) · [configuration](docs/configuration.md) ·
[adding a connector](docs/connectors.md) · [changelog](CHANGELOG.md)

## Contributing

Bug reports, ideas and pull requests are welcome. Read [CONTRIBUTING.md](CONTRIBUTING.md) first,
and please follow the [code of conduct](CODE_OF_CONDUCT.md). Found a security problem? Report it
privately, as described in [SECURITY.md](SECURITY.md), not in a public issue.

## Security notes

- Connect with a **read-only database account**. Stardeck enforces read-only itself, but a
  read-only account is the strongest guarantee.
- If Stardeck is reachable by others (LAN, VPN), turn on sign-in (Settings → Turn on sign-in), or
  set `STARDECK_PASSWORD` for one shared password. Until then, anyone who can open Stardeck is an admin.
- Row rules apply to **viewers**. Editors and admins see every row (as in Power BI), so give anyone who
  must only see some rows the viewer role.
- Saved database passwords, tokens and keys are encrypted with a key in `data/secret.key` (or
  `STARDECK_SECRET_KEY`). Back it up together with the data volume.
- Some databases (SQL Server, Redshift, Snowflake, BigQuery, Databricks, Trino) have no read-only
  connection mode. For
  those, a read-only account is the only database-level guarantee. BigQuery queries also have a cap
  on data scanned.
- Columns marked **Hide from AI** are never sent to the model.
- **Maps** load tiles from OpenStreetMap and look up place names (e.g. "Pune") with OpenStreetMap's
  Nominatim service; only map views and place names leave your network, and lookups are cached.
  Set `STARDECK_GEOCODER=off` or point the map settings at self-hosted servers to keep it all local.

## License

[Apache-2.0](LICENSE)
