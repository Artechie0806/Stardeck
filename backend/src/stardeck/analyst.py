"""The data-analyst agent: answers questions about the data in words, backed by real queries.

It works in small steps, like an analyst at a SQL console: run a read-only query, read the result,
maybe run another, then answer. Every query goes through the same checks as widgets (read-only,
double-counting), and the answer may only use numbers that came back from a query, which the
user can inspect ("show the numbers", "how I worked it out").
"""

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Literal

import sqlglot
from pydantic import BaseModel, Field
from sqlglot import exp

from stardeck.context import Restriction, SourceContext
from stardeck.grain import check_fanout
from stardeck.llm import LLMClient
from stardeck.measures import expand_sql, fiscal_note
from stardeck.query import (
    DateRange,
    QueryError,
    QueryResult,
    UnsafeSQLError,
    apply_date_filter,
    run,
)
from stardeck.schema import render_schema
from stardeck.sqlutil import dialect_name
from stardeck.widgets import is_date_column, pick_date_column, select_tables, usable_tables

MAX_STEPS = 5  # model calls, including the final answer
SHOWN_ROWS = 30  # rows of each result the model reads
KEPT_ROWS = 100  # rows of the evidence table the user can open


class AnalystStep(BaseModel):
    """One move. Field order matters: thinking before acting improves the query."""

    thought: str = Field(description="1-2 sentences: what you know so far and what you need next.")
    action: Literal["query", "answer"]
    source: str | None = Field(None, description="query: the name of the data source to query, when there are several.")
    sql: str | None = Field(None, description="query: one read-only SELECT that gets the next fact.")
    all_dates: bool = Field(False, description="query: true only when the question asks about all dates (all time, ever).")
    answer: str | None = Field(None, description="answer: the reply for the user, in markdown.")
    chart_request: str | None = Field(
        None, description="answer: if a chart would help, a short chart request like 'Revenue by city this year'.")
    follow_ups: list[str] = Field(default_factory=list, max_length=3,
                                  description="answer: up to 3 short follow-up questions the user might ask next.")


PROMPT = """You are a careful data analyst. A non-technical user asks a question about their data; find
the facts with read-only SELECT queries ({dialect}), then answer in plain words.

How to work:
- Take one step at a time. action=query with ONE SELECT to get a fact; you'll see its result. Most
  questions need 1-2 queries; never more than 3. Then action=answer.
- Aggregate in SQL (SUM, COUNT, AVG, GROUP BY) and return few rows; LIMIT 50 at most.
- Follow column descriptions, units, code values and the business glossary exactly (e.g. convert
  money units the way the glossary says). Coded columns: filter with the documented codes.
- Mind the grain: after a join that repeats rows of one table (order lines per order), don't SUM/COUNT
  that table's columns; aggregate it first in a subquery.
- Today is {today}. The dashboard is showing {period}. When the question names no period, leave
  dates out of the query: the dashboard's dates are applied to it for you. When it names one ("last
  month", "in 2025", "this year"), filter by the date yourself; those are relative to today. For all
  dates (all time, ever), set all_dates=true. Each result says which dates it covers. {fiscal}
- Use MEASURE(name) for anything a listed measure covers, so numbers match the dashboard.{filters}

How to answer:
- Every number must come from a query result above. Never estimate or invent figures. If the data
  can't answer the question, say what's missing.
- Start with the direct answer in one sentence, key figures in **bold**. Then 0-3 bullet points, each
  adding ANOTHER FACT from the results (share of total, change vs an earlier period, the runner-up).
  No bullet that only restates the answer or describes the data; no bullets beats filler.
- Name the dates the results cover (each result says, e.g. "in 2025"), and no other period.
- Money: use the currency symbol, whole units. Large numbers in results come with their lakh/crore
  reading, e.g. "18584215 (= 1.86 crore)": copy that reading (₹1.86Cr, ₹4.2L); never convert yourself.
  Percentages: 1 decimal.
- Plain business words only: never mention SQL, queries, tables or column names.
- follow_ups: always 2-3 short questions the user could ask next about this answer.
- chart_request: for rankings, trends, splits and comparisons, the chart that shows it (e.g. 'Revenue
  by city this year'); null for a single number.

SCHEMA:
{schema}"""


