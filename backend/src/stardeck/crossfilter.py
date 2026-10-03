"""Cross-filtering: turn "the user clicked Mumbai on this chart" into a filter for the whole dashboard.

The clicked category is traced back through the widget's SQL to the table it comes from: a plain
column (`c.city` -> customers.city) or an expression over one table's columns (`strftime('%Y-%m',
o.order_date)` -> orders). The filter then restricts that table, and through relationships every
widget that reads it or links to it.
"""

from typing import Any

import sqlglot
from sqlglot import exp

from stardeck.context import SourceContext
from stardeck.sqlgen import _BLOCKED_FUNCTIONS, UnsafeSQLError
from stardeck.sqlutil import direct_tables, is_cte_reference, unqualify


def trace_category(ctx: SourceContext, sql: str, column: str) -> dict | None:
    """Where result column `column` comes from: {table, column} or {table, expr}; None if it can't be traced."""
    try:
        tree = sqlglot.parse_one(sql, read=ctx.dialect)
    except sqlglot.errors.SqlglotError:
        return None
    if not isinstance(tree, exp.Select):
        return None  # UNIONs etc.
    proj = next((p for p in tree.expressions if p.alias_or_name == column), None)
    if proj is None:
        return None
    inner = proj.this if isinstance(proj, exp.Alias) else proj
    if any(isinstance(n, exp.AggFunc | exp.Query | exp.Window) for n in inner.walk()):
        return None  # a measure, not a category
    if any(isinstance(n, exp.Anonymous) and n.name.upper() == "MEASURE" for n in inner.walk()):
        return None
    cols = list(inner.find_all(exp.Column))
    if not cols:
        return None
    sources = {t.alias_or_name.lower(): t for t in direct_tables(tree)}
    known = {t.name.lower(): t for t in ctx.tables if not t.error}
    found: set[str] = set()
    for c in cols:
        if c.table:
            t = sources.get(c.table.lower())
        else:  # unqualified: the one direct table that has this column
            owners = [t for t in sources.values() if t.name.lower() in known
                      and any(x.name.lower() == c.name.lower() for x in known[t.name.lower()].columns)]
            t = owners[0] if len(owners) == 1 else None
        if t is None or is_cte_reference(t) or t.name.lower() not in known:
            return None  # from a subquery or a query-local CTE: can't map it to a table
        found.add(known[t.name.lower()].name)
    if len(found) != 1:
        return None
    table = found.pop()
    if isinstance(inner, exp.Column):
        return {"table": table, "column": inner.name}
    return {"table": table, "expr": unqualify(inner).sql(dialect=ctx.dialect)}


def check_filter(ctx: SourceContext, table: str, column: str | None, expr: str | None) -> str:
    """Validate a filter coming from the browser. Returns the table's real name; raises UnsafeSQLError.

    An expression must only use the table's own columns: no subqueries, other tables or blocked
    functions, so a filter can't be used to read rows that security rules hide.
    """
    t = next((x for x in ctx.tables if x.name.lower() == table.lower() and not x.error), None)
    if t is None:
        raise UnsafeSQLError(f"There's no table called {table}.")
    names = {c.name.lower() for c in t.columns}
    if column is not None:
        if column.lower() not in names:
            raise UnsafeSQLError(f"{t.name} has no column {column}.")
        return t.name
    if not expr:
        raise UnsafeSQLError("A filter needs a column or an expression.")
    try:
        tree = sqlglot.parse_one(expr, read=ctx.dialect)
    except sqlglot.errors.SqlglotError as exc:
        raise UnsafeSQLError("That filter isn't valid.") from exc
    for node in tree.walk():
        if isinstance(node, exp.Query | exp.Subquery | exp.Table | exp.Star | exp.AggFunc):
            raise UnsafeSQLError("That filter isn't allowed.")
        if isinstance(node, exp.Func):
            name = node.name if isinstance(node, exp.Anonymous) else node.sql_name()
            if _BLOCKED_FUNCTIONS.match(name or "") or (name or "").upper() == "MEASURE":
                raise UnsafeSQLError("That filter isn't allowed.")
        if isinstance(node, exp.Column) and node.name.lower() not in names:
            raise UnsafeSQLError(f"{t.name} has no column {node.name}.")
    return t.name


def describe_value(value: Any) -> str:
    return "blank" if value is None else str(value)
