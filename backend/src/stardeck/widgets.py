"""The widget engine: a plain-English request -> validated SQL, chart spec and summary.

Small local models are reliable when each step is narrow, so:
  1. pick the relevant tables (lexical retrieval + foreign-key neighbours),
  2. ask the model for SQL plus a *proposed* chart, constrained to a JSON schema,
  3. check and run the SQL read-only, feeding any error back for a retry,
  4. fix the chart deterministically against the real result shape,
  5. validate the date column the dashboard filter will use.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Literal

import sqlglot
from pydantic import BaseModel, Field
from sqlglot import exp

from stardeck.charts import CHART_GUIDE, ChartType, Stack, derive_roles, finalize
from stardeck.context import Relationship, SourceContext
from stardeck.definitions import Definitions, Measure
from stardeck.grain import check_fanout, many_sides, measured_tables
from stardeck.llm import LLMClient, LLMError
from stardeck.measures import expand_sql, fiscal_note, measure_for_column
from stardeck.query import QueryError, QueryResult, UnsafeSQLError, reads_table, run
from stardeck.schema import TableInfo, render_schema
from stardeck.sqlutil import dialect_name

ValueFormat = Literal["number", "currency", "percent"]

SCHEMA_BUDGET_CHARS = 12_000
_TEMPORAL = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?([ T].*)?$|^\d{4}-?(Q|W)\d{1,2}$")
_DATE_TYPE = re.compile(r"date|time", re.IGNORECASE)


class WidgetDraft(BaseModel):
    """What the model returns. Field order matters: reasoning first improves the SQL."""

    reasoning: str = Field(description="1-3 sentences: tables, joins, grain, filters needed.")
    sql: str
    title: str = Field(description="Short widget title, at most 6 words, no numbers.")
    summary: str = Field(description=(
        "One short sentence for a non-technical reader saying what the widget shows. No numbers, and never "
        "mention SQL, queries, tables, joins, codes or units conversion. Good: 'Revenue from completed "
        "orders each month.' Bad: 'This query sums orders.amt / 100 grouped by month.'"))
    chart_type: ChartType
    x: str | None = Field(None, description="Result column for categories or time. Null for kpi.")
    y: str | None = Field(None, description="Result column holding the measure.")
    series: str | None = Field(None, description="Result column that splits the data into several series.")
    measures: list[str] = Field(default_factory=list,
                                description="Several number columns to plot side by side (wide format).")
    stack: Stack = Field("none", description="bar/hbar/area with several series: none, stacked or percent.")
    value_format: ValueFormat = "number"
    currency: str | None = Field(None, description="ISO code like INR or USD when value_format is currency.")
    up_is_good: bool = Field(True, description="False when a higher value is bad (refunds, churn, delays).")
    date_column: str | None = Field(
        None, description="table.column of the main date the dashboard date filter should apply to, or null.")


@dataclass
class WidgetResult:
    title: str
    summary: str
    sql: str
    spec: dict
    date_filter: tuple[str, str] | None
    result: QueryResult
    attempts: int
    notes: list[str] = field(default_factory=list)


class WidgetError(RuntimeError):
    """The engine could not produce a working widget; message is safe to show to users."""

    def __init__(self, message: str, sql: str | None = None):
        super().__init__(message)
        self.sql = sql


PROMPT = """You are a data analyst building one dashboard widget for a non-technical user.
Write ONE read-only {dialect} SELECT query for the request, and describe how to chart it.

SQL rules:
- Use only tables and columns from the schema. Follow column descriptions, units, code values and the
  business glossary exactly (e.g. convert units the way the glossary says).
- Grouping by a coded column? Return readable labels with CASE, using the documented code values.
- Mind the grain. If a join creates several rows per row of another table (e.g. order lines per order),
  never SUM/COUNT/AVG columns of the "one" side after that join: aggregate that side first in a
  subquery, or use the "many" side's own columns.
- Do NOT add date-range filters unless the request names a specific period. The dashboard applies
  its date filter automatically. {fiscal}
