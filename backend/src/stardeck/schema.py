"""Profile a database (tables, columns, samples) and render it as compact prompt context."""

import re

from pydantic import BaseModel, Field
from sqlalchemy import Engine, column, func, inspect, select, table

from stardeck.definitions import Definitions

SAMPLE_SCAN_ROWS = 1000  # distinct samples come from the first N rows, so huge tables stay cheap
MAX_TABLES = 200


class ColumnInfo(BaseModel):
    name: str
    type: str
    samples: list[str] = Field(default_factory=list)


class TableInfo(BaseModel):
    name: str
    columns: list[ColumnInfo]
    foreign_keys: list[str] = Field(default_factory=list)
    row_count: int | None = None
    is_view: bool = False
    prepared: bool = False  # a Stardeck prepared table (recipe), not a table in the database
    # A prepared table that drops rows (filters, top-N, inner merges, grouping): only right for
    # questions about that subset, so the AI only gets it when a request names it.
    subset: bool = False
    error: str | None = None


def introspect(engine: Engine, schema: str | None = None, sample_values: int = 5) -> list[TableInfo]:
    insp = inspect(engine)
    names = [(n, False) for n in insp.get_table_names(schema=schema)]
    names += [(n, True) for n in insp.get_view_names(schema=schema)]
    tables: list[TableInfo] = []
    with engine.connect() as conn:
        for tname, is_view in sorted(names)[:MAX_TABLES]:
            try:
                cols_meta = insp.get_columns(tname, schema=schema)
                t = table(tname, *[column(c["name"]) for c in cols_meta], schema=schema)
                row_count = conn.execute(select(func.count()).select_from(t)).scalar_one()
                cols = []
                for c in cols_meta:
                    head = select(t.c[c["name"]]).limit(SAMPLE_SCAN_ROWS).subquery()
                    rows = conn.execute(
                        select(head.c[c["name"]]).distinct()
                        .where(head.c[c["name"]].is_not(None))
                        .limit(sample_values)
                    ).all()
                    cols.append(ColumnInfo(name=c["name"], type=str(c["type"]),
                                           samples=[str(r[0])[:40] for r in rows]))
                fks = [] if is_view else [
                    f"{','.join(fk['constrained_columns'])} -> {fk['referred_table']}."
                    f"{','.join(fk['referred_columns'])}"
                    for fk in insp.get_foreign_keys(tname, schema=schema)
                ]
                tables.append(TableInfo(name=tname, columns=cols, foreign_keys=fks,
                                        row_count=row_count, is_view=is_view))
            # One unreadable table (permissions, odd types) shouldn't sink the rest.
            except Exception as exc:  # noqa: BLE001
                conn.rollback()
                tables.append(TableInfo(name=tname, columns=[], is_view=is_view,
                                        error=str(exc).splitlines()[0][:300]))
    return tables


# Names that are self-explanatory enough that the AI rarely misreads them.
_COMMON = {"id", "name", "city", "qty", "price", "date", "email", "type", "code", "year", "month",
           "day", "url", "note", "notes", "title", "state", "zip", "age"}


def flag_column(name: str, has_description: bool) -> str | None:
    """Why the AI may misread this column, or None if it's probably fine."""
    if has_description:
        return None
    n = name.lower()
    if n in _COMMON or n.endswith(("_id", "_date", "_at", "_name", "_price", "_count")):
        return None
    if re.fullmatch(r"(col|column|c|field|f|var|attr)_?\d+", n):
        return "Generic name gives no hint of meaning"
    if len(n) <= 4:
        return "Short or abbreviated name"
    if any(0 < len(part) <= 2 and part not in {"id", "no"} for part in n.split("_")):
        return "Abbreviated name"
    if not re.search(r"[aeiou]", n.replace("_", "")):
        return "Abbreviated name"
    return None


def render_schema(tables: list[TableInfo], defs: Definitions | None = None) -> str:
    """Render tables for an LLM prompt, merging in user definitions and dropping hidden columns."""
    defs = defs or Definitions()
    out: list[str] = []
    if defs.database.description:
        out.append(f"Database context: {defs.database.description}\n")
    for t in tables:
        if t.error:
            continue
        tdef = defs.tables.get(t.name)
        kind = "PREPARED TABLE" if t.prepared else "VIEW" if t.is_view else "TABLE"
        header = f"{kind} {t.name}" + (f" ({t.row_count} rows)" if t.row_count is not None else "")
        if tdef and tdef.description:
            header += f" -- {tdef.description}"
        out.append(header)
        for c in t.columns:
            cdef = defs.column(t.name, c.name)
            if cdef and cdef.hidden:
                continue
            line = f"  {c.name} {c.type}"
            notes = []
            if cdef and cdef.description:
                notes.append(cdef.description)
            if cdef and cdef.unit:
                notes.append(f"unit: {cdef.unit}")
            if cdef and cdef.values:
                notes.append("values: " + ", ".join(f"{k}={v}" for k, v in cdef.values.items()))
            elif c.samples:
                notes.append("e.g. " + ", ".join(c.samples))
            if notes:
                line += " -- " + "; ".join(notes)
            out.append(line)
        for fk in t.foreign_keys:
            out.append(f"  FK {fk}")
        out.append("")
    if defs.glossary:
        out.append("Business glossary:")
        out += [f"  {g.term}: {g.definition}" for g in defs.glossary]
    return "\n".join(out).strip()
