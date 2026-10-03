"""Catch join fan-out: summing a column after joining a table that has many rows per row.

Example: orders (one) JOIN order_items (many) then SUM(orders.amt) counts each order's amount once
per item. Small models make this mistake often, so the widget engine checks every query against the
schema's foreign keys and asks the model to fix it.
"""

import sqlglot
from sqlglot import exp

from stardeck.schema import TableInfo


def _many_sides(tables: list[TableInfo]) -> dict[str, set[str]]:
    """one-side table -> tables holding a foreign key to it (the "many" sides)."""
    out: dict[str, set[str]] = {}
    for t in tables:
        for fk in t.foreign_keys:
            referred = fk.split("->", 1)[1].strip().rsplit(".", 1)[0].lower()
            if referred != t.name.lower():
                out.setdefault(referred, set()).add(t.name.lower())
    return out


def _source_table(source: exp.Expression) -> str | None:
    """The base table a FROM/JOIN source stands for, at the same grain.

    A plain table is itself. A derived table counts as its single base table when it doesn't group
    to a coarser level (no GROUP BY, or grouped by the table's own id), since its rows still map
    one-to-one onto the base table's rows.
    """
    if isinstance(source, exp.Table):
        return source.name.lower()
    if isinstance(source, exp.Subquery) and isinstance(source.this, exp.Select):
        inner = source.this
        from_ = inner.args.get("from_") or inner.args.get("from")
        if not from_ or not isinstance(from_.this, exp.Table) or inner.args.get("joins"):
            return None
        group = inner.args.get("group")
        if group and not any(isinstance(g, exp.Column) and g.name.lower() == "id" for g in group.expressions):
            return None
        return from_.this.name.lower()
    return None


def check_fanout(sql: str, dialect: str, tables: list[TableInfo]) -> str | None:
    """Return a plain explanation of the first fan-out found, or None."""
    many = _many_sides(tables)
    if not many:
        return None
    columns_of = {t.name.lower(): {c.name.lower() for c in t.columns} for t in tables}
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.SqlglotError:
        return None
    for select in tree.find_all(exp.Select):
        sources = []
        if from_ := select.args.get("from_") or select.args.get("from"):
            sources.append(from_.this)
        sources += [j.this for j in select.args.get("joins") or []]
        alias_to_table = {s.alias_or_name.lower(): base for s in sources if (base := _source_table(s))}
        joined = set(alias_to_table.values())
        if len(joined) < 2:
            continue
        for agg in select.find_all(exp.Sum, exp.Avg):
            if agg.find_ancestor(exp.Select) is not select:
                continue  # belongs to a nested query, checked on its own
            for col in agg.find_all(exp.Column):
                if col.table:
                    table = alias_to_table.get(col.table.lower())
                else:  # unqualified: resolve by unique column name among joined tables
                    owners = [t for t in joined if col.name.lower() in columns_of.get(t, set())]
                    table = owners[0] if len(owners) == 1 else None
                if not table:
                    continue
                culprits = sorted(many.get(table, set()) & joined)
                if culprits:
                    fn = "SUM" if isinstance(agg, exp.Sum) else "AVG"
                    return (
                        f"{fn}({table}.{col.name}) is computed after joining {culprits[0]}, which has several "
                        f"rows per {table} row, so each {table} value is counted more than once. Use "
                        f"{culprits[0]}'s own columns for the measure, or aggregate {table} in a subquery "
                        "before joining."
                    )
    return None


def measured_tables(sql: str, dialect: str) -> list[str]:
    """Base tables whose columns are aggregated (COUNT(*) counts the FROM table), outermost first."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.SqlglotError:
        return []
    found: list[str] = []
    for select in tree.find_all(exp.Select):
        sources = []
        if from_ := select.args.get("from_") or select.args.get("from"):
            sources.append(from_.this)
        sources += [j.this for j in select.args.get("joins") or []]
        aliases = {s.alias_or_name.lower(): base for s in sources if (base := _source_table(s))}
        for agg in select.find_all(exp.AggFunc):
            if agg.find_ancestor(exp.Select) is not select:
                continue
            cols = list(agg.find_all(exp.Column))
            if not cols and sources and (base := _source_table(sources[0])):
                found.append(base)  # COUNT(*)
            for col in cols:
                if col.table and (base := aliases.get(col.table.lower())):
                    found.append(base)
                elif not col.table and len(aliases) == 1:
                    found.append(next(iter(aliases.values())))
    return list(dict.fromkeys(found))