- If a MEASURE in the schema fits the request, use MEASURE(name) instead of writing the calculation.
- Time series: one row per period with a sortable label (e.g. 'YYYY-MM'), ordered by period.
- Rankings: ORDER BY the measure DESC and LIMIT to the N asked for (default 10). No LIMIT otherwise.
- A single headline number: return exactly one row and one column.
- Return only the columns the chosen chart needs (see the chart types below), nothing extra.
- Give computed columns short snake_case aliases. Put the category/time column first, measure second.

{chart_guide}

SCHEMA:
{schema}"""


# ---------- table retrieval ----------

def usable_tables(tables: list[TableInfo], request: str) -> list[TableInfo]:
    """Leave out prepared tables that are subsets (e.g. "2025 completed orders only") unless the
    request names them: models otherwise grab them for general questions because their columns
    are convenient, and silently answer for the subset."""
    text = request.lower()
    return [t for t in tables if not t.subset or t.name.lower() in text]

def _words(text: str) -> set[str]:
    words = set(re.findall(r"[a-z0-9]+", text.lower()))
    return words | {w[:-1] for w in words if len(w) > 3 and w.endswith("s")}


def select_tables(tables: list[TableInfo], defs: Definitions, question: str,
                  budget: int = SCHEMA_BUDGET_CHARS) -> list[TableInfo]:
    """All tables if they fit the budget; otherwise the best lexical matches plus FK neighbours."""
    usable = [t for t in tables if not t.error]
    if len(render_schema(usable, defs)) <= budget:
        return usable
    q = _words(question)
    for g in defs.glossary:  # a glossary term in the question pulls in what its definition mentions
        if _words(g.term) <= q:
            q |= _words(g.definition)

    def score(t: TableInfo) -> float:
        tdef = defs.tables.get(t.name)
        name = _words(t.name.replace("_", " "))
        s = 3.0 * len(name & q)
        if tdef:
            s += 2.0 * len(_words(" ".join([tdef.description or "", *tdef.synonyms])) & q)
        for c in t.columns:
            cdef = defs.column(t.name, c.name)
            s += 1.0 * len(_words(c.name.replace("_", " ")) & q)
            if cdef and cdef.description:
                s += 0.5 * len(_words(cdef.description) & q)
        return s

    ranked = sorted(usable, key=score, reverse=True)
    picked = [t for t in ranked[:5] if score(t) > 0] or ranked[:3]
    names = {t.name for t in picked}
    for t in usable:  # one hop of foreign keys in either direction, so joins are possible
        linked = any(fk.split("->")[1].strip().split(".")[0] in names for fk in t.foreign_keys)
        links_to = any(f"-> {t.name}." in fk for p in picked for fk in p.foreign_keys)
        if t.name not in names and (linked or links_to):
            picked.append(t)
            names.add(t.name)
    chosen: list[TableInfo] = []
    for t in picked:
        if len(render_schema([*chosen, t], defs)) > budget and chosen:
            break
        chosen.append(t)
    return chosen


# ---------- chart & date-column validation ----------

def finalize_spec(draft: WidgetDraft, result: QueryResult) -> tuple[dict, list[str]]:
    """Correct the model's chart choice so it always fits the actual data."""
    roles = derive_roles(result, draft.x, draft.y, draft.series, draft.measures)
    spec, notes = finalize(draft.chart_type, draft.stack, roles)
    spec["format"] = draft.value_format
    if draft.value_format == "currency" and draft.currency:
        spec["currency"] = draft.currency.upper()[:3]
    if spec["type"] in ("kpi", "gauge") or not draft.up_is_good:
        spec["upIsGood"] = draft.up_is_good
    if spec["type"] == "bar" and not roles.temporal_x and roles.x in roles.dims:
        xi = result.columns.index(roles.x)
        if any(len(str(r[xi])) > 14 for r in result.rows):
            spec["type"] = "hbar"  # long labels read better horizontally
    return spec, notes


