# Changelog

## 2.5.0 — 2026-10-11

Stardeck is open source.

- **More data sources:** SQL Server and Azure SQL, Oracle, Snowflake, Google BigQuery, Amazon Redshift,
  Databricks, ClickHouse, Trino and DuckDB files, next to PostgreSQL, MySQL/MariaDB, SQLite and uploads.
  - **Drivers:** optional extras of the backend (`uv sync --extra all`). The Docker image includes them all.
  - **Everything works on them:** the AI writes each database's own SQL, and dashboard filters, security
    rules, measures and every prepared-table step compile for it.
  - **Safety:** functions that read files, URLs or other servers are refused, BigQuery queries have a cap on
    data scanned, and tokens and keys are stored encrypted like passwords. Some of these databases have no
    read-only connection mode; the source form says so.
  - **Picking a source:** the "Add a data source" dialog groups them into files, databases, and
    warehouses and query engines.
- **Fixed:** "Keep top rows" after "Sort" now keeps the top rows of the sorted table on every database.

- **Removed: the marketing website and the waitlist.**
  - **Website:** `site/` is gone, with its legal pages and contact settings.
  - **Waitlist:** the sign-up endpoint (`/api/public/waitlist`), its ntfy and Telegram pings, and `stardeck-admin waitlist` are gone. To let someone in, create a workspace with `stardeck-admin workspace create`, then send an invitation with `stardeck-admin invite`.
  - **Hosted deploys:** they run on two hosts, the app and share links (`SITE_DOMAIN`, `CONTACT_EMAIL`, `OPERATOR_NAME` and `OPERATOR_CITY` are no longer used).
  - **Settings:** remove `STARDECK_WAITLIST_NTFY_URL`, `STARDECK_TELEGRAM_BOT_TOKEN` and `STARDECK_TELEGRAM_CHAT_ID` from your environment.
  - **Existing databases:** an old `waitlist` table is left as it is, unused.
