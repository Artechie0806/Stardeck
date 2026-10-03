"""Stardeck's own metadata store (data sources, definitions; dashboards from M3)."""

from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, String, Text, create_engine, inspect, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from stardeck.config import Settings


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


class SourceRecord(Base):
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
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DashboardRecord(Base):
    __tablename__ = "dashboards"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    source_id: Mapped[str] = mapped_column(String(64))
    date_preset: Mapped[str] = mapped_column(String(20), default="all")
    # Widget definitions (no results): id, title, summary, sql, spec, layout, dateFilter.
    widgets: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class GeocodeRecord(Base):
    """Cached place-name lookups (null coordinates = not found), so each name is looked up once."""

    __tablename__ = "geocode_cache"

    query: Mapped[str] = mapped_column(String(300), primary_key=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LabelRecord(Base):
    """Cached AI-shortened chart labels, keyed by "<max chars>|<label>"."""

    __tablename__ = "label_cache"

    key: Mapped[str] = mapped_column(String(400), primary_key=True)
    short: Mapped[str] = mapped_column(String(100))


class Database:
    def __init__(self, settings: Settings):
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.engine: Engine = create_engine(settings.metadata_url)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        self._add_missing_columns()

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

    def new_session(self) -> Session:
        return self._sessions()

    def session(self) -> Iterator[Session]:
        with self._sessions() as s:
            yield s