_COMPARISON_REQUEST = re.compile(
    r"year[\s-]*(on|over|to)[\s-]*year|\byoy\b|\bmom\b|month[\s-]*(on|over)[\s-]*month|"
    r"\b(vs\.?|versus|compared?\s+(to|with))\s+(the\s+)?(last|previous|prior|same)\b|\bgrowth\b|"
    r"\bchange\s+(from|since|vs)\b|\bdifference\b.*\b(year|month|period)",
    re.IGNORECASE)


def compares_periods(sql: str, dialect: str, request: str = "") -> bool:
    """Whether the widget compares time periods itself (YoY, growth...).

    Such widgets need the earlier periods, so the dashboard date filter must not remove them:
    with "Last year" selected, a year-on-year difference would otherwise have nothing to
    compare against and come out empty.
    """
    if _COMPARISON_REQUEST.search(request):
        return True
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.SqlglotError:
        return False
    return any(isinstance(w.this, (exp.Lag, exp.Lead)) for w in tree.find_all(exp.Window))


_YEAR_MONTH = re.compile(r"^\d{4}-\d{2}$")
_WANTS_CHART = re.compile(r"\b(chart|graph|plot|trend|over time|breakdown|broken down|by|per|each|month by month|"
                          r"monthly|weekly|daily)\b", re.IGNORECASE)
_WANTS_NUMBER = re.compile(r"\b(total|overall|one number|single number|kpi|how many|how much)\b", re.IGNORECASE)


def check_wants_chart(result: QueryResult, request: str) -> str | None:
    """The user asked for a chart or a breakdown but the query returns a single number."""
    if len(result.rows) == 1 and _WANTS_CHART.search(request) and not _WANTS_NUMBER.search(request):
        return ("The request asks for a chart or breakdown, but this returns a single number. Break it down "
                "(for example by month, or by a category) so it can be charted.")
    return None


def check_year_columns(result: QueryResult, request: str, dialect: str) -> str | None:
    """Year-over-year columns on a year-month axis never overlap (2025 is empty for 2024 months)."""
    if not _COMPARISON_REQUEST.search(request) or len(result.columns) < 3 or not result.rows:
        return None
    if not all(isinstance(r[0], str) and _YEAR_MONTH.match(r[0]) for r in result.rows):
        return None
    filled = [{i for i, r in enumerate(result.rows) if r[c] is not None} for c in range(1, len(result.columns))]
    if len(filled) >= 2 and all(a and b and not (a & b) for a in filled for b in filled if a is not b):
        month = "strftime('%m', date)" if dialect == "sqlite" else "the month number (01-12) of the date"
        return ("Each year's column only has values in its own year, so the lines never overlap. Group by "
                f"month of year only ({month}) so each row holds every year's value for that month.")
    return None


def is_date_column(c) -> bool:
    return bool(_DATE_TYPE.search(c.type) or (
        c.samples and all(_TEMPORAL.match(v) for v in c.samples)
        and re.search(r"date|time|_at$|day", c.name, re.IGNORECASE)))


def validate_date_column(draft: WidgetDraft, tables: list[TableInfo], sql: str, dialect: str, *,
                         relationships: Iterable[Relationship] = (),
                         measures: Mapping[str, Measure] | None = None) -> tuple[str, str] | None:
    return pick_date_column(tables, sql, dialect, draft.date_column, relationships=relationships, measures=measures)


