"""Stardeck's own metadata store: workspaces, data sources, dashboards, people, sign-in sessions.

Workspace isolation lives here. Data sources, dashboards and people belong to a workspace
(`WorkspaceScoped`). A session opened for a workspace adds `workspace_id = <that workspace>` to
every query and refuses to write another workspace's rows, so one organisation can never read or
change another's data, even through a forgotten filter. A query on workspace data without a
workspace fails instead of returning everything. Platform sessions (sign-in, invitations, the
operator CLI) see every workspace and must not be used to serve a workspace's own requests.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    event,
    inspect,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    ORMExecuteState,
    Session,
    mapped_column,
    sessionmaker,
    with_loader_criteria,
)

from stardeck.config import Settings

DEFAULT_WORKSPACE = "default"  # self-hosted installs keep everything here


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class WorkspaceScoped:
    """Rows that belong to one workspace (see the module docstring)."""

    # Nullable only because adding a NOT NULL column to an existing SQLite table needs a default;
    # migration 1 fills it in and new rows are always stamped (`_stamp`).
    workspace_id: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)


class WorkspaceRecord(Base):
    """An organisation's private space. Self-hosted installs have one, "default"."""

    __tablename__ = "workspaces"

    id: Mapped[str] = mapped_column(String(40), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    plan: Mapped[str] = mapped_column(String(20), default="self_hosted")  # community | enterprise | self_hosted
    status: Mapped[str] = mapped_column(String(20), default="active")  # active | suspended
    # Settings admins change in the app, e.g. {"fiscal_year_start": 4}.
    settings: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Per-workspace exceptions to the plan's limits (set by the operator).
    limits_override: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class SourceRecord(WorkspaceScoped, Base):
    __tablename__ = "sources"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    kind: Mapped[str] = mapped_column(String(20))
    # Connection settings; the password is stored encrypted under "password_enc".
    config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # stardeck.definitions.Definitions as JSON.
    definitions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Latest profiling result: list of tables with columns, samples and row counts.
    snapshot: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    profiled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    profile_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Prepared tables (Power Query-style recipes): [{name, base, steps, columns, snapshot, error}].
    transforms: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # The built-in sample data (the shared, read-only retail demo); doesn't count toward plan limits.
    sample: Mapped[bool | None] = mapped_column(Boolean, default=False, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DashboardRecord(WorkspaceScoped, Base):
    __tablename__ = "dashboards"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    # The first source (kept for older code paths); `source_ids` lists every source the dashboard uses.
    source_id: Mapped[str] = mapped_column(String(64))
    source_ids: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)
    date_preset: Mapped[str] = mapped_column(String(20), default="all")
    # The default range when date_preset is "custom": {"start": "YYYY-MM-DD", "end": ...}.
    date_range: Mapped[dict[str, str] | None] = mapped_column(JSON, nullable=True)
    # Widget definitions (no results): id, sourceId, title, summary, sql, spec, layout, dateFilter.
    widgets: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # Filters (slicers): [{id, sourceId, table, column, label, values (default selection)}].
    filters: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    sample: Mapped[bool | None] = mapped_column(Boolean, default=False, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class UserRecord(WorkspaceScoped, Base):
    """A person who can sign in. No users = sign-in off (single-user, local install).

    Usernames (emails when hosted) are unique across all workspaces: one sign-in, one workspace.
    """

    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    username: Mapped[str] = mapped_column(String(120), unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20), default="viewer")  # viewer | editor | admin
    # Row-level security for viewers: [{sourceId, table, column, values}].
    rules: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON, nullable=True)
    disabled: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ShareLinkRecord(WorkspaceScoped, Base):
    """A public, read-only link to one dashboard. The token is the whole secret: anyone holding it
    sees the dashboard's charts (never its SQL) without signing in, until it expires or is turned off.
    Kept in plain text so editors can copy it again; it opens nothing the database doesn't hold anyway."""

    __tablename__ = "share_links"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    dashboard_id: Mapped[str] = mapped_column(String(64), index=True)
    created_by: Mapped[str] = mapped_column(String(64), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    views: Mapped[int] = mapped_column(Integer, default=0)
    last_viewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SessionRecord(Base):
    """A signed-in browser. Only a hash of the cookie is stored, so a copy of the DB can't sign in."""

    __tablename__ = "sessions"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    subject: Mapped[str] = mapped_column(String(64), index=True)  # a user id, or the shared-password marker
    expires_at: Mapped[float] = mapped_column(Float)  # epoch seconds
    last_seen_at: Mapped[float] = mapped_column(Float)
    user_agent: Mapped[str] = mapped_column(String(300), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class InviteRecord(Base):
    """A single-use link to join a workspace ("invite") or set a new password ("reset")."""

    __tablename__ = "invites"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(10))  # invite | reset
    workspace_id: Mapped[str] = mapped_column(String(40), index=True)
    email: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20), default="admin")
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)  # reset: whose password
    expires_at: Mapped[float] = mapped_column(Float)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class WaitlistRecord(Base):
    """People who asked for an invitation (hosted)."""

    __tablename__ = "waitlist"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    email: Mapped[str] = mapped_column(String(200), unique=True)
    name: Mapped[str] = mapped_column(String(200), default="")
    company: Mapped[str] = mapped_column(String(200), default="")
    role: Mapped[str] = mapped_column(String(200), default="")
    data_source: Mapped[str] = mapped_column(String(200), default="")
    use_case: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="new")  # new | invited | rejected
    workspace_id: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppSettingRecord(Base):
    """Legacy install-wide settings (before workspaces); migration 1 moves them into the default
    workspace. Kept so older databases still open."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)


class GeocodeRecord(Base):
    """Cached place-name lookups (null coordinates = not found), so each name is looked up once."""

    __tablename__ = "geocode_cache"

    query: Mapped[str] = mapped_column(String(300), primary_key=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LabelRecord(Base):
    """Cached AI-shortened chart labels, keyed by "<workspace>|<max chars>|<label>"."""

    __tablename__ = "label_cache"

    key: Mapped[str] = mapped_column(String(400), primary_key=True)
    short: Mapped[str] = mapped_column(String(100))


class UsageRecord(Base):
    """How much AI a workspace used on one day (India time), per kind of use."""

    __tablename__ = "usage_daily"

    workspace_id: Mapped[str] = mapped_column(String(40), primary_key=True)
    day: Mapped[str] = mapped_column(String(10), primary_key=True)  # YYYY-MM-DD, IST
    kind: Mapped[str] = mapped_column(String(20), primary_key=True)  # generation | assistant | labels
    count: Mapped[int] = mapped_column(Integer, default=0)


class SchemaMigrationRecord(Base):
    __tablename__ = "schema_migrations"

    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    applied_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------- workspace isolation ----------

class ScopeError(RuntimeError):
    """A metadata query or write that would cross workspaces. Always a bug, never user input."""


def _scope_query(state: ORMExecuteState) -> None:
    if state.is_column_load or state.is_relationship_load:
        return  # refreshing an object this session already loaded (so already in scope)
    info = state.session.info
    if info.get("platform"):
        return
    ws = info.get("workspace_id")
    if ws is None:
        raise ScopeError("Metadata queries need a workspace session or a platform session.")
    if state.is_update or state.is_delete:
        raise ScopeError("Bulk changes in a workspace session aren't allowed; change the records instead.")
    if state.is_select:
        # Always attached, not only when the statement names a scoped model at the top: count(),
        # exists() and subqueries hide their models from `all_mappers` but are still filtered.
        state.statement = state.statement.options(
            with_loader_criteria(WorkspaceScoped, lambda cls: cls.workspace_id == ws, include_aliases=True))


def _stamp(session: Session, _flush_context, _instances) -> None:
    """New rows get the session's workspace; rows of another workspace can't be written."""
    ws = session.info.get("workspace_id")
    for obj in session.new:
        if not isinstance(obj, WorkspaceScoped):
            continue
        if obj.workspace_id is None:
            if ws is None:
                raise ScopeError("New workspace data needs a workspace.")
            obj.workspace_id = ws
        elif ws is not None and obj.workspace_id != ws:
            raise ScopeError("That record belongs to another workspace.")
    if ws is not None:
        for obj in (*session.dirty, *session.deleted):
            if isinstance(obj, WorkspaceScoped) and obj.workspace_id != ws:
                raise ScopeError("That record belongs to another workspace.")


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    # WAL lets the operator CLI write while the server reads; busy_timeout waits instead of failing.
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


class Database:
    def __init__(self, settings: Settings):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.engine: Engine = create_engine(settings.metadata_url)
        if self.engine.dialect.name == "sqlite":
            event.listen(self.engine, "connect", _sqlite_pragmas)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)
        event.listen(self._sessions, "do_orm_execute", _scope_query)
        event.listen(self._sessions, "before_flush", _stamp)
        from stardeck.migrations import backup_before_upgrade, migrate  # they use the models above

        backup_before_upgrade(self, settings)  # before anything touches the schema
        Base.metadata.create_all(self.engine)
        self._add_missing_columns()
        migrate(self, settings)

    def _add_missing_columns(self) -> None:
        """Tiny migration: create_all() adds new tables but not new columns on existing ones."""
        insp = inspect(self.engine)
        with self.engine.begin() as conn:
            for table in Base.metadata.sorted_tables:
                if not insp.has_table(table.name):
                    continue
                present = {c["name"] for c in insp.get_columns(table.name)}
                for column in table.columns:
                    if column.name not in present:
                        ddl = column.type.compile(dialect=self.engine.dialect)
                        conn.execute(text(f'ALTER TABLE "{table.name}" ADD COLUMN "{column.name}" {ddl}'))

    def new_session(self, workspace_id: str) -> Session:
        """A session that only sees and writes `workspace_id`'s data."""
        if not workspace_id:
            raise ScopeError("A workspace session needs a workspace.")
        s = self._sessions()
        s.info["workspace_id"] = workspace_id
        return s

    def platform_session(self) -> Session:
        """A session across all workspaces: sign-in, invitations, the operator CLI. Never use it to
        serve a workspace's own requests."""
        s = self._sessions()
        s.info["platform"] = True
        return s

    def session(self, workspace_id: str) -> Iterator[Session]:
        with self.new_session(workspace_id) as s:
            yield s
