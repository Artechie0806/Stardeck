"""SQL safety: only single, read-only SELECT statements may run against user data."""

import sqlglot
from sqlglot import exp


class UnsafeSQLError(ValueError):
    pass


_FORBIDDEN = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command, exp.Merge)


def ensure_read_only(sql: str, dialect: str) -> str:
    """Reject anything that is not a single SELECT statement. Returns normalised SQL."""
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except sqlglot.errors.SqlglotError as exc:
        raise UnsafeSQLError(f"Could not parse SQL: {exc}") from exc
    if len(statements) != 1:
        raise UnsafeSQLError("Exactly one statement is allowed.")
    stmt = statements[0]
    if not isinstance(stmt, exp.Query):
        raise UnsafeSQLError(f"Only SELECT queries are allowed, got {stmt.key.upper()}.")
    if any(stmt.find_all(*_FORBIDDEN)):
        raise UnsafeSQLError("Query contains a write or DDL operation.")
    return stmt.sql(dialect=dialect)

