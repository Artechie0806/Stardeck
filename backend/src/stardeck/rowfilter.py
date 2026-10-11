"""Row filters: restrict which rows a query can see, whatever SQL it is.

Used for dashboard filters (slicers, cross-filters) and row-level security. Every reference to a
restricted table, anywhere in the query (joins, subqueries, CTEs, UNIONs), is replaced by
`(SELECT * FROM table WHERE <allowed rows>) AS alias`, so the rest of the SQL is untouched.

Restrictions follow relationships from the "many" side to the "one" side, the way Power BI filters
flow: restricting customers to Pune turns every `orders` reference into
`(SELECT * FROM orders WHERE customer_id IN (SELECT id FROM customers WHERE city IN ('Pune')))`.

Strict mode (whenever security rules are in force) also refuses SQL that could step around them:
tables the source doesn't know, other schemas/databases, and CTEs that reuse a real table's name.
"""

from collections.abc import Sequence

from sqlglot import exp

from stardeck.context import Relationship, Restriction
from stardeck.sqlgen import UnsafeSQLError
from stardeck.sqlutil import is_cte_reference

MAX_DEPTH = 4  # relationship hops a restriction travels (orders -> order lines -> ...)

# Qualifiers that just mean "this database" in each dialect.
_DEFAULT_DBS = {"sqlite": {"main"}, "duckdb": {"main"}, "postgres": {"public"}, "redshift": {"public"},
                "mysql": set(), "tsql": {"dbo"}}


def apply_row_filters(
    tree: exp.Expression, dialect: str, restrictions: Sequence[Restriction],
    relationships: Sequence[Relationship], *, strict: bool = False, known_tables: set[str] | None = None,
    prepared: set[str] | None = None, schema: str | None = None,
) -> tuple[exp.Expression, set[str]]:
    """Restrict `tree` in place. Returns it with the keys of the restrictions that took effect.

    `known_tables`: the source's real tables (lowercase), for strict mode. `prepared`: prepared-table
    names, which are CTEs by now and may be restricted by name (slicers on a prepared table).
    """
    prepared = {p.lower() for p in prepared or set()}
    by_table: dict[str, list[Restriction]] = {}
    for r in restrictions:
        by_table.setdefault(r.table.lower(), []).append(r)
    parents: dict[str, list[Relationship]] = {}
    for rel in relationships:
        parents.setdefault(rel.table.lower(), []).append(rel)
    memo: dict[str, tuple[exp.Expression | None, set[str]]] = {}

    def condition(table: str, depth: int, path: frozenset[str]) -> tuple[exp.Expression | None, set[str]]:
        """Allowed rows of `table` as a WHERE condition over its own columns (None = unrestricted)."""
        if table in memo:
            return memo[table]
        parts: list[exp.Expression] = []
        keys: set[str] = set()
        for r in by_table.get(table, []):
            parts.append(r.condition(dialect))
            keys.add(r.key)
        if depth < MAX_DEPTH:
            for rel in parents.get(table, []):
                ref = rel.ref_table.lower()
                if ref in path:  # relationship loop
                    continue
                cond, ref_keys = condition(ref, depth + 1, path | {ref})
                if cond is None:
                    continue
                sub = (exp.select(exp.column(rel.ref_column))
                       .from_(exp.Table(this=exp.to_identifier(rel.ref_table)))
                       .where(cond))
                parts.append(exp.In(this=exp.column(rel.column), query=exp.Subquery(this=sub)))
                keys |= ref_keys
        result = (exp.and_(*parts) if parts else None), keys
        if depth == 0:
            memo[table] = result
        return result

    if strict:
        _check_strict(tree, dialect, known_tables or set(), prepared, schema)

    applied: set[str] = set()
    for table in list(tree.find_all(exp.Table)):
        if not isinstance(table.this, exp.Identifier):
            continue  # table-valued functions etc.
        name = table.name.lower()
        if is_cte_reference(table) and name not in prepared:
            continue  # a query-local CTE: its own FROMs are restricted where they read real tables
        cond, keys = condition(name, 0, frozenset({name}))
        if cond is None:
            continue
        inner = table.copy()
        inner.set("alias", None)
        alias = table.args.get("alias") or exp.TableAlias(this=exp.to_identifier(table.name))
        sub = exp.Subquery(this=exp.select("*").from_(inner).where(cond.copy()), alias=alias.copy())
        table.replace(sub)
        applied |= keys
    return tree, applied


def _check_strict(tree: exp.Expression, dialect: str, known: set[str], prepared: set[str], schema: str | None) -> None:
    allowed_dbs = _DEFAULT_DBS.get(dialect, set()) | ({schema.lower()} if schema else set())
    real = known - prepared
    for table in tree.find_all(exp.Table):
        if not isinstance(table.this, exp.Identifier):
            raise UnsafeSQLError("Table functions aren't allowed here.")
        if table.catalog or (table.db and table.db.lower() not in allowed_dbs):
            raise UnsafeSQLError(f"{table.sql(dialect=dialect)} is outside this data source.")
        name = table.name.lower()
        if is_cte_reference(table):
            continue
        if name not in known:
            raise UnsafeSQLError(f"There's no table called {table.name} in this data source.")
    for cte in tree.find_all(exp.CTE):
        if cte.alias_or_name.lower() in real:
            raise UnsafeSQLError(f"A WITH name can't reuse the table name {cte.alias_or_name}.")
