"""Small sqlglot helpers shared by the query runner, row filters and measures."""

from functools import cache
from typing import Any

from sqlglot import exp


def direct_tables(select: exp.Select) -> list[exp.Table]:
    """Tables in this SELECT's own FROM/JOINs, not inside nested subqueries."""
    sources = []
    # sqlglot renamed "from" to "from_" in newer releases.
    if from_ := select.args.get("from_") or select.args.get("from"):
        sources.append(from_.this)
    sources += [j.this for j in select.args.get("joins") or []]
    return [s for s in sources if isinstance(s, exp.Table)]


def literal(value: Any) -> exp.Expression:
    """A SQL literal for a JSON value (filter values come from query results, so they're typed)."""
    if value is None:
        return exp.Null()
    if isinstance(value, bool):
        return exp.Boolean(this=value)
    if isinstance(value, int | float):
        return exp.Literal.number(value)
    return exp.Literal.string(str(value))


def unqualify(expr: exp.Expression) -> exp.Expression:
    """Drop table qualifiers (o.city -> city), for conditions evaluated inside `SELECT * FROM table`."""
    expr = expr.copy()
    for col in expr.find_all(exp.Column):
        col.set("table", None)
        col.set("db", None)
        col.set("catalog", None)
    return expr


def is_cte_reference(table: exp.Table) -> bool:
    """Whether this table reference names a CTE that is in scope, rather than a real table.

    Scoping matters for security: in `WITH customers AS (SELECT * FROM customers)` the inner
    `customers` is the real table (a CTE can't see itself unless it's RECURSIVE), so it must still
    be filtered even though a CTE with the same name exists.
    """
    if table.db or table.catalog:
        return False
    name = table.name.lower()
    node: exp.Expression = table
    while node.parent is not None:
        parent = node.parent
        with_ = parent.args.get("with_") or parent.args.get("with")
        if isinstance(with_, exp.With):
            ctes = list(with_.expressions)
            if node is with_:
                # Inside one of this WITH's own CTE definitions: only earlier CTEs (and itself, if
                # RECURSIVE) are visible. Identity checks: sqlglot's == compares structure.
                idx = next(i for i, c in enumerate(ctes) if any(n is table for n in c.walk()))
                visible = ctes[: idx + (1 if with_.args.get("recursive") else 0)]
            else:  # in the query body: every CTE of this WITH is visible
                visible = ctes
            if any(c.alias_or_name.lower() == name for c in visible):
                return True
        node = parent
    return False


# How the AI is told which SQL to write, with the habits other dialects most often get wrong.
_DIALECT_NAMES = {
    "postgres": "PostgreSQL",
    "mysql": "MySQL",
    "sqlite": "SQLite",
    "duckdb": "DuckDB",
    "redshift": "Amazon Redshift",
    "tsql": "SQL Server T-SQL (TOP n instead of LIMIT; FORMAT(d, 'yyyy-MM') for year-month)",
    "oracle": "Oracle (FETCH FIRST n ROWS ONLY instead of LIMIT; TO_CHAR(d, 'YYYY-MM') for year-month)",
    "clickhouse": "ClickHouse (formatDateTime(d, '%Y-%m') for year-month)",
    "trino": "Trino (date_format(d, '%Y-%m') for year-month)",
    "snowflake": "Snowflake (TO_CHAR(d, 'YYYY-MM') for year-month)",
    "bigquery": "BigQuery standard SQL (FORMAT_DATE('%Y-%m', d) for year-month)",
    "databricks": "Databricks Spark SQL (date_format(d, 'yyyy-MM') for year-month)",
}


def dialect_name(dialect: str) -> str:
    return _DIALECT_NAMES.get(dialect, dialect)


# Unquoted names are stored in uppercase here, and SQLAlchemy reports them in lowercase, except
# names it would have to quote (reserved words like SIZE), which it reports as stored.
_UPPERCASE_DIALECTS = {"oracle", "snowflake"}


@cache
def _preparer(dialect: str):
    from sqlalchemy.dialects import oracle

    return oracle.dialect().identifier_preparer  # Snowflake's reserved words are close enough


def ident(name: str, dialect: str) -> exp.Identifier:
    """A quoted identifier for a table or column name as Stardeck knows it (from SQLAlchemy)."""
    if dialect in _UPPERCASE_DIALECTS and name == name.lower() and not _preparer(dialect)._requires_quotes(name):
        name = name.upper()
    return exp.to_identifier(name, quoted=True)


def quote(name: str, dialect: str) -> str:
    return ident(name, dialect).sql(dialect=dialect)