- **The public demo:** its "Join the waitlist" bar now has a "Get Stardeck" button. The button opens `STARDECK_SITE_URL` (e.g. the project's GitHub page) and is hidden when that isn't set.
- **Upgrade links:** they no longer fall back to `STARDECK_SITE_URL/#pricing`. Set `STARDECK_UPGRADE_URL` to show them.
- **Fixed:** stopping the server now closes its connections to data sources. On Windows, an open connection kept uploaded files locked, so deleting a workspace failed.
- **For contributors:** a code of conduct, a security policy, issue and pull request templates, and CI (lint, tests and build on every pull request).

## 1.8.0 — 2026-10-03

- **PDF export keeps every chart whole.**
  - **Pages:** charts used to be cut in two at page breaks. Export PDF now lays the dashboard out at A4 landscape width, in rows that each stay on one page.
  - **Tables:** they show every row.
  - **Dark mode:** the PDF prints in the light theme, even when the screen is dark. Before, text came out white on white.
  - **Ctrl+P:** it does the same on the read-only view. In the editor, it opens that view.
- **Public share links.**
  - **Making links:** editors make a link from the Share dialog. Anyone with it sees the dashboard read-only, without signing in, and can change dates, use the saved filters and click charts to filter.
  - **What's never shared:** SQL, the assistant and editing.
  - **Managing links:** links can expire, editors can turn them off, and each one counts its views.
  - **Badge:** Community links show a small "Made with Stardeck" badge.
- **Safe to open to the internet.** Public calls carry no SQL. The server traces clicks itself, uses no AI, and only shows map places it has already looked up. They are rate limited per address and capped server-wide.
- **The public demo.** `stardeck-admin demo setup` builds a demo workspace with City and Category filters and a share link. `/demo` opens it with a four-step tour, and a "Join the waitlist" bar.
- **The website (`site/`).**
  - **Pages:** a landing page with the hero video, the live demo, how it works, pricing in rupees, FAQ and a waitlist form.
  - **Legal:** privacy policy and terms, written for India's DPDP Act. Have both reviewed before charging anyone.
- **Fewer assistant messages a day,** to fit one GPU: Community 5 (was 30), Enterprise 60 (was 400). AI suggestions still count as messages; dashboard builds are unchanged (3 and 30).
- **Waitlist sign-ups from the website.** They're protected against bots, rate limited, and can ping you on ntfy or Telegram.
- **Deployment (`deploy/`).**
  - **Running it:** Caddy with automatic HTTPS for the site, the app and the share host, plus Docker Compose for the VPS.
  - **Backups:** encrypted with age and copied off the server with rclone.
  - **Runbook:** a step-by-step guide covering Tailscale and LM Studio.
- **For the operator:** `stardeck-admin share list|revoke` lists and turns off links. Deleting a workspace removes its links too.
- **Fixed:** the assistant's answers covered all dates while saying they covered the dashboard's. Asked which city brings in the most revenue "this year", it gave Mumbai's all-time ₹1.54 Cr instead of ₹94.9 L. Queries that don't set dates themselves now get the dashboard's period, and "How I worked it out" says so.
- **Fixed:** counting rows of a join could filter by the wrong date. "Completed orders by city" counted orders by the customers' signup date, because customers came first in the query. Counting now follows the table being counted (the "many" side of the join), using inferred relationships too, as CSV files have no declared ones.
- **Fixed:** the assistant's suggested follow-up questions were cut at apostrophes ("s revenue?" for "…Mumbai's revenue?").
- **Fixed:** the check that every route with an id is covered by the isolation test had silently stopped checking anything after the FastAPI upgrade. It reads the routes from the API schema now.

## 1.7.0 — 2026-10-03

Built for one AI server shared by many people, such as a single home GPU.

- **AI allowances are enforced** on hosted plans. Community gets 3 dashboard builds and 30 assistant messages a day, Enterprise 30 and 400.
  - They reset at midnight, India time.
  - AI suggestions count as messages.
  - Failures on our side don't count.
  - The assistant and New dashboard show how many are left today, and say plainly when they're used up.
- **"AI is paused."** When the AI server is off, the assistant and New dashboard say so, and everything else keeps working: dashboards, filters, sharing.
  - Stardeck stops waiting on timeouts: after one failed call it answers at once.
  - The app checks again every minute, or straight away with "Check again".
- **A queue in front of the AI.**
  - `STARDECK_LLM_PARALLEL` (default 4) sets how many requests go to the AI server at once.
  - One slot is always kept for the assistant, so dashboard builds can't crowd out chat.
  - When it's too busy, people get "The AI is busy. Try again in a minute." instead of a long hang.
- **Dashboard builds:**
  - Closing the page stops the build: unstarted widgets are skipped, and nothing is saved.
  - At most two builds run at once, one per workspace.
  - Long builds send keep-alive pings so proxies don't cut them off.
- **Chart labels** use the AI only while it's free and within their own allowance, and are shortened without it otherwise.

## 1.6.0 — 2026-10-03

- **Community and Enterprise plans** for hosted workspaces.
  - **Community:**
    - one data source and two dashboards (the sample data doesn't count);
    - one data source per dashboard;
    - uploads up to 25 MB;
    - every chart type, filters, the assistant and the analyst.
  - **Enterprise adds:** people and data access rules, measures, editing table links, data preparation, several data sources per dashboard, and bigger uploads.
  - **Self-hosted:** stays unlimited.
- **The server enforces each limit,** even when two requests race for the last slot, or a dashboard finishes generating after another one took the last slot.
- **The app locks things up front** with a short note and an upgrade link instead of failing halfway: People, Measures, table links, Prepared tables, the second-source checkbox, "Add data source" and New dashboard.
  - Settings shows the plan and how much of it is in use.
  - The add-data-source dialog no longer offers server file paths when hosted.
- **Downgrading never breaks dashboards.** Measures, prepared tables and multi-source dashboards that already exist keep working, read-only.
- **For the operator:**
  - `stardeck-admin workspace limits` grants exceptions (e.g. three data sources, or measures for a trial);
  - `STARDECK_UPGRADE_URL` sets where upgrade links go.

## 1.5.0 — 2026-10-03

Groundwork for Stardeck Cloud: one server, many private workspaces. Self-hosted installs keep
working as before; everything hosted sits behind `STARDECK_HOSTED`.

- **Workspaces.** Data sources, dashboards, people and settings belong to a workspace, and every
  database query is limited to the signed-in person's workspace automatically. That covers counts,
  subqueries, unions and the rest, and it fails loudly instead of leaking. Existing installs move
  into a "default" workspace on first start, after a backup of the database (`data/backups/`).
  A test calls every route that takes an id as one workspace with another's ids.
- **Hosted sign-in.** Accounts come from invitation links; people sign in with their email. New:
  password-reset links, "Change password" from the account card, sign-ins that survive restarts
  (stored hashed in the database), and limits on repeated wrong passwords. A suspended workspace
  can't sign in.
- **Operator CLI** (`stardeck-admin`):
  - the waitlist: import a form export, approve (creates the workspace and prints an invitation email), reject;
  - workspaces: create, list, change plan, suspend, delete;
  - invitations and reset links, signing someone out everywhere, and backups.
  See `docs/hosting.md`.
- **Safe to host.**
  - No file paths on the server as data sources: uploads only, and file sources can only be renamed.
  - Deleting an upload never touches files outside the uploads folder. Self-hosted gets this too: before, a source could be pointed at another file and deleting it removed that file.
  - Hosted database connections must go to the public internet. Private networks, cloud metadata and Tailscale addresses are refused, and each connection is pinned to the checked address.
  - Cross-site requests are refused, and the app's cookie is Secure.
  - AI errors no longer reveal the AI server's address.
- **Steadier with a remote AI server.**
  - An AI server that's off now fails in seconds instead of holding requests for minutes, and the AI status is cached.
  - Closing the page during dashboard generation stops the remaining work instead of blocking the server.
  - Uploads are size-checked as they arrive and no longer stall other requests.
  - Map place lookups no longer queue behind each other.
  - Old database connections are closed after a while.
- Maps: places still waiting for a lookup are retried instead of being shown as "not found".

## 1.4.0 — 2026-10-03

- **Filters (slicers).** Editors add a filter for any column with "+ Filter" and pick values from a
  searchable list (code values show their documented meanings). Filters follow the links between tables:
  filtering customers to Pune also filters their orders and order items. A chart a filter can't reach
  says "Not filtered by City" instead of quietly ignoring it. Changing a filter only changes your view;
  "Make default" saves the dates and selections for everyone.
- **Cross-filtering.** Click a bar, slice, point or tile to filter the whole dashboard by it. Clicks on
  computed categories (a month, a code turned into a label) are traced back to the underlying column.
  The clicked chart keeps every bar and highlights the one picked; click it again, or the chip, to clear.
- **Financial year, quarters and custom dates.** New ranges: this/last quarter, this/last financial
  year (labelled, e.g. FY 2026-27), last 90 days, and a custom from–to range. The financial year starts
  in April by default (change it in Settings); quarters follow it, and the assistant and analyst use it too.
- **People, roles and row-level security.** Turn on sign-in in Settings by creating an admin account,
  then add people on the new People page as viewers (open dashboards, use filters, ask the analyst),
  editors (also build and change things) or admins (also connect data, manage people and settings).
  Viewers can be limited to rows, e.g. "customers.city is Pune". Rules apply to everything they see
  (dashboards, filters, the analyst) and follow table links; the rule editor shows which tables a rule
  limits and which stay fully visible. Rules are enforced by rewriting the SQL syntax tree, and queries
  that try to get around them (other schemas, look-alike CTEs, table functions) are refused. The
  shared password (`STARDECK_PASSWORD`) still works until the first account is created.
- **Measures.** Define a calculation once, e.g. Revenue = `SUM(CASE WHEN sts = 1 THEN amt END) / 100.0`,
  on the Definitions page. Check it against your data before saving, or let the AI suggest measures from
  your glossary. Charts, the assistant and the analyst use `MEASURE(revenue)`, so every number agrees,
  and a chart of a measure shows it the measure's way (money, percent, lower is better). The retail demo
  ships with four.
- **How tables connect.** The Definitions page lists table links: from the database, detected from names
  like `customer_id`, or added by you. Turn off a wrong one or add a missing one.
- **Several data sources on one dashboard.** Pick more than one source when generating a dashboard (each
  chart uses one, picked per chart), or add sources from the dashboard's Data menu. Charts show their
  source when there are several, filters only reach charts from their own source, and the assistant asks
  which data to use when it isn't clear.
- **Faster dashboards on big databases.** Charts appear one by one as their queries finish instead of
  waiting for the slowest, and earlier results stay up (dimmed) while new ones load. Each source runs at
  most 4 queries at once (`STARDECK_MAX_QUERIES_PER_SOURCE`) so a dashboard can't swamp a database, and
  filters run inside the database.
- Viewers only see what they can use: no Data sources, New dashboard, layout editing or chart menus.
  The phone header shrinks to an avatar and a sign-out icon.
- After adding a person or setting a new password, Stardeck shows the sign-in details to send them
  (address, username, password, with a copy button), and the People list says who signs in as what.
  The sign-in page can show the password, and explains a failed sign-in (username, not full name;
  a password the browser filled in).
- Menus and confirmations opened inside a dialog (like removing a person) appear above it and can be clicked.
- A wider sidebar with roomier links. Its footer puts your account and sign-out in one card, with a labelled theme switch below.
- The sidebar shows "AI connected" (or "AI not connected" / "AI model not loaded") instead of the model's name; admins see the model and server on hover.

## 1.3.0 — 2026-10-03

- **Pivot column** (rows → columns): one new column per value, filled with sum/average/count/min/max
  of another column. Values are picked in the form from the column's real values, or all of them are
  filled in automatically (up to 30) and saved with the recipe, so the columns stay fixed. Columns are
  named after documented code meanings (sgmt 1 → `retail`).
- **Split column** at each delimiter into N columns, or at the first delimiter into two; missing parts
  are blank. Native SPLIT_PART on Postgres; a linear multi-pass chain on SQLite and MySQL.
- **Conditional column** built from a form: "if [column] [operator] [value] then [result]" rules in
  order, results can be a fixed value or another column, plus an "otherwise".
- **New logo** (the "cut one colour" mark from Stardeck Logo Directions): in the sidebar, sign-in
  screen, phone header and shared views, with the 1.5s intro animation (skipped for reduced motion).
  New favicon (adapts to dark mode), home-screen icons and a web app manifest so Stardeck can be
  added to a phone's home screen, plus light/dark logos in the README. Source files in `frontend/public/brand/`.
- **New identity: flare pink on black.** Stardeck's signature is a hot coral-pink (`#FF3D7F`), chosen because
  no analytics or BI tool uses pink. Buttons are pink with black text. Dark mode is true black, not grey,
  and light mode is clean white. Glass panels float over soft pink glows, and the font is Inter throughout.
  The logo, favicon, app icons and README logos are all recoloured. Chart colours are unchanged and still pass
  every check on the new surfaces.
- **Add data source** is now a guided dialog (a bottom sheet on phones): pick a type (spreadsheets first),
  then only the fields that type needs. Paste a connection URL to fill the form, show/hide password,
  and advanced settings tucked away.
- **Phones**: a bottom tab bar, a slim header, a single "⋯" menu for dashboard actions, a sideways-
  scrolling date filter, and KPIs two to a row sized to their content. Controls are at least 40px tall.
- Fixes from a full UI audit:
  - Widget and column menus are no longer cut off by their cards, see-through, or hidden behind the tab bar.
  - A 1366px laptop with the assistant open keeps the full dashboard grid.
  - Long dashboard titles are no longer cut off by the toolbar.
  - Removing a widget can be undone for a few seconds.
  - The assistant keeps the conversation when it's closed, and suggests prompts from your own dashboard.
  - Broken charts explain themselves and can be handed to the assistant.
  - IDs are shown without thousands separators.
  - Delete buttons are no longer nested inside links.
  - Pages show placeholders while they load.
  - Charts now switch between light and dark instantly. Before, they kept the old theme's colours until a reload.
  - Picking a date range on a dashboard only changes your view. "Make default" saves it for everyone.
  - Definitions: tables collapse, open first when they need attention, and list unclear columns first.
    A "Find a column" search covers every table.
  - A column a prepared table passes through is flagged on its original table only, so it isn't counted twice.
  - Deleting a step in the transform editor can be undone.
- **Maps**: when a map shows an amount per place, circle *area* is proportional to the amount, the largest
  circle scales with the map size and number of points, and a size legend explains the scale. Large circles are
  drawn under small ones. Maps of just places, or where every amount is the same, use small dots.
- **Assistant redesign**:
  - The assistant is now a frosted-glass panel that slides over the dashboard on every screen size; the dashboard keeps its full width, and on phones the panel goes full screen.
  - It opens from a pink "Ask Stardeck" button. Escape closes it, focus returns to whatever opened it, and the conversation is kept.
  - New empty state with starter suggestions taken from your dashboard, labelled answers, a "thinking" indicator, and "Try again" on errors.
  - Proposal cards are clearer and show a properly sized chart preview. Pie legends no longer overlap the chart.
  - After "Add to dashboard", the new widget scrolls into view, highlighted, with no reload.
  - Fixed: on phones the assistant could grow wider than the screen, pushing its close button off screen. A newly added chart then stayed hidden behind the panel until a reload.
- **Data analyst agent**: ask the assistant questions about your data ("which city brought in the most
  revenue this year?", "why did revenue grow in 2026?") and get a plain-English answer with exact figures.
  It runs read-only queries, and only uses numbers that came back from them. Every answer has "Show the
  numbers" and "How I worked it out", follow-up questions, and "Add as chart", which builds a widget
  from the same query. A small router call picks the dashboard builder or the analyst for each message,
  so each keeps its own focused prompt.
- Fixed: text the database couldn't even tokenize (e.g. prose in place of SQL) crashed the request
  instead of being rejected.
- **Sample data**: cities in the Retail demo now differ a lot in size, like real markets. Revenue runs from about
  ₹1.5Cr (Mumbai) down to about ₹2L (Chennai), so maps and city rankings show real contrast. The Chart gallery
  shows the bubble map next to a dots-only map.
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
