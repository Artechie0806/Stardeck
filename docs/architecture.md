# Architecture

```
frontend (React + ECharts)  ──/api──▶  backend (FastAPI)  ──SQL (read-only)──▶  your database
                                            │
                                            └──OpenAI-compatible API──▶  local LLM
```

The guiding rule: **the model writes SQL and small JSON specs; deterministic code does
everything else.** Small local models are reliable when every step is narrow and checked.

## Backend modules (`backend/src/stardeck`)

| Module | Job |
|---|---|
| `db.py` | Stardeck's metadata store, and workspace isolation (below). |
| `migrations.py` | Versioned changes to the metadata DB, applied on start after a backup. |
| `workspaces.py` | Workspaces: plan, status and per-workspace settings (financial year), cached briefly. |
| `connectors.py` | Connection settings for every kind of data source, and the (read-only where possible) SQLAlchemy engines built from them. |
| `netguard.py` | Hosted: network databases must be on the public internet; each connection is checked and pinned to the checked address. |
| `uploads.py` | Load CSV/Excel into a managed SQLite file with inferred column types. |
| `schema.py` | Profile tables (columns, samples, row counts, foreign keys); flag cryptic columns; render schema for prompts. |
| `definitions.py` | The semantic layer: column meanings, units, code values, hidden columns, glossary. YAML import/export. |
| `sqlgen.py` | SQL safety: one statement, `SELECT` only, no functions that read files, other databases or sleep. |
| `context.py` | `SourceContext`: everything a query on one source needs for one person (engine, schema, definitions, prepared tables, table links, their row rules, the financial year, a concurrency limit). |
| `query.py` | The one way to run SQL (below): row caps, timeouts, date filters by editing the syntax tree, KPI previous-period values. |
| `rowfilter.py` | Row filters and row-level security: rewrites every table a query reads into a filtered subquery, following table links. |
| `measures.py` | Measures: `MEASURE(name)` expansion and validation. |
| `crossfilter.py` | Traces a clicked chart value back to the column or expression it came from. |
| `grain.py` | Detect join fan-out (summing the "one" side of a one-to-many join) using foreign keys. |
| `charts.py` | Chart catalogue: which result shapes each of the 20 chart types needs, and fallbacks. Mirrored in `frontend/src/lib/chartFit.ts`. |
| `widgets.py` | The widget engine (below). |
| `geo.py` | Place name → coordinates via Nominatim, throttled and cached. |
| `transforms.py` | Prepared tables: step models, a type-aware compiler to SQL (one CTE per step), CTE injection into queries, AI step drafts. |
| `labels.py` | AI-shortened chart labels, validated and cached. |
| `dashboards.py` | Storage, automatic layout, parallel cached execution, AI dashboard generation. |
| `router.py` | Picks the agent for a chat message (one small model call). |
| `chat.py` | The assistant: routes to the builder (intent classification → validated proposals) or the analyst. |
| `analyst.py` | The data analyst: read-only queries step by step → an answer backed by their results. |
| `auth.py`, `users.py`, `principal.py` | Sign-in (open, shared password, or accounts; email accounts when hosted), sessions stored hashed in the DB, roles, per-viewer row rules, changing your password. |
| `invites.py` | Single-use invitation and password-reset links (stored hashed). |
| `ratelimit.py` | In-memory sliding-window limits (sign-in attempts per address and per account). |
| `admin.py` | The operator CLI, `stardeck-admin`: workspaces, invitations, resets, backups. |
| `llm/status.py` | Whether the AI server is up: probed at most every 20 s with short timeouts, and updated by every real call so an offline server fails fast. |
| `llm/gate.py` | The one queue in front of the AI server (below), and the limit on dashboard builds running at once. |
| `plans.py`, `usage.py` | What each plan allows, and counting it: data sources, dashboards and daily AI allowances. |
| `shares.py`, `api/public.py` | Public share links, and what anyone can reach without signing in (below). |
| `streaming.py` | Long work in a thread, streamed as NDJSON, stopped when the browser goes away. |

## Workspaces and isolation

Data sources, dashboards and people belong to a workspace (`WorkspaceScoped` in `db.py`). A
self-hosted install keeps everything in one, `default`; hosted, every organisation has its own.

- Every request's database session is opened for the signed-in person's workspace
  (`deps.get_session`). A `do_orm_execute` hook adds `workspace_id = <theirs>` to every SELECT,
  including counts, `exists()`, subqueries, unions and CTEs, so a forgotten filter can't leak
  another workspace's rows. A `before_flush` hook stamps new rows and refuses to write rows of
  another workspace.
- It fails closed: a query from a session without a workspace raises instead of returning
  everything, and bulk UPDATE/DELETE in a workspace session is refused.
