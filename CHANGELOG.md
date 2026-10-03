# Changelog

## 1.3.0 — 2026-10-03

- **Pivot column** (rows → columns): one new column per value, filled with sum/average/count/min/max
  of another column. Values are picked in the form from the column's real values, or all of them are
  filled in automatically (up to 30) and saved with the recipe, so the columns stay fixed. Columns are
  named after documented code meanings (sgmt 1 → `retail`).
- **Split column** at each delimiter into N columns, or at the first delimiter into two; missing parts
  are blank. Native SPLIT_PART on Postgres; a linear multi-pass chain on SQLite and MySQL.
- **Conditional column** built from a form: "if [column] [operator] [value] then [result]" rules in
  order, results can be a fixed value or another column, plus an "otherwise".
- AI step suggestions cover all three; the user's own aggregation word ("average", "count") wins over
  the model's guess, and invented pivot values are ignored.

## 1.2.0 — 2026-10-03

- **Data transformation (prepared tables):** Power Query-style recipes per data source with 17 step
  types (choose/remove/rename columns, filter, sort, top N, remove duplicates, change type, replace
  values, fill blanks, clean text, calculated column, date parts, group and summarize, merge, append,
  unpivot). Applied-steps list with per-step preview, editing, reordering and errors pinned to the
  failing step; column-header quick actions; AI-suggested steps from plain English with a preview.
- Recipes compile to dialect-correct SQL (type-aware for strict databases like Postgres) and are
  added to queries as CTEs: no writes to the user's database, always live. Prepared tables can build
  on each other and appear everywhere a table does: dashboards, the assistant, dashboard generation,
  definitions (inherited from the source columns) and date filters.
- Prepared tables that drop rows are only offered to the AI when a request names them, so general
  questions ("revenue by year") aren't silently answered from a subset.
- Currency is also recognized from column names (`amount_inr`, `price_usd`).

## 1.1.0 — 2026-10-03

- **Chart types:** clustered, stacked and 100% stacked column/bar; multi-line and stacked area;
  waterfall (from signed changes, or "before/after" columns per step); pie; treemap; funnel; scatter;
  bubble; heatmap; matrix (pivot with heat shading and totals); radar; Sankey; histogram; box plot;
  gauge; and maps on OpenStreetMap (Leaflet) with cached place-name geocoding via Nominatim.
- Grouped chart-type menu that disables types the data can't show, with the reason.
- The engine checks every chart choice against the result shape and falls back sensibly; it plots
  comparable number columns as series, and catches year-over-year queries that would never overlap.
- The assistant understands "stacked", "100%", "bar" (horizontal) and "column" (vertical).
- Comparison widgets (year on year, growth) ignore the dashboard date filter so they always have the
  earlier period; charts with no values say so instead of drawing nothing.
- Mobile: the assistant works over plain-HTTP LAN addresses and opens as a full-screen sheet.
- Charts respect "reduce motion"; weekdays and months sort in their natural order.
- **Demo data** now spans January 2024 to September 2026 (450 customers, 4,500 orders, steady growth);
  the demo dashboard opens on "This year". Existing installs: delete `data/demo/retail.sqlite` and
  click re-scan on the Retail demo source (or reset the data folder).
- The assistant asks a short question, with tappable answers, when a request is vague.
- Evals accept other defensible readings of ambiguous questions (`also_accept`).
- **Axis labels always fit:** crowded category labels tilt (every label shown), long ones are
  shortened by the AI (validated, compacted if still too long, cached), full names stay in
  tooltips; legends wrap instead of paging.
- Editing a widget's SQL refits its chart fields, number format, date filter, title and summary.

## 1.0.0 — 2026-10-03

First release.

- **Data sources:** PostgreSQL, MySQL, SQLite, CSV/Excel upload. Read-only at the SQL and
  connection level, query timeouts, encrypted saved passwords, schema profiling.
- **Definitions:** column meanings, units, code values, hidden columns, business glossary;
  automatic flagging of cryptic columns; AI-suggested definitions for review; YAML import/export.
- **Widget engine:** plain-English request → validated SQL, chart and summary; self-correction;
  join fan-out detection; deterministic chart fitting; currency inference; date-column selection.
- **Dashboards:** AI generation with streamed progress, automatic layout, drag-and-resize editing,
  date presets with previous-period KPI deltas, cached parallel execution, SQL editing with preview.
- **Assistant:** add, modify, re-chart, rename, remove and explain widgets; define columns in chat;
  every change confirmed by the user.
- **Sharing:** read-only view links, PDF export, CSV download; optional password protection.
- **Evals:** 19-question suite over a demo database; runner for comparing models and prompts.
