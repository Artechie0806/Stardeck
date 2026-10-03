"""Everything needed to query one data source on behalf of one user.

Every data query goes through `query.run(ctx, ...)`, so whatever the context carries is applied
everywhere: prepared tables, measures, and the user's row-level security rules. Building a context
needs a user (see `SourceService.context`), which makes it hard to forget the security rules.
"""

import threading
from dataclasses import dataclass, field
from typing import Any

import sqlglot
from sqlalchemy.engine import Engine
from sqlglot import exp

from stardeck.definitions import Definitions, Measure
from stardeck.schema import TableInfo
from stardeck.sqlgen import UnsafeSQLError
from stardeck.sqlutil import literal, unqualify


@dataclass(frozen=True)
class Restriction:
    """Which rows of `table` may be used: `column IN values`, or `(expr) = value` (cross-filters).

    Restrictions also reach tables that point at `table` through a relationship: restricting
    customers to Pune restricts orders to Pune customers' orders.
    """

    table: str
    column: str | None = None
    values: tuple[Any, ...] = ()
    expr: str | None = None  # SQL over the table's own columns, e.g. strftime('%Y-%m', order_date)
    value: Any = None
    key: str = ""  # the dashboard filter it came from ("" for security rules)

    def condition(self, dialect: str) -> exp.Expression:
        if self.expr is not None:
            target = unqualify(sqlglot.parse_one(self.expr, read=dialect))
            # Defence in depth (the API checks too): a condition must not read other tables.
            if any(isinstance(n, exp.Query | exp.Subquery | exp.Table) for n in target.walk()):
                raise UnsafeSQLError("A filter can't contain a query.")
            return exp.Is(this=exp.Paren(this=target), expression=exp.Null()) if self.value is None \
                else exp.EQ(this=exp.Paren(this=target), expression=literal(self.value))
        col = exp.column(self.column or "")
        present = [v for v in self.values if v is not None]
        parts: list[exp.Expression] = []
        if present:
            parts.append(exp.In(this=col.copy(), expressions=[literal(v) for v in present]))
        if len(present) < len(self.values):
            parts.append(exp.Is(this=col.copy(), expression=exp.Null()))
        if not parts:  # nothing allowed (e.g. a security rule with no values)
            return exp.EQ(this=exp.Literal.number(1), expression=exp.Literal.number(0))
        return parts[0] if len(parts) == 1 else exp.Paren(this=exp.or_(*parts))


@dataclass(frozen=True)
class Relationship:
    """`table.column` points at `ref_table.ref_column` (many orders -> one customer)."""

    table: str
    column: str
    ref_table: str
    ref_column: str
    origin: str = "database"  # database | inferred | user

    @property
    def key(self) -> str:
        return f"{self.table}.{self.column}->{self.ref_table}.{self.ref_column}"


@dataclass
class SourceContext:
    id: str
    name: str
    engine: Engine
    dialect: str
    tables: list[TableInfo]
    defs: Definitions
    prepared: dict[str, str] = field(default_factory=dict)
    relationships: list[Relationship] = field(default_factory=list)
    security: tuple[Restriction, ...] = ()  # the user's row-level security rules, always applied
    timeout_s: int = 30
    fiscal_start: int = 4  # month the financial year starts (4 = April)
    schema: str | None = None  # the database/schema this source reads; others are off limits under security
    slots: threading.Semaphore | None = None  # limits concurrent queries against this source

    @property
    def measures(self) -> dict[str, Measure]:
        return {m.name.lower(): m for m in self.defs.measures}

    @property
    def base_tables(self) -> set[str]:
        return {t.name.lower() for t in self.tables if not t.prepared and not t.error}

    @property
    def security_key(self) -> str:
        """Identifies the security rules, so cached results are never shared across different rules."""
        return repr(sorted((r.table, r.column, tuple(map(str, r.values))) for r in self.security))
