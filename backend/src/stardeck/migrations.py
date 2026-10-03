"""Versioned changes to Stardeck's metadata DB, applied automatically on start.

`create_all()` adds new tables and `Database._add_missing_columns()` adds new columns; migrations
handle what those can't: filling in data, carrying settings over, adding indexes. Each runs once,
in its own transaction, and is recorded in `schema_migrations`. On SQLite a copy of the database is
saved to data_dir/backups first, so an upgrade can always be undone.
"""

import sqlite3
from collections.abc import Callable
from pathlib import Path

from sqlalchemy import Connection, insert, inspect, select, text, update

from stardeck.config import Settings
from stardeck.db import (
    DEFAULT_WORKSPACE,
    AppSettingRecord,
    DashboardRecord,
    Database,
    SchemaMigrationRecord,
    SourceRecord,
    UserRecord,
    WorkspaceRecord,
    utcnow,
)

SAMPLE_IDS = ("retail-demo", "sales-overview")  # the fixed ids of the self-hosted demo


def _m001_workspaces(conn: Connection, settings: Settings) -> None:
    """Everything that existed before workspaces moves into the "default" workspace."""
    ws = WorkspaceRecord.__table__
    if conn.execute(select(ws.c.id).where(ws.c.id == DEFAULT_WORKSPACE)).first() is None:
        fiscal = settings.fiscal_year_start
        legacy = AppSettingRecord.__table__
        stored = conn.execute(select(legacy.c.value).where(legacy.c.key == "fiscal_year_start")).scalar()
        if isinstance(stored, int) and 1 <= stored <= 12:
            fiscal = stored
        conn.execute(insert(ws).values(id=DEFAULT_WORKSPACE, name="My workspace", plan="self_hosted",
                                       status="active", settings={"fiscal_year_start": fiscal},
                                       created_at=utcnow()))
    for model in (SourceRecord, DashboardRecord, UserRecord):
        t = model.__table__
        conn.execute(update(t).where(t.c.workspace_id.is_(None)).values(workspace_id=DEFAULT_WORKSPACE))
        _ensure_index(conn, t.name, "workspace_id")
    for model in (SourceRecord, DashboardRecord):
        t = model.__table__
        conn.execute(update(t).where(t.c.sample.is_(None)).values(sample=t.c.id.in_(SAMPLE_IDS)))


def _ensure_index(conn: Connection, table: str, column: str) -> None:
    """The index create_all() makes on fresh installs (same name), for tables that predate the column."""
    name = f"ix_{table}_{column}"
    if name not in {ix["name"] for ix in inspect(conn).get_indexes(table)}:
        conn.execute(text(f'CREATE INDEX "{name}" ON "{table}" ("{column}")'))


MIGRATIONS: list[tuple[int, str, Callable[[Connection, Settings], None]]] = [
    (1, "workspaces", _m001_workspaces),
]


def pending(db: Database) -> list[tuple[int, str]]:
    if not inspect(db.engine).has_table(SchemaMigrationRecord.__tablename__):
        return [(v, name) for v, name, _ in MIGRATIONS]
    with db.engine.connect() as conn:
        done = set(conn.execute(select(SchemaMigrationRecord.__table__.c.version)).scalars())
    return [(v, name) for v, name, _ in MIGRATIONS if v not in done]


def backup_before_upgrade(db: Database, settings: Settings) -> Path | None:
    """Before anything changes the schema: copy an existing SQLite database that has data and
    pending migrations, so the upgrade can be undone."""
    todo = pending(db)
    if not todo or db.engine.dialect.name != "sqlite" or not _has_data(db):
        return None
    return backup(db, settings.data_dir / "backups", f"pre-v{todo[0][0]}")


def migrate(db: Database, settings: Settings) -> list[int]:
    """Apply pending migrations; returns their versions."""
    todo = pending(db)
    if not todo:
        return []
    fns = {v: fn for v, _, fn in MIGRATIONS}
    for version, name in todo:
        with db.engine.begin() as conn:
            fns[version](conn, settings)
            conn.execute(insert(SchemaMigrationRecord.__table__).values(version=version, name=name,
                                                                        applied_at=utcnow()))
    return [v for v, _ in todo]


def _has_data(db: Database) -> bool:
    insp = inspect(db.engine)
    with db.engine.connect() as conn:
        # Raw counts: the tables may still have their old columns.
        return any(insp.has_table(t) and conn.execute(text(f'SELECT COUNT(*) FROM "{t}"')).scalar()
                   for t in ("sources", "dashboards", "users"))


def backup(db: Database, folder: Path, label: str) -> Path | None:
    """Copy the SQLite metadata DB (consistently, while it's in use) into `folder`."""
    path = db.engine.url.database
    if db.engine.dialect.name != "sqlite" or not path:
        return None
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"stardeck-{label}-{utcnow():%Y%m%d-%H%M%S}.db"
    src, dst = sqlite3.connect(path), sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return target