def pick_date_column(tables: list[TableInfo], sql: str, dialect: str, preferred: str | None = None, *,
                     relationships: Iterable[Relationship] = (),
                     measures: Mapping[str, Measure] | None = None) -> tuple[str, str] | None:
    """Pick the date the dashboard filter should restrict, preferring the table being measured.

    "Revenue by customer, last year" must filter orders by order date, not customers by signup
    date, so date columns of the tables inside aggregates come first, even over `preferred`
    (the model's pick, or the widget's previous date column, as "table.column"). Counting rows of a
    join counts its "many" side, which the foreign keys and `relationships` tell; MEASURE(...)
    calls are expanded with `measures` first, so the measure's own table counts.
    """
    if measures:
        try:
            sql = expand_sql(sql, dialect, dict(measures))
        except (QueryError, sqlglot.errors.SqlglotError):
            pass  # an unknown measure: decide from the query as written
    by_name = {t.name.lower(): t for t in tables}
    measured = measured_tables(sql, dialect, many_sides(tables, relationships))
    model_pick: tuple[str, str] | None = None
    if preferred and "." in preferred:
        tname, cname = preferred.rsplit(".", 1)
        model_pick = (tname.strip(), cname.strip())
    candidates: list[tuple[str, str]] = []
    if model_pick and model_pick[0].lower() in measured:
        candidates.append(model_pick)
    for name in measured:
        if t := by_name.get(name):
            candidates += [(t.name, c.name) for c in t.columns if is_date_column(c)]
    if model_pick:
        candidates.append(model_pick)
    for t in tables:
        candidates += [(t.name, c.name) for c in t.columns if is_date_column(c)]
    for tname, cname in candidates:
        t = by_name.get(tname.lower())
        col = next((c for c in t.columns if c.name.lower() == cname.lower()), None) if t else None
        if col and reads_table(sql, dialect, t.name):
            return (t.name, col.name)
    return None


_CURRENCY_UNITS = {"inr": "INR", "rupee": "INR", "rupees": "INR", "₹": "INR", "paise": "INR",
                   "usd": "USD", "$": "USD", "dollar": "USD", "dollars": "USD", "cents": "USD",
                   "eur": "EUR", "€": "EUR", "euro": "EUR", "gbp": "GBP", "£": "GBP", "jpy": "JPY"}
_QUERY_TALK = re.compile(
    r"^(this|the)\s+(query|widget|chart|sql|table|metric)\s+(calculates|computes|shows|displays|returns|"
    r"counts|lists|identifies|sums|tracks|measures)\s+", re.IGNORECASE)


def clean_summary(text: str) -> str:
    """One plain sentence: drop extra sentences and 'This query calculates...' phrasing."""
    first = re.split(r"(?<=[.!?])\s+", text.strip(), maxsplit=1)[0]
    first = _QUERY_TALK.sub("Shows ", first)
    return first[:1].upper() + first[1:] if first else first


def infer_currency(spec: dict, sql: str, dialect: str, tables: list[TableInfo], defs: Definitions,
                   model_code: str | None = None) -> dict:
    """Work out the currency from documented units (e.g. paise -> INR).

    A plain number becomes currency when the measured column's unit is a currency. A value the model
    called currency but couldn't name (no code) gets the documented currency, or stays a plain number:
    a wrong symbol ($ on rupee data) is worse than none.
    """
    unknown_code = spec.get("format") == "currency" and not model_code
    if spec["type"] in ("table", "scatter", "bubble") or (spec.get("format") != "number" and not unknown_code):
        return spec
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.SqlglotError:
        return spec
    # Only value-preserving aggregates carry the unit; COUNT(amt) is a count, not money.
    names = {c.name.lower() for agg in tree.find_all(exp.Sum, exp.Avg, exp.Min, exp.Max)
             for c in agg.find_all(exp.Column)}
    if not names:
        return {k: v for k, v in spec.items() if k != "currency"} | {"format": "number"} if unknown_code else spec
    codes = set()
    for t in tables:
        for c in t.columns:
            cdef = defs.column(t.name, c.name)
            if c.name.lower() in names and cdef and cdef.unit:
                code = _CURRENCY_UNITS.get(cdef.unit.strip().lower())
                if code:
                    codes.add(code)
    if not codes:  # no documented unit: a currency in the column name (amount_inr, price_usd) counts too
        for name in names:
            for part in re.split(r"[_\W]+", name):
                if part in _CURRENCY_UNITS and part not in ("paise", "cents", "$", "₹", "€", "£"):
                    codes.add(_CURRENCY_UNITS[part])
    if len(codes) == 1:
        return {**spec, "format": "currency", "currency": codes.pop()}
    if unknown_code:
        return {k: v for k, v in spec.items() if k != "currency"} | {"format": "number"}
    return spec


