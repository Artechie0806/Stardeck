"""Reusable measures: MEASURE(revenue) in any query becomes the measure's formula.

A measure is an aggregate over one table's columns, written once (Definitions page, YAML or chat)
and used by every widget, the assistant and the analyst, so "revenue" always means the same thing.
Saved widgets keep MEASURE(...) in their SQL and are expanded on every run, so editing a measure
updates every chart that uses it.
"""

import sqlglot
from sqlglot import exp

from stardeck.definitions import Measure
from stardeck.sqlgen import QueryError
from stardeck.sqlutil import direct_tables


def uses_measures(tree: exp.Expression) -> bool:
    return any(f.name.upper() == "MEASURE" for f in tree.find_all(exp.Anonymous))


def expand_measures(tree: exp.Expression, dialect: str, measures: dict[str, Measure]) -> exp.Expression:
    """Replace every MEASURE(name) in place with its formula, qualified with the table's alias."""
    for node in list(tree.find_all(exp.Anonymous)):
        if node.name.upper() != "MEASURE":
            continue
        if len(node.expressions) != 1:
            raise QueryError("MEASURE() takes one measure name, e.g. MEASURE(revenue).")
        arg = node.expressions[0]
        name = (arg.name if isinstance(arg, exp.Column | exp.Literal | exp.Identifier) else arg.sql()).strip().lower()
        m = measures.get(name)
        if m is None:
            known = ", ".join(sorted(measures)) or "none defined yet"
            raise QueryError(f"There's no measure called {name} (measures: {known}).")
        select = node.find_ancestor(exp.Select)
        alias = next((t.alias_or_name for t in direct_tables(select) if t.name.lower() == m.table.lower()), None) \
            if select else None
        if alias is None:
            raise QueryError(f"MEASURE({m.name}) adds up {m.table}, so {m.table} must be in the FROM or JOIN "
                             "of the same SELECT.")
        formula = sqlglot.parse_one(m.expression, read=dialect)
        for col in formula.find_all(exp.Column):  # the formula's columns belong to the measure's table
            col.set("table", exp.to_identifier(alias))
            col.set("db", None)
            col.set("catalog", None)
        node.replace(exp.Paren(this=formula))
    return tree


def check_measure(m: Measure, dialect: str, columns: set[str]) -> None:
    """Validate a measure's formula before saving it. Raises QueryError with a user-facing message."""
    try:
        formula = sqlglot.parse_one(m.expression, read=dialect)
    except sqlglot.errors.SqlglotError as exc:
        raise QueryError(f"The formula isn't valid SQL: {str(exc).splitlines()[0]}") from exc
    if any(isinstance(n, exp.Select | exp.Subquery) for n in formula.walk()):
        raise QueryError("A measure's formula can't contain a query; use CASE WHEN for conditions.")
    if uses_measures(formula):
        raise QueryError("A measure can't use another MEASURE().")
    if not any(isinstance(n, exp.AggFunc) for n in formula.walk()):
        raise QueryError("A measure must add something up, e.g. SUM(amount), COUNT(*) or AVG(price).")
    unknown = sorted({c.name for c in formula.find_all(exp.Column) if c.name.lower() not in columns})
    if unknown:
        raise QueryError(f"{m.table} has no column {', '.join(unknown)}.")


def expand_sql(sql: str, dialect: str, measures: dict[str, Measure]) -> str:
    """`sql` with MEASURE(...) expanded (for checks that need to see the real aggregates)."""
    tree = sqlglot.parse_one(sql, read=dialect)
    return expand_measures(tree, dialect, measures).sql(dialect=dialect) if uses_measures(tree) else sql


def measure_for_column(sql: str, dialect: str, column: str, measures: dict[str, Measure]) -> Measure | None:
    """The measure behind a result column (`MEASURE(revenue) AS revenue`), if it is a bare measure."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.SqlglotError:
        return None
    select = tree if isinstance(tree, exp.Select) else next(iter(tree.find_all(exp.Select)), None)
    for proj in select.expressions if select else []:
        if proj.alias_or_name != column:
            continue
        inner = proj.this if isinstance(proj, exp.Alias) else proj
        if isinstance(inner, exp.Anonymous) and inner.name.upper() == "MEASURE" and inner.expressions:
            return measures.get(inner.expressions[0].name.lower())
    return None


def fiscal_note(start_month: int) -> str:
    """How the business's financial year runs, for prompts."""
    if start_month == 1:
        return "The financial year is the calendar year."
    names = ["January", "February", "March", "April", "May", "June", "July", "August", "September",
             "October", "November", "December"]
    end = names[(start_month - 2) % 12]
    return (f"The financial year starts in {names[start_month - 1]}: FY 2025-26 runs from "
            f"1 {names[start_month - 1]} 2025 to the end of {end} 2026. For a financial-year label, use the "
            f"date's year when its month is {start_month} or later, else the year before.")