@dataclass
class Analysis:
    answer: str
    queries: list[dict] = field(default_factory=list)  # {sql, rows, sourceId} for "how I worked it out"
    evidence: QueryResult | None = None  # the last successful result
    chart_request: str | None = None
    follow_ups: list[str] = field(default_factory=list)
    source_id: str | None = None  # the source the evidence came from (for "Add as chart")


def _period(rng: DateRange | None) -> str:
    return f"{rng.start.isoformat()} to {rng.end.isoformat()}" if rng else "all dates"


def _sets_dates(tree: exp.Expression, ctx: SourceContext) -> bool:
    """Whether a query works with dates itself: it filters, groups or picks by a date column."""
    dates = {c.name.lower() for t in ctx.tables if not t.error for c in t.columns if is_date_column(c)}
    return any(col.name.lower() in dates for col in tree.find_all(exp.Column))


def _dashboard_dates(sql: str, ctx: SourceContext, rng: DateRange | None, all_dates: bool) -> tuple[str, dict | None, str]:
    """The query to run, the dashboard dates applied to it (if any), and which dates its result covers.

    The model used to leave the period out and still name it in the answer, so a question about this
    year was answered with all-time totals. A query that doesn't set dates itself gets the dashboard's
    period on the date of the table it measures, the same date a chart of it would use."""
    if rng is None:
        return sql, None, "all dates"
    if all_dates:
        return sql, None, "all dates, as asked"
    try:
        tree = sqlglot.parse_one(sql, read=ctx.dialect)
        if _sets_dates(tree, ctx):
            return sql, None, "the dates the query sets"
        picked = pick_date_column(ctx.tables, sql, ctx.dialect, relationships=ctx.relationships, measures=ctx.measures)
        if picked is None:
            return sql, None, "all dates (this data has no date to filter by)"
        dated = apply_date_filter(sql, ctx.dialect, picked[0], picked[1], rng)
    except (QueryError, sqlglot.errors.SqlglotError):
        return sql, None, "all dates"  # not valid SQL: running it reports what's wrong
    dates = {"table": picked[0], "column": picked[1], "start": rng.start.isoformat(), "end": rng.end.isoformat()}
    return dated, dates, f"the dashboard's dates, {_period(rng)}"


def _cell(v: object) -> str:
    """Big numbers come with their lakh/crore reading: small models get those conversions wrong."""
    if isinstance(v, bool) or not isinstance(v, int | float):
        return "" if v is None else str(v)
    n = abs(v)
    plain = f"{v:.2f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)
    if n >= 1e7:
        return f"{plain} (= {v / 1e7:.2f} crore)"
    if n >= 1e5:
        return f"{plain} (= {v / 1e5:.2f} lakh)"
    return plain


def _as_text(result: QueryResult) -> str:
    """A compact table the model can read."""
    head = " | ".join(result.columns)
    rows = [" | ".join(_cell(v) for v in r) for r in result.rows[:SHOWN_ROWS]]
    more = len(result.rows) - SHOWN_ROWS
    tail = f"\n... {more} more rows" if more > 0 else ""
    return f"{head}\n" + "\n".join(rows) + tail if rows else f"{head}\n(no rows)"


_LEAKED = re.compile(r"\n+\s*\**follow[_ -]?ups?\**\s*:?(.*)$", re.IGNORECASE | re.DOTALL)


def _split_follow_ups(answer: str) -> tuple[str, list[str]]:
    """Models sometimes write their follow-up list into the answer; move it out."""
    m = _LEAKED.search(answer)
    if not m:
        return answer, []
    listed = m.group(1)
    # Double quotes first; single quotes only around a whole question (an apostrophe inside a word,
    # as in "Mumbai's", is not a quote).
    questions = (re.findall(r'["“]([^"“”\n]{6,}?\?)["”]', listed)
                 or re.findall(r"(?<!\w)['‘]([^\n]{6,}?\?)['’](?!\w)", listed)
                 or [q.strip(" -*•") for q in listed.splitlines() if q.strip(" -*•").endswith("?")])
    return answer[: m.start()].rstrip(), questions


