"""The data-analyst agent: answers questions about the data in words, backed by real queries.

It works in small steps, like an analyst at a SQL console: run a read-only query, read the result,
maybe run another, then answer. Every query goes through the same checks as widgets (read-only,
double-counting), and the answer may only use numbers that came back from a query, which the
user can inspect ("show the numbers", "how I worked it out").
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy.engine import Engine

from stardeck.definitions import Definitions
from stardeck.grain import check_fanout
from stardeck.llm import LLMClient
from stardeck.query import DateRange, QueryError, QueryResult, UnsafeSQLError, run
from stardeck.schema import TableInfo, render_schema
from stardeck.widgets import select_tables, usable_tables

MAX_STEPS = 5  # model calls, including the final answer
SHOWN_ROWS = 30  # rows of each result the model reads
KEPT_ROWS = 100  # rows of the evidence table the user can open


class AnalystStep(BaseModel):
    """One move. Field order matters: thinking before acting improves the query."""

    thought: str = Field(description="1-2 sentences: what you know so far and what you need next.")
    action: Literal["query", "answer"]
    sql: str | None = Field(None, description="query: one read-only SELECT that gets the next fact.")
    answer: str | None = Field(None, description="answer: the reply for the user, in markdown.")
    chart_request: str | None = Field(
        None, description="answer: if a chart would help, a short chart request like 'Revenue by city this year'.")
    follow_ups: list[str] = Field(default_factory=list, max_length=3,
                                  description="answer: up to 3 short follow-up questions the user might ask next.")


PROMPT = """You are a careful data analyst. A non-technical user asks a question about their data; find
the facts with read-only {dialect} SELECT queries, then answer in plain words.

How to work:
- Take one step at a time. action=query with ONE SELECT to get a fact; you'll see its result. Most
  questions need 1-2 queries; never more than 3. Then action=answer.
- Aggregate in SQL (SUM, COUNT, AVG, GROUP BY) and return few rows; LIMIT 50 at most.
- Follow column descriptions, units, code values and the business glossary exactly (e.g. convert
  money units the way the glossary says). Coded columns: filter with the documented codes.
- Mind the grain: after a join that repeats rows of one table (order lines per order), don't SUM/COUNT
  that table's columns; aggregate it first in a subquery.
- Today is {today}. The dashboard is showing {period}. If the question names no period, use that
  period and say so in the answer. "Last month", "this year" etc. are relative to today.

How to answer:
- Every number must come from a query result above. Never estimate or invent figures. If the data
  can't answer the question, say what's missing.
- Start with the direct answer in one sentence, key figures in **bold**. Then 0-3 bullet points, each
  adding ANOTHER FACT from the results (share of total, change vs an earlier period, the runner-up).
  No bullet that only restates the answer or describes the data; no bullets beats filler.
- Name the period you actually used (e.g. "in September 2026"), and no other period.
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
    queries: list[dict] = field(default_factory=list)  # {sql, rows} for "how I worked it out"
    evidence: QueryResult | None = None  # the last successful result
    chart_request: str | None = None
    follow_ups: list[str] = field(default_factory=list)


def _period(rng: DateRange | None) -> str:
    return f"{rng.start.isoformat()} to {rng.end.isoformat()}" if rng else "all dates"


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
    questions = re.findall(r"[\"'“]([^\"'”]{6,}\?)[\"'”]", m.group(1)) or [
        q.strip(" -*•") for q in m.group(1).splitlines() if q.strip(" -*•").endswith("?")]
    return answer[: m.start()].rstrip(), questions


def analyse(llm: LLMClient, engine: Engine, dialect: str, tables: list[TableInfo], defs: Definitions,
            question: str, history: list[dict], rng: DateRange | None, *,
            prepared: dict[str, str] | None = None, timeout_s: int = 30, today: date | None = None) -> Analysis:
    chosen = select_tables(usable_tables(tables, question), defs, question)
    today = today or datetime.now(UTC).astimezone().date()
    system = PROMPT.format(dialect=dialect, today=today.isoformat(), period=_period(rng),
                           schema=render_schema(chosen, defs))
    messages = [{"role": "system", "content": system}]
    for h in history[-4:]:
        if h.get("role") in ("user", "assistant") and h.get("text"):
            messages.append({"role": h["role"], "content": str(h["text"])[:800]})
    messages.append({"role": "user", "content": question})

    queries: list[dict] = []
    evidence: QueryResult | None = None
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
                            follow_ups=[f.strip() for f in (s.follow_ups or leaked) if f.strip()][:3])
        sql = s.sql.strip()
        if fanout := check_fanout(sql, dialect, tables):
            messages.append({"role": "user", "content": f"That query double counts: {fanout}\nFix it."})
            continue
        try:
            result = run(engine, sql, dialect, timeout_s, max_rows=KEPT_ROWS, prepared=prepared)
        except (UnsafeSQLError, QueryError) as exc:
            messages.append({"role": "user", "content": f"That query failed: {str(exc)[:400]}\nFix it."})
            continue
        evidence = result
        queries.append({"sql": sql, "rows": len(result.rows)})
        messages.append({"role": "user", "content": f"Result ({len(result.rows)} rows):\n{_as_text(result)}"})
    return Analysis(answer="I couldn't finish working that out. Try asking it a simpler way.", queries=queries,
                    evidence=evidence)