def measure_format(spec: dict, sql: str, dialect: str, measures: dict) -> dict:
    """A chart of a measure shows it the way the measure says (currency, percent, up-is-good)."""
    m = measure_for_column(sql, dialect, spec.get("y") or "", measures) if spec.get("y") else None
    if m is None and spec.get("type") in ("kpi", "gauge"):
        m = next((x for x in (measure_for_column(sql, dialect, c, measures) for c in _first_select_columns(sql, dialect)) if x), None)
    if m is None:
        return spec
    out = {k: v for k, v in spec.items() if k not in ("currency",)} | {"format": m.format}
    if m.format == "currency" and m.currency:
        out["currency"] = m.currency
    if not m.up_is_good:
        out["upIsGood"] = False
    return out


def _first_select_columns(sql: str, dialect: str) -> list[str]:
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.SqlglotError:
        return []
    select = tree if isinstance(tree, exp.Select) else next(iter(tree.find_all(exp.Select)), None)
    return [p.alias_or_name for p in select.expressions] if select else []


# ---------- the pipeline ----------

def generate_widget(llm: LLMClient, ctx: SourceContext, request: str, *, max_attempts: int = 3) -> WidgetResult:
    tables, defs, dialect = ctx.tables, ctx.defs, ctx.dialect
    chosen = select_tables(usable_tables(tables, request), defs, request)
    messages = [
        {"role": "system", "content": PROMPT.format(dialect=dialect_name(dialect), schema=render_schema(chosen, defs),
                                                    chart_guide=CHART_GUIDE, fiscal=fiscal_note(ctx.fiscal_start))},
        {"role": "user", "content": request},
    ]
    last_sql, last_error = None, "no attempt made"
    retried_empty = False
    for attempt in range(1, max_attempts + 1):
        draft = llm.structured(messages, WidgetDraft)
        last_sql = draft.sql
        feedback = None
        try:  # double counting is checked on the real aggregates, i.e. with measures expanded
            fanout = check_fanout(expand_sql(draft.sql, dialect, ctx.measures), dialect, tables)
        except (QueryError, sqlglot.errors.SqlglotError):
            fanout = None  # unknown measures etc. are reported when the query runs
        if fanout and attempt < max_attempts:
            last_error = fanout
            feedback = f"That query double counts: {fanout}\nFix it. Return the full answer again."
            messages += [
                {"role": "assistant", "content": draft.model_dump_json()},
                {"role": "user", "content": feedback},
            ]
            continue
        try:
            result = run(ctx, draft.sql)
        except (UnsafeSQLError, QueryError) as exc:
            last_error = str(exc)
            feedback = f"That query failed: {last_error}\nFix it. Return the full answer again."
        else:
            shape = check_year_columns(result, request, dialect) or check_wants_chart(result, request)
            if shape and attempt < max_attempts:
                feedback = f"Almost: {shape}\nFix it. Return the full answer again."
            elif not result.rows and not retried_empty and attempt < max_attempts:
                retried_empty = True
                feedback = ("That query returned no rows. Check filters and code values against the schema; "
                            "if no rows is genuinely correct, return the same query.")
            else:
                spec, notes = finalize_spec(draft, result)
                spec = infer_currency(spec, draft.sql, dialect, chosen, defs, draft.currency)
                spec = measure_format(spec, draft.sql, dialect, ctx.measures)
                if fanout:
                    notes.append("Warning: this query may count some values more than once.")
                if not result.rows:
                    notes.append("The query returned no rows.")
                if result.truncated:
                    notes.append(f"Showing the first {len(result.rows)} rows.")
                own_periods = compares_periods(draft.sql, dialect, request)
                if own_periods:
                    notes.append("It compares periods itself, so it always uses all dates.")
                return WidgetResult(
                    title=draft.title.strip()[:80] or "Untitled", summary=clean_summary(draft.summary),
                    sql=draft.sql.strip(), spec=spec,
                    date_filter=None if own_periods else validate_date_column(
                        draft, chosen, draft.sql, dialect, relationships=ctx.relationships, measures=ctx.measures),
                    result=result, attempts=attempt, notes=notes,
                )
        messages += [
            {"role": "assistant", "content": draft.model_dump_json()},
            {"role": "user", "content": feedback},
        ]
    raise WidgetError(f"Couldn't build a working query after {max_attempts} tries. Last error: {last_error}",
                      sql=last_sql)