def analyse(llm: LLMClient, ctxs: Sequence[SourceContext], question: str, history: list[dict],
            rng: DateRange | None, *, filters: Mapping[str, Sequence[Restriction]] | None = None,
            filter_note: str = "", today: date | None = None) -> Analysis:
    """Answer `question` from the dashboard's sources. `filters` (by source id) apply to every query."""
    filters = filters or {}
    by_name = {c.name.lower(): c for c in ctxs}
    if len(ctxs) == 1:
        c = ctxs[0]
        schema = render_schema(select_tables(usable_tables(c.tables, question), c.defs, question), c.defs)
        dialects = dialect_name(c.dialect)
    else:
        schema = "\n\n".join(
            f'SOURCE "{c.name}" ({dialect_name(c.dialect)} SQL; set "source" to this name to query it):\n'
            + render_schema(select_tables(usable_tables(c.tables, question), c.defs, question), c.defs)
            for c in ctxs)
        dialects = "each source's own SQL dialect, given with its tables"
    today = today or datetime.now(UTC).astimezone().date()
    note = (f"\n- The dashboard's filters ({filter_note}) are applied to every query you run, so results only "
            "cover that; say so when it matters. If the question needs other data, say the filters exclude it."
            if filter_note else "")
    system = PROMPT.format(dialect=dialects, today=today.isoformat(), period=_period(rng),
                           fiscal=fiscal_note(ctxs[0].fiscal_start), filters=note, schema=schema)
    messages = [{"role": "system", "content": system}]
    for h in history[-4:]:
        if h.get("role") in ("user", "assistant") and h.get("text"):
            messages.append({"role": h["role"], "content": str(h["text"])[:800]})
    messages.append({"role": "user", "content": question})

    queries: list[dict] = []
    evidence: QueryResult | None = None
    evidence_source: str | None = None
    nagged = False
    for step in range(MAX_STEPS):
        if step == MAX_STEPS - 1:
            messages.append({"role": "user", "content": "Answer now (action=answer) with the facts you have."})
        s = llm.structured(messages, AnalystStep)
        messages.append({"role": "assistant", "content": s.model_dump_json()})
        if s.action == "answer" or not (s.sql or "").strip():
            if evidence is None and not nagged and step < MAX_STEPS - 1:
                nagged = True  # numbers must come from the data: make it look before answering
                messages.append({"role": "user", "content": "You haven't looked at the data yet. Run a query first."})
                continue
            if not (s.answer or "").strip() and step < MAX_STEPS - 1:
                messages.append({"role": "user", "content": "Your answer is empty. Write it in `answer`."})
                continue
            text, leaked = _split_follow_ups((s.answer or "").strip())
            return Analysis(answer=text or "I couldn't work that out from the data.", queries=queries,
                            evidence=evidence, chart_request=(s.chart_request or "").strip() or None,
                            follow_ups=[f.strip() for f in (s.follow_ups or leaked) if f.strip()][:3],
                            source_id=evidence_source)
        ctx = ctxs[0] if len(ctxs) == 1 else by_name.get((s.source or "").strip().lower())
        if ctx is None:
            names = ", ".join(f'"{c.name}"' for c in ctxs)
            messages.append({"role": "user", "content": f"Set \"source\" to one of {names}, then run the query."})
            continue
        sql = s.sql.strip()
        try:
            fanout = check_fanout(expand_sql(sql, ctx.dialect, ctx.measures), ctx.dialect, ctx.tables)
        except (QueryError, sqlglot.errors.SqlglotError):
            fanout = None  # reported properly when it runs
        if fanout:
            messages.append({"role": "user", "content": f"That query double counts: {fanout}\nFix it."})
            continue
        dated, dates, covers = _dashboard_dates(sql, ctx, rng, s.all_dates)
        try:
            result = run(ctx, dated, max_rows=KEPT_ROWS, filters=filters.get(ctx.id, ()))
        except (UnsafeSQLError, QueryError) as exc:
            messages.append({"role": "user", "content": f"That query failed: {str(exc)[:400]}\nFix it."})
            continue
        evidence, evidence_source = result, ctx.id
        # The query as written: "Add as chart" builds from it, and the chart follows the dashboard's dates.
        queries.append({"sql": sql, "rows": len(result.rows), "sourceId": ctx.id, **({"dates": dates} if dates else {})})
        messages.append({"role": "user", "content": f"Result ({len(result.rows)} rows, covering {covers}):\n{_as_text(result)}"})
    return Analysis(answer="I couldn't finish working that out. Try asking it a simpler way.", queries=queries,
                    evidence=evidence, source_id=evidence_source)
