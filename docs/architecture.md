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
| `connectors.py` | Build read-only SQLAlchemy engines for Postgres, MySQL, SQLite and uploads. |
| `uploads.py` | Load CSV/Excel into a managed SQLite file with inferred column types. |
| `schema.py` | Profile tables (columns, samples, row counts, foreign keys); flag cryptic columns; render schema for prompts. |
| `definitions.py` | The semantic layer: column meanings, units, code values, hidden columns, glossary. YAML import/export. |
| `sqlgen.py` | SQL safety: one statement, `SELECT` only. |
| `query.py` | Execute with row caps and timeouts; apply dashboard date filters by editing the syntax tree; KPI previous-period values. |
| `grain.py` | Detect join fan-out (summing the "one" side of a one-to-many join) using foreign keys. |
| `charts.py` | Chart catalogue: which result shapes each of the 20 chart types needs, and fallbacks. Mirrored in `frontend/src/lib/chartFit.ts`. |
| `widgets.py` | The widget engine (below). |
| `geo.py` | Place name → coordinates via Nominatim, throttled and cached. |
| `transforms.py` | Prepared tables: step models, a type-aware compiler to SQL (one CTE per step), CTE injection into queries, AI step drafts. |
| `labels.py` | AI-shortened chart labels, validated and cached. |
| `dashboards.py` | Storage, automatic layout, parallel cached execution, AI dashboard generation. |
| `chat.py` | The assistant: intent classification → validated proposals. |
| `auth.py` | Optional shared password with cookie sessions. |

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
Each request runs through the widget engine, three at a time, streaming progress to the browser
as NDJSON. Widgets that fail or return no data are skipped. `auto_layout` places them: KPI row,
trends (paired with a donut if there is one), breakdowns in pairs, tables full width.

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
