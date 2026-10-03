"""Run read-only queries for widgets: safety checks, measures, row filters, row caps, timeouts.

Every data query goes through `run(ctx, sql)`. In order it:
  1. checks the SQL is a single read-only SELECT,
  2. expands MEASURE(name) into the measure's formula,
  3. adds the prepared tables the query uses as CTEs,
  4. applies row filters: the user's security rules (always) and the dashboard's filters,
  5. caps the rows and runs it with a timeout.
Dashboard date filters are applied by editing the SQL syntax tree too; the LLM never writes them.
"""

import math
import time
from collections.abc import Sequence
from contextlib import nullcontext
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from datetime import time as dtime
from decimal import Decimal
from typing import Any

import sqlglot
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlglot import exp

from stardeck.context import Restriction, SourceContext
from stardeck.measures import expand_measures, uses_measures
from stardeck.rowfilter import apply_row_filters
from stardeck.sqlgen import QueryError, UnsafeSQLError, ensure_read_only
from stardeck.sqlutil import direct_tables
from stardeck.transforms import TransformError, with_prepared

MAX_ROWS = 1000


@dataclass
class DateRange:
    start: date
    end: date  # inclusive

    def literals(self) -> tuple[str, str]:
        """(start, end-exclusive) as ISO strings. Exclusive end keeps timestamps on the last day."""
        return self.start.isoformat(), (self.end + timedelta(days=1)).isoformat()


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool = False
    # Dashboard filters (by key) that didn't reach this query: it doesn't read the filtered table,
    # or anything linked to it.
    unaffected: list[str] = field(default_factory=list)


def _json_safe(v: Any) -> Any:
    if v is None or isinstance(v, (bool, int, str)):
        return v
    if isinstance(v, float):
        return v if math.isfinite(v) else None
    if isinstance(v, Decimal):
        return int(v) if v == v.to_integral_value() else float(v)
    if isinstance(v, datetime):
        return v.isoformat(sep=" ")
    if isinstance(v, (date, dtime)):
        return v.isoformat()
    if isinstance(v, (bytes, memoryview)):
        return "<binary>"
    return str(v)


def cap_rows(sql: str, dialect: str, max_rows: int = MAX_ROWS) -> str:
    """Add a LIMIT to the outer query if it has none, so a stray query can't return millions of rows."""
    tree = sqlglot.parse_one(sql, read=dialect)
    if isinstance(tree, exp.Query) and not tree.args.get("limit"):
        tree = tree.limit(max_rows + 1)  # one extra row tells us the result was truncated
    return tree.sql(dialect=dialect)


def reads_table(sql: str, dialect: str, table: str) -> bool:
    """Whether any SELECT in the query reads `table` directly (i.e. a date filter could apply)."""
    tree = sqlglot.parse_one(sql, read=dialect)
    return any(t.name.lower() == table.lower()
               for s in tree.find_all(exp.Select) for t in direct_tables(s))


def apply_date_filter(sql: str, dialect: str, table: str, column: str, rng: DateRange) -> str:
    """Restrict every SELECT that reads `table` to rows where `column` falls inside `rng`."""
    tree = sqlglot.parse_one(sql, read=dialect)
    start, end_excl = rng.literals()
    applied = False
    for select in list(tree.find_all(exp.Select)):
        for t in direct_tables(select):
            if t.name.lower() == table.lower():
                col = exp.column(column, table=t.alias_or_name)
                cond = exp.and_(col.copy() >= exp.Literal.string(start), col < exp.Literal.string(end_excl))
                select.where(cond, append=True, copy=False)
                applied = True
                break
    if not applied:
        raise QueryError(f"This widget doesn't read {table}, so the date filter can't be applied.")
    return tree.sql(dialect=dialect)