# ---------- refitting a widget after its SQL was edited by hand ----------

class WidgetText(BaseModel):
    title: str = Field(description="Short widget title, at most 6 words, no numbers.")
    summary: str = Field(description=(
        "One short sentence for a non-technical reader saying what the widget shows. No numbers, never "
        "mention SQL, tables or joins."))


def _plain_title(spec: dict, result: QueryResult) -> str:
    """Fallback title from the columns, e.g. "Order count by region and outcome"."""
    def h(c: str | None) -> str:
        return (c or "").replace("_", " ").strip()
    measure = h(spec.get("y") or next(iter(spec.get("measures") or []), None) or (result.columns[-1] if result.columns else ""))
    parts = [p for p in (h(spec.get("x")), h(spec.get("series"))) if p]
    title = f"{measure} by {' and '.join(parts)}" if parts else measure
    return (title[:1].upper() + title[1:])[:80] or "Custom query"


def refit_widget(widget: dict, sql: str, result: QueryResult, tables: list[TableInfo], defs: Definitions,
                 dialect: str, llm: LLMClient | None = None, *, relationships: Iterable[Relationship] = (),
                 measures: Mapping[str, Measure] | None = None) -> dict:
    """Bring a widget's chart spec, number format, date filter, title and summary in line with new SQL.

    Keeps the chart type the user chose. When the result's columns changed, the old title and
    format (e.g. currency) no longer describe the data, so they're redone; when only rows changed
    (a filter, a LIMIT), the user's title and summary stay.
    """
    old = widget["spec"]
    old_cols = {old.get(k) for k in ("x", "y", "series", "label", "location") if old.get(k)} | set(old.get("measures") or [])
    changed = not old_cols <= set(result.columns)
    hints = {} if changed else {"x": old.get("x"), "y": old.get("y"), "series": old.get("series"),
                                "measures": old.get("measures")}
    roles = derive_roles(result, **hints)
    spec, _ = finalize(old["type"], old.get("stack", "none"), roles)
    if spec["type"] == old["type"] and old.get("stack") and "stack" not in spec and (roles.series or len(roles.measures) > 1):
        spec["stack"] = old["stack"]
    if changed:
        spec["format"] = "number"
        spec = infer_currency(spec, sql, dialect, tables, defs)
    else:
        spec |= {k: old[k] for k in ("format", "currency") if k in old}
    if "upIsGood" in old:
        spec["upIsGood"] = old["upIsGood"]

    previous = widget.get("dateFilter") or {}
    preferred = f"{previous['table']}.{previous['column']}" if previous else None
    if compares_periods(sql, dialect):
        date_filter = None  # it needs the earlier periods
    elif previous or changed:
        date_filter = pick_date_column(tables, sql, dialect, preferred, relationships=relationships,
                                       measures=measures)  # still valid? else the best fit
    else:
        date_filter = None  # the filter was off and the query's shape didn't change: keep it off

    out = {"sql": sql, "spec": spec,
           "dateFilter": {"table": date_filter[0], "column": date_filter[1]} if date_filter else None}
    if changed:
        text = None
        if llm is not None:
            sample = "\n".join(", ".join(str(v) for v in r) for r in result.rows[:5])
            try:
                text = llm.structured([
                    {"role": "system", "content": "Name and describe a dashboard widget for a non-technical user."},
                    {"role": "user", "content": f"SQL:\n{sql}\n\nColumns: {', '.join(result.columns)}\nFirst rows:\n{sample}"},
                ], WidgetText)
            except LLMError:
                text = None
        out["title"] = (text.title.strip()[:80] if text and text.title.strip() else _plain_title(spec, result))
        out["summary"] = clean_summary(text.summary) if text and text.summary.strip() else ""
    return out
