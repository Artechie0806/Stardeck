"""Run read-only queries for widgets: safety checks, row caps, timeouts, date filters.

Dashboard filters are applied here, deterministically, by editing the SQL syntax tree;
the LLM never writes filter clauses.
"""

import math
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from datetime import time as dtime
from decimal import Decimal
from typing import Any

import sqlglot
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.exc import SQLAlchemyError
from sqlglot import exp

from stardeck.sqlgen import UnsafeSQLError, ensure_read_only
from stardeck.transforms import TransformError, with_prepared

MAX_ROWS = 1000


class QueryError(RuntimeError):
    """A query failed; the message is safe to show to users (and to feed back to the model)."""


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


def _direct_tables(select: exp.Select) -> list[exp.Table]:
    """Tables in this SELECT's own FROM/JOINs, not inside nested subqueries."""
    sources = []
    # sqlglot renamed "from" to "from_" in newer releases.
    if from_ := select.args.get("from_") or select.args.get("from"):
        sources.append(from_.this)
    sources += [j.this for j in select.args.get("joins") or []]
    return [s for s in sources if isinstance(s, exp.Table)]


def reads_table(sql: str, dialect: str, table: str) -> bool:
    """Whether any SELECT in the query reads `table` directly (i.e. a date filter could apply)."""
    tree = sqlglot.parse_one(sql, read=dialect)
    return any(t.name.lower() == table.lower()
               for s in tree.find_all(exp.Select) for t in _direct_tables(s))


def apply_date_filter(sql: str, dialect: str, table: str, column: str, rng: DateRange) -> str:
    """Restrict every SELECT that reads `table` to rows where `column` falls inside `rng`."""
    tree = sqlglot.parse_one(sql, read=dialect)
    start, end_excl = rng.literals()
    applied = False
    for select in list(tree.find_all(exp.Select)):
        for t in _direct_tables(select):
            if t.name.lower() == table.lower():
                col = exp.column(column, table=t.alias_or_name)
                cond = exp.and_(col.copy() >= exp.Literal.string(start), col < exp.Literal.string(end_excl))
                select.where(cond, append=True, copy=False)
                applied = True
                break
    if not applied:
        raise QueryError(f"This widget doesn't read {table}, so the date filter can't be applied.")
    return tree.sql(dialect=dialect)


def run(engine: Engine, sql: str, dialect: str, timeout_s: int = 30, max_rows: int = MAX_ROWS,
        prepared: dict[str, str] | None = None) -> QueryResult:
    """Validate and execute a SELECT. Raises UnsafeSQLError or QueryError.

    `prepared` maps prepared-table names to their compiled SELECTs; any the query uses are added as CTEs.
    """
    checked = ensure_read_only(sql, dialect)
    try:
        expanded = with_prepared(checked, dialect, prepared or {})
    except TransformError as exc:
        raise QueryError(str(exc)) from exc
    safe = cap_rows(expanded, dialect, max_rows)
    try:
        with engine.connect() as conn:
            raw = conn.connection.driver_connection
            # SQLite has no statement timeout; abort long queries from a progress callback.
            if dialect == "sqlite" and hasattr(raw, "set_progress_handler"):
                deadline = time.monotonic() + timeout_s
                raw.set_progress_handler(lambda: int(time.monotonic() > deadline), 10_000)
            try:
                result = conn.execute(text(safe))
                columns = list(result.keys())
                rows = [[_json_safe(v) for v in r] for r in result.fetchmany(max_rows + 1)]
            finally:
                if dialect == "sqlite" and hasattr(raw, "set_progress_handler"):
                    raw.set_progress_handler(None, 0)
    except SQLAlchemyError as exc:
        root = getattr(exc, "orig", None) or exc
        message = str(root).strip().splitlines()[0]
        if "interrupted" in message.lower():
            message = f"Query took longer than {timeout_s}s and was stopped."
        raise QueryError(message) from exc
    return QueryResult(columns=columns, rows=rows[:max_rows], truncated=len(rows) > max_rows)


def run_widget_query(
    engine: Engine, sql: str, dialect: str, *, kpi: bool = False,
    date_filter: tuple[str, str] | None = None, rng: DateRange | None = None,
    compare: DateRange | None = None, timeout_s: int = 30, prepared: dict[str, str] | None = None,
) -> QueryResult:
    """Run a widget's SQL with the dashboard's date range. KPIs return [[value, previous]]."""
    def filtered(r: DateRange | None) -> str:
        if r is None or date_filter is None:
            return sql
        return apply_date_filter(sql, dialect, date_filter[0], date_filter[1], r)

    result = run(engine, filtered(rng), dialect, timeout_s, prepared=prepared)
    if not kpi:
        return result
    value = _first_number(result)
    previous = None
    if compare is not None and date_filter is not None:
        previous = _first_number(run(engine, filtered(compare), dialect, timeout_s, prepared=prepared))
    return QueryResult(columns=["value", "previous"], rows=[[value, previous]])


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
    "reads_table",
    "run",
    "run_widget_query",
]
