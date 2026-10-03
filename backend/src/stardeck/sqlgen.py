"""SQL safety: only single, read-only SELECT statements may run against user data."""

import re

import sqlglot
from sqlglot import exp


class UnsafeSQLError(ValueError):
    pass


class QueryError(RuntimeError):
    """A query failed; the message is safe to show to users (and to feed back to the model)."""


# Functions that read data or files by name, run SQL from a string, or stall the server. They'd let
# a query step around row filters and security rules, and no dashboard needs them.
_BLOCKED_FUNCTIONS = re.compile(
    r"^(query_to_xml.*|table_to_xml.*|cursor_to_xml.*|schema_to_xml.*|database_to_xml.*|dblink.*|"
    r"pg_read_file|pg_read_binary_file|pg_ls_dir|pg_stat_file|lo_import|lo_export|lo_get|lo_open|"
    r"load_file|sys_exec|sys_eval|xp_cmdshell|pg_sleep.*|sleep|benchmark|readfile|writefile|"
    r"load_extension)$",
    re.IGNORECASE,
)


_FORBIDDEN = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command, exp.Merge)


def ensure_read_only(sql: str, dialect: str) -> str:
    """Reject anything that is not a single SELECT statement. Returns normalised SQL."""
    try:
        statements = [s for s in sqlglot.parse(sql, read=dialect) if s is not None]
    except sqlglot.errors.SqlglotError as exc:
        raise UnsafeSQLError(f"Could not parse SQL: {str(exc).splitlines()[0]}") from exc
    if len(statements) != 1:
        raise UnsafeSQLError("Exactly one statement is allowed.")
    stmt = statements[0]
    if not isinstance(stmt, exp.Query):
        raise UnsafeSQLError(f"Only SELECT queries are allowed, got {stmt.key.upper()}.")
    if any(stmt.find_all(*_FORBIDDEN)):
        raise UnsafeSQLError("Query contains a write or DDL operation.")
    for fn in stmt.find_all(exp.Func):
        name = fn.name if isinstance(fn, exp.Anonymous) else fn.sql_name()
        if _BLOCKED_FUNCTIONS.match(name or ""):
            raise UnsafeSQLError(f"The function {name} isn't allowed.")
    return stmt.sql(dialect=dialect)