- Platform sessions (sign-in, invitations, the operator CLI) see every workspace and are never used
  to serve a workspace's own requests.
- Ids are 64 random bits; uploads live in `uploads/<workspace>/`; the AI label cache is keyed per
  workspace. Sample data in each workspace reads the one shared, read-only demo file.
- `tests/test_isolation.py` calls every route that takes an id as workspace A with workspace B's
  ids and expects a 404 with B's data untouched; a coverage check fails when a new id-taking route
  isn't in that table.

Hosted mode also refuses anything that could reach the server itself: no file paths as data
sources (uploads only), file sources can't be re-pointed, deletes never leave the uploads folder,
and network databases go through `netguard.py`.

## Running a query

Every query, whether it comes from a widget, a filter's value list, the analyst or a preview, goes through
`query.run(ctx, sql, filters=...)`, so row rules, filters and measures can't be skipped on some path:

1. **Safety check**: one `SELECT`, no blocked functions.
2. **Measures**: `MEASURE(revenue)` becomes the measure's formula, its columns qualified with the
   alias of the measure's table in that `SELECT` (which must be there).
3. **Prepared tables**: their CTE chains are added in front.
4. **Row filters**: the viewer's rules plus the dashboard filters. Each table reference becomes
   `(SELECT * FROM t WHERE ...) AS t`. A rule on `customers` reaches `orders` as
   `customer_id IN (SELECT id FROM customers WHERE ...)`, following table links from the many side to
   the one side, up to 4 links away. Filters that reach none of the tables a query reads are reported
   back, so the chart can say "Not filtered by …". When row rules apply, anything that could reach
   around them is refused: unknown tables, other schemas or databases, CTEs named like real tables,
   table functions.
5. **Row cap and timeout**, then execution, at most `STARDECK_MAX_QUERIES_PER_SOURCE` at a time per
   source.

Table links come from foreign keys, from names (`customer_id` → `customers.id`) and from links people add on
the Definitions page; wrong ones can be turned off there. Row rules apply to viewers only; editors and admins
see every row.

## The widget engine

1. **Pick tables.** If the whole schema fits the prompt budget, use all of it; otherwise rank
   tables lexically against the request (names, descriptions, synonyms, glossary) and add
   foreign-key neighbours so joins remain possible.
2. **Ask the model** for reasoning, SQL, title, summary and a proposed chart, constrained to a JSON
   schema (grammar-constrained decoding on the server, Pydantic validation in Stardeck).
3. **Check before running.** The grain checker rejects double counting; the safety check rejects
   anything but a `SELECT`. Failures go back to the model with a precise explanation (up to 3 tries).
4. **Run** read-only, capped at 1,000 rows. Errors and suspicious empty results are fed back.
5. **Fix the chart** to fit the actual result. The result is described by roles (categories, numbers,
   a second category that splits series, several comparable numbers) and each chart type declares the
   shape it needs (`charts.py`): one value becomes a KPI, a line without a time axis becomes bars, a
   pie with too many parts becomes bars, more than 8 series become a matrix, and so on. Money is
   detected from documented units (e.g. `paise` → INR). Year-over-year results laid out so the years
   never overlap are sent back to the model with an explanation.
6. **Choose the date column** the dashboard filter will restrict, preferring the table being
   measured (orders by order date, not by customer signup date).

## Dashboard generation

A planner call returns 6–9 widget requests (KPIs first, then trends, breakdowns, a top-N table).
With several sources, the planner sees each source's schema under its name and picks one per widget.
Each request runs through the widget engine, three at a time, streaming progress to the browser
as NDJSON. Widgets that fail or return no data are skipped. `auto_layout` places them: KPI row,
trends (paired with a donut if there is one), breakdowns in pairs, tables full width.

The build runs in its own thread and the response only relays its events (`streaming.relay`),
with a `ping` after 10 quiet seconds so proxies keep the stream open. When the browser goes away,
the stream sets a cancel flag: widgets not started are skipped, waiting AI calls give up, and
nothing is saved.

## Printing

The screen grid places widgets absolutely, and browsers can't paginate that: a chart crossing a page
edge was cut in two. Export PDF (`?print=1` on the view page, also Ctrl+P) lays the dashboard out in
normal flow instead (`lib/printing.ts`):

- **Width:** exactly the printable width of A4 landscape.
- **Bands:** widgets go in rows that are each kept on one page. A row too tall for a page gets shorter grid rows.
- **Tables:** they grow to show every row.
- **Look:** charts skip their animations, and the page uses the light theme.

The print dialog opens once every chart has drawn.

## AI capacity

All AI calls go through `llm/gate.py`, sized by `STARDECK_LLM_PARALLEL`:

- **Slots:** chat calls may take any free slot. Background work (builds, suggestions, labels) never takes the last one.
- **Waiting:** at most 8 calls wait, for 45 s (interactive) or 120 s (background), before `LLMBusy`.
- **Offline:** `LLMStatus.offline()` fails calls at once (`LLMUnavailable`) for 20 s after a connection error.
- **Builds:** at most 2 at once overall and 1 per workspace (`GenerationSlots`).
- **Errors:** `ai_error` turns any of these into a 503 with a plain message.

Hosted plans add daily allowances (`usage.Quotas`), charged once per action at the API boundary
and refunded when the failure was on our side. Self-hosted has no allowances, so nothing is counted.

## Share links

A share link's token opens one dashboard as a "share" principal of its workspace, with no row rules
(`shares.OpenLink.principal`). `/api/public/s/{token}/…` returns the dashboard without SQL, and runs it
through the same `run_all` as the app. The filters it accepts are narrow on purpose:

- **Saved filters:** by id. The table and column come from the saved filter, and only the values from the browser.
- **Clicks:** a widget id and the clicked column. The server traces the click from that widget's SQL with `trace_category`, as it does for signed-in people.

Widget errors are replaced by a generic message, since they can quote SQL. The frontend shows the
same `DashboardView` for both (`lib/viewClient.ts`): signed in, it talks to the app's API, and on a
link to the public one. Maps and chart labels find the right client through React context.

## Running a dashboard

Each widget runs against its own source with the date range and the filters for that source, in
parallel, and results stream to the browser as NDJSON lines in the order they finish. Results are
cached per (source, the viewer's rules, SQL, dates, filters). A cross-filter from a click is keyed
to the widget that was clicked, which isn't filtered by it and highlights the picked value instead.

## Measures

A measure is a named aggregate over one table (`SUM(CASE WHEN sts = 1 THEN amt END) / 100.0` on
`orders`) with a display format. Saving one runs it first, so a broken formula is never stored. The
model is told the measures exist and writes `MEASURE(revenue)`; the double-counting check runs on the
expanded SQL, and a chart of a single measure takes the measure's format and direction.

## Prepared tables (data transformation)

A prepared table is a recipe: a base table (or another prepared table) plus ordered steps. The
compiler turns it into one SELECT where each step is a CTE reading the previous one, so depth stays
flat and steps that read their input twice (unpivot) don't duplicate it. Columns and their kinds
(number, text, date) are tracked through every step, which validates the recipe and keeps the SQL
type-correct on strict databases. Any query that mentions a prepared table gets its CTE chain added
in front (dependencies first), so recipes run live against the source and nothing is ever written.

Prepared tables are profiled like tables (row count, samples, inferred types) and appear in the
schema with their recipe as a description; their columns inherit meanings from the source columns.
Recipes that drop rows are hidden from the AI unless a request names them.

## The assistant

Two agents share the chat box, each with its own focused prompt. A router (`router.py`, one tiny
schema-constrained call) picks one per message. Conversation state can skip the router: an answer to
the builder's own question, or "Add as chart" on an analyst answer.

- **Builder**: changes the dashboard. Described below.
- **Analyst** (`analyst.py`): answers questions about the data. It runs up to 3 read-only queries one
  step at a time, through the same safety and double-counting checks as widgets, then answers using
  only numbers from those results. Its queries run within the dashboard's filters and the person's
  row rules. A query that doesn't set dates itself gets the dashboard's period added in code, on
  the date of the table it measures (`pick_date_column`, as for charts). Each result tells the model
  which dates it covers, and "how I worked it out" shows them too. Small models used to leave the
  period out and still name it in the answer. Large numbers reach the model with their lakh/crore reading
  attached, because small models get those conversions wrong. The reply carries its evidence (the
  last result and the queries), follow-up questions, and an optional chart request. "Add as chart"
  passes that request to the builder together with the analyst's query, so the chart shows the same
  numbers as the answer.

### Builder

One schema-constrained call classifies the message (add, modify, change chart type, rename,
remove, define a column, explain, clarify) and extracts its parts. Widget work reuses the widget
engine; everything is validated against the real schema and dashboard and returned as a
**proposal**. The browser applies it through the normal API only after the user confirms. When a
new widget relies on an unexplained column, the assistant asks what it means.

## Evals

`backend/evals` holds a demo retail database with deliberately cryptic columns and 19 questions
with gold SQL. `run_eval.py` sends each through the real widget engine and compares result rows
(ignoring column order, aliases and extra columns), plus checks the chart type.

```bash
cd backend
uv run python evals/run_eval.py --model qwen/qwen3.5-9b
uv run python evals/run_eval.py --model qwen/qwen3.5-9b --no-definitions
```

Add cases to `evals/cases/retail.yaml`, or create a suite for your own database: any YAML file
with `database`, optional `definitions`, and `cases` works with `--cases`.