def prepare_sql(ctx: SourceContext, sql: str, filters: Sequence[Restriction] = ()) -> tuple[str, set[str]]:
    """The SQL that actually runs: checked, measures expanded, prepared tables added, rows filtered.

    Returns it with the keys of the dashboard filters that took effect.
    """
    checked = ensure_read_only(sql, ctx.dialect)
    tree = sqlglot.parse_one(checked, read=ctx.dialect)
    if uses_measures(tree):
        tree = expand_measures(tree, ctx.dialect, ctx.measures)
    try:
        expanded = with_prepared(tree.sql(dialect=ctx.dialect), ctx.dialect, ctx.prepared or {})
    except TransformError as exc:
        raise QueryError(str(exc)) from exc
    restrictions = [*ctx.security, *filters]
    if not restrictions:
        return expanded, set()
    tree = sqlglot.parse_one(expanded, read=ctx.dialect)
    tree, applied = apply_row_filters(
        tree, ctx.dialect, restrictions, ctx.relationships, strict=bool(ctx.security),
        known_tables={t.name.lower() for t in ctx.tables if not t.error}, prepared=set(ctx.prepared),
        schema=ctx.schema,
    )
    return tree.sql(dialect=ctx.dialect), applied


def run(ctx: SourceContext, sql: str, *, max_rows: int = MAX_ROWS,
        filters: Sequence[Restriction] = ()) -> QueryResult:
    """Validate and execute a SELECT against `ctx`'s source. Raises UnsafeSQLError or QueryError."""
    final, applied = prepare_sql(ctx, sql, filters)
    safe = cap_rows(final, ctx.dialect, max_rows)
    timeout_s = ctx.timeout_s
    with ctx.slots or nullcontext():
        try:
            with ctx.engine.connect() as conn:
                raw = conn.connection.driver_connection
                # SQLite has no statement timeout; abort long queries from a progress callback.
                sqlite = ctx.dialect == "sqlite" and hasattr(raw, "set_progress_handler")
                if sqlite:
                    deadline = time.monotonic() + timeout_s
                    raw.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
                try:
                    result = conn.execute(text(safe))
                    columns = list(result.keys())
                    rows = [[_json_safe(v) for v in r] for r in result.fetchmany(max_rows + 1)]
                finally:
                    if sqlite:
                        raw.set_progress_handler(None, 0)
        except SQLAlchemyError as exc:
            root = getattr(exc, "orig", None) or exc
            message = str(root).strip().splitlines()[0]
            if "interrupted" in message.lower():
                message = f"Query took longer than {timeout_s}s and was stopped."
            raise QueryError(message) from exc
    unaffected = sorted({f.key for f in filters if f.key} - applied)
    return QueryResult(columns=columns, rows=rows[:max_rows], truncated=len(rows) > max_rows, unaffected=unaffected)


def run_widget_query(
    ctx: SourceContext, sql: str, *, kpi: bool = False, date_filter: tuple[str, str] | None = None,
    rng: DateRange | None = None, compare: DateRange | None = None, filters: Sequence[Restriction] = (),
) -> QueryResult:
    """Run a widget's SQL with the dashboard's date range and filters. KPIs return [[value, previous]]."""
    def dated(r: DateRange | None) -> str:
        if r is None or date_filter is None:
            return sql
        return apply_date_filter(sql, ctx.dialect, date_filter[0], date_filter[1], r)

    result = run(ctx, dated(rng), filters=filters)
    if not kpi:
        return result
    value = _first_number(result)
    previous = None
    if compare is not None and date_filter is not None:
        previous = _first_number(run(ctx, dated(compare), filters=filters))
    return QueryResult(columns=["value", "previous"], rows=[[value, previous]], unaffected=result.unaffected)


def _first_number(result: QueryResult) -> float | int | None:
    for v in (result.rows[0] if result.rows else []):
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return v
    return None


__all__ = [
    "DateRange",
    "QueryError",
    "QueryResult",
    "UnsafeSQLError",
    "apply_date_filter",
    "cap_rows",
    "prepare_sql",
    "reads_table",
    "run",
    "run_widget_query",
]
