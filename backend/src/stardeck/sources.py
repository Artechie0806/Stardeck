"""Data sources: storage, connection, profiling, and the definitions attached to them."""

import dataclasses
import re
import threading
import time
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic.alias_generators import to_camel
from sqlalchemy import select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlglot import exp

from stardeck import connectors
from stardeck.config import Settings
from stardeck.connectors import (
    SourceConfig,
    SourceConnectionError,
    SQLiteConfig,
)
from stardeck.context import Relationship, SourceContext
from stardeck.db import DashboardRecord, SourceRecord, utcnow
from stardeck.definitions import (
    ColumnDef,
    Definitions,
    DefinitionSource,
    GlossaryTerm,
    Measure,
    RelationshipDef,
    TableDef,
)
from stardeck.demo import retail
from stardeck.ids import new_id
from stardeck.llm import LLMClient
from stardeck.measures import check_measure
from stardeck.netguard import Guard
from stardeck.principal import SYSTEM, Principal
from stardeck.query import QueryError, UnsafeSQLError, run
from stardeck.schema import ColumnInfo, TableInfo, flag_column, inspector, introspect, render_schema
from stardeck.secrets import SecretBox
from stardeck.sqlutil import dialect_name, ident, quote
from stardeck.transforms import (
    NAME_RE,
    PIVOT_MAX,
    Compiler,
    Pivot,
    Recipe,
    TransformError,
    describe,
    drops_rows,
    referenced_tables,
    steps_adapter,
)

_config_adapter: TypeAdapter[SourceConfig] = TypeAdapter(SourceConfig)
DEMO_ID = "retail-demo"


# ---------- API shapes (camelCase, matching frontend/src/api/types.ts) ----------

class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


class ColumnOut(ApiModel):
    name: str
    type: str
    samples: list[str]
    definition: dict | None
    source: DefinitionSource | None
    flag: str | None


class TableOut(ApiModel):
    name: str
    row_count: int | None
    description: str | None
    foreign_keys: list[str]
    is_view: bool
    prepared: bool = False
    error: str | None
    columns: list[ColumnOut]


class RelationshipOut(ApiModel):
    table: str
    column: str
    ref_table: str
    ref_column: str
    origin: str  # database | inferred | user
    key: str
    ignored: bool = False


class SourceOut(ApiModel):
    id: str
    name: str
    kind: str
    sample: bool = False
    detail: str
    # Connection settings without secrets, so the edit form can be prefilled.
    config: dict
    database_description: str | None
    glossary: list[GlossaryTerm]
    tables: list[TableOut]
    profiled_at: datetime | None
    profile_error: str | None
    measures: list[dict] = Field(default_factory=list)
    relationships: list[RelationshipOut] = Field(default_factory=list)


# ---------- engines ----------

class EngineRegistry:
    """One pooled engine per source, rebuilt when its config changes.

    Engines idle for a while, or beyond the most recently used `max_engines`, are disposed, so a
    server with many workspaces doesn't keep a connection pool open to every database forever.
    """

    def __init__(self, max_engines: int = 50, idle_s: float = 1800) -> None:
        self._engines: OrderedDict[str, tuple[Engine, float]] = OrderedDict()
        self._lock = Lock()
        self.max_engines, self.idle_s = max_engines, idle_s

    def get(self, source_id: str, factory) -> Engine:
        now = time.monotonic()
        with self._lock:
            hit = self._engines.pop(source_id, None)
            engine = hit[0] if hit else factory()
            self._engines[source_id] = (engine, now)
            retired = [self._engines.pop(k)[0] for k, (_, used) in list(self._engines.items())
                       if k != source_id and now - used > self.idle_s]
            while len(self._engines) > self.max_engines:
                retired.append(self._engines.popitem(last=False)[1][0])
        for e in retired:
            e.dispose()  # connections in use stay open until they're returned
        return engine

    def drop(self, source_id: str) -> None:
        with self._lock:
            hit = self._engines.pop(source_id, None)
        if hit:
            hit[0].dispose()

    def close(self) -> None:
        """Dispose every engine (on shutdown), so no connection or file stays open."""
        with self._lock:
            engines = [e for e, _ in self._engines.values()]
            self._engines.clear()
        for e in engines:
            e.dispose()


class SourceService:
    def __init__(self, settings: Settings, box: SecretBox, engines: EngineRegistry, workspaces=None):
        self.settings = settings
        self.box = box
        self.engines = engines
        self.workspaces = workspaces  # stardeck.workspaces.WorkspaceService (financial year start...)
        # Hosted: network databases must be on the public internet (see netguard).
        self.guard = Guard(settings.ssrf_deny) if settings.hosted else None
        self._slots: dict[str, threading.Semaphore] = {}
        self._slots_lock = threading.Lock()

    # ----- config <-> storage -----

    def _store_config(self, config: SourceConfig) -> dict:
        data = config.model_dump(by_alias=True)
        for field in config.secrets:  # passwords, tokens, keys: stored encrypted
            value = data.pop(field, None)
            if value:
                data[f"{field}_enc"] = self.box.encrypt(value)
        return data

    def load_config(self, rec: SourceRecord) -> SourceConfig:
        data = dict(rec.config)
        for field in connectors.SECRETS.get(rec.kind, ()):
            enc = data.pop(f"{field}_enc", None)
            if enc:
                data[field] = self.box.decrypt(enc)
        return _config_adapter.validate_python(data)

    def engine(self, rec: SourceRecord) -> Engine:
        def build() -> Engine:
            config = self.load_config(rec)
            if self.settings.hosted and isinstance(config, connectors.FileConfig):
                self.check_local_file(config.path)  # defence in depth: never another file on the server
            return connectors.build_engine(config, self.settings.query_timeout_s, self.guard)
        return self.engines.get(rec.id, build)

    def check_local_file(self, path: str) -> None:
        """Hosted: a file source can only be one of the workspace uploads or the shared sample data."""
        p = Path(path).resolve()
        if p != self.demo_path and not p.is_relative_to(self.uploads_root):
            raise SourceConnectionError("Only uploaded files can be used as data here.")

    @staticmethod
    def dialect(rec: SourceRecord) -> str:
        return connectors.DIALECTS[rec.kind]

    # ----- query context -----

    def fiscal_start_for(self, workspace_id: str | None) -> int:
        return self.workspaces.fiscal_year_start(workspace_id) if self.workspaces else self.settings.fiscal_year_start

    def _slot(self, source_id: str) -> threading.Semaphore:
        with self._slots_lock:
            if source_id not in self._slots:
                self._slots[source_id] = threading.BoundedSemaphore(max(1, self.settings.max_queries_per_source))
            return self._slots[source_id]

    def context(self, rec: SourceRecord, who: Principal) -> SourceContext:
        """Everything needed to query `rec` as `who`, including their row-level security rules."""
        config = self.load_config(rec)
        schema = config.schema_name()
        return SourceContext(
            id=rec.id, name=rec.name, engine=self.engine(rec), dialect=self.dialect(rec),
            tables=self.tables(rec), defs=self.effective_definitions(rec), prepared=self.prepared(rec),
            relationships=self.relationships(rec), security=who.rules_for(rec.id),
            timeout_s=self.settings.query_timeout_s, fiscal_start=self.fiscal_start_for(rec.workspace_id), schema=schema,
            slots=self._slot(rec.id),
        )

    def relationships(self, rec: SourceRecord, include_ignored: bool = False) -> list[Relationship]:
        """How tables link: the database's foreign keys, ones you added, and `customer_id -> customers.id`
        style names (CSV files and many MySQL databases declare none). Filters and security rules
        travel along these from the "many" side to the "one" side."""
        defs = self.definitions(rec)
        ignored = set(defs.ignored_relationships)
        snap = [t for t in rec.snapshot or [] if not t.get("error")]
        cols = {t["name"].lower(): {c["name"].lower(): c["name"] for c in t["columns"]} for t in snap}
        names = {t["name"].lower(): t["name"] for t in snap}
        out: list[Relationship] = []
        seen: set[tuple[str, str]] = set()

        def add(rel: Relationship) -> None:
            if (not include_ignored and rel.key in ignored) or (rel.table.lower(), rel.column.lower()) in seen:
                return
            seen.add((rel.table.lower(), rel.column.lower()))
            out.append(rel)

        for u in defs.relationships:
            (t, c), (rt, rc) = u.from_.split("."), u.to.split(".")
            add(Relationship(t, c, rt, rc, "user"))
        for t in snap:
            for fk in t.get("foreign_keys") or []:
                left, _, right = fk.partition("->")
                ref_table, _, ref_cols = right.strip().partition(".")
                if "," in left or "," in ref_cols or not ref_cols:
                    continue  # composite keys aren't used for filtering
                add(Relationship(t["name"], left.strip(), ref_table, ref_cols.strip(), "database"))
        for t in snap:
            for low, col in cols[t["name"].lower()].items():
                m = re.fullmatch(r"(.+?)_?id", low)
                if not m or low == "id":
                    continue
                stem = m.group(1).rstrip("_")
                for cand in (stem, f"{stem}s", f"{stem}es", f"{stem[:-1]}ies" if stem.endswith("y") else ""):
                    if cand and cand in names and cand != t["name"].lower() and "id" in cols[cand]:
                        add(Relationship(t["name"], col, names[cand], cols[cand]["id"], "inferred"))
                        break
        return out

    # ----- reads -----

    @staticmethod
    def definitions(rec: SourceRecord) -> Definitions:
        """Definitions exactly as stored (use for edits)."""
        return Definitions.model_validate(rec.definitions or {})

    def effective_definitions(self, rec: SourceRecord) -> Definitions:
        """Stored definitions plus meanings that prepared-table columns inherit (use for reading).

        A column that passes through a recipe unchanged by name (e.g. sgmt via a merge) keeps the
        meaning it has in its base table, or in the one table that has a column of that name.
        Inherited entries are never saved, so editing the original updates them everywhere.
        """
        defs = self.definitions(rec)
        real = {t["name"]: {c["name"] for c in t["columns"]} for t in rec.snapshot or []}
        for t in rec.transforms or []:
            tdef = defs.tables.setdefault(t["name"], TableDef())
            if not tdef.description:
                # The recipe tells the model what's in it, e.g. that it only holds 2025's completed
                # orders, so it isn't used for questions it can't answer.
                labels = [describe(st) for st in steps_adapter.validate_python(t["steps"])]
                tdef.description = f"Prepared from {t['base']}" + (f": {'; '.join(labels)}" if labels else "")
            for col in t.get("columns", []):
                if col in tdef.columns:
                    continue
                source = defs.column(t["base"], col)
                if source is None:
                    owners = [n for n, cols in real.items() if col in cols and defs.column(n, col)]
                    source = defs.column(owners[0], col) if len(owners) == 1 else None
                if source is not None:
                    tdef.columns[col] = source
        defs.tables = {k: v for k, v in defs.tables.items() if v.description or v.columns or v.synonyms}
        return defs

    @staticmethod
    def tables(rec: SourceRecord) -> list[TableInfo]:
        """Database tables plus prepared tables (as last profiled)."""
        real = [TableInfo.model_validate(t) for t in rec.snapshot or []]
        prepared = [TableInfo.model_validate(t["snapshot"]).model_copy(
                        update={"subset": drops_rows(steps_adapter.validate_python(t["steps"]))})
                    for t in rec.transforms or [] if t.get("snapshot")]
        return real + prepared

    # ----- prepared tables (transforms) -----

    def _compiler(self, rec: SourceRecord, exclude: str | None = None) -> tuple[Compiler, dict[str, str]]:
        """A compiler that knows every table's columns, and the SQL of every other prepared table.

        Prepared tables may build on each other, so they're compiled repeatedly until no more
        can be resolved; ones that still fail (missing columns, loops) are simply left out.
        """
        columns = {t["name"]: [c["name"] for c in t["columns"]] for t in rec.snapshot or [] if not t.get("error")}
        types = {t["name"]: {c["name"]: c["type"] for c in t["columns"]} for t in rec.snapshot or [] if not t.get("error")}
        for t in rec.transforms or []:  # prepared tables' profiled types (inferred from their values)
            if t.get("snapshot") and not t["snapshot"].get("error"):
                types[t["name"]] = {c["name"]: c["type"] for c in t["snapshot"]["columns"]}
        compiled: dict[str, str] = {}
        pending = [t for t in rec.transforms or [] if t["name"] != exclude]
        progress = True
        while pending and progress:
            progress = False
            for t in list(pending):
                try:
                    sql, history = Compiler(self.dialect(rec), columns, types).compile(_recipe(t))
                except TransformError:
                    continue
                compiled[t["name"]], columns[t["name"]] = sql, history[-1]
                pending.remove(t)
                progress = True
        return Compiler(self.dialect(rec), columns, types), compiled

    def prepared(self, rec: SourceRecord) -> dict[str, str]:
        """Prepared-table name -> compiled SELECT, for the query runner."""
        return self._compiler(rec)[1]

    def compile_recipe(self, rec: SourceRecord, recipe: Recipe, upto: int | None = None,
                       editing: str | None = None) -> tuple[str, list[list[str]], dict[str, str]]:
        """Compile a recipe. Pivot steps without values get every distinct value filled in (in place,
        so a saved recipe keeps a fixed set of columns, as in Power Query)."""
        compiler, compiled = self._compiler(rec, exclude=editing or recipe.name)
        last = len(recipe.steps) if upto is None else upto
        for i, step in enumerate(recipe.steps[:last]):
            if isinstance(step, Pivot) and not step.values:
                values = self.distinct_values(rec, recipe, i, step.column, editing, limit=PIVOT_MAX + 1,
                                              _compiler=(compiler, compiled))
                if len(values) > PIVOT_MAX:
                    raise TransformError(f"\"{step.column}\" has more than {PIVOT_MAX} different values; "
                                         "pick the ones to turn into columns.", i)
                recipe.steps[i] = step.model_copy(update={"values": [str(v) for v in values]})
            step = recipe.steps[i]
            if isinstance(step, Pivot) and not step.labels:
                # Name pivoted columns after documented code meanings (sgmt 1 -> "retail").
                meanings = self._code_meanings(rec, recipe.base, step.column)
                if meanings and any(v in meanings for v in step.values):
                    recipe.steps[i] = step.model_copy(update={"labels": {v: meanings[v] for v in step.values if v in meanings}})
        sql, history = compiler.compile(recipe, upto)
        return sql, history, compiled

    def _code_meanings(self, rec: SourceRecord, base: str, column: str) -> dict[str, str]:
        defs = self.effective_definitions(rec)
        d = defs.column(base, column)
        if d is None or not d.values:
            owners = [t for t in defs.tables.values() if column in t.columns and t.columns[column].values]
            d = owners[0].columns[column] if len(owners) == 1 else None
        return dict(d.values) if d and d.values else {}

    def distinct_values(self, rec: SourceRecord, recipe: Recipe, upto: int, column: str,
                        editing: str | None = None, limit: int = 50, _compiler=None) -> list:
        """Distinct values of `column` after the first `upto` steps (for pivot)."""
        compiler, compiled = _compiler or self._compiler(rec, exclude=editing or recipe.name)
        sql = compiler.distinct_sql(recipe, upto, column, limit)
        ctx = dataclasses.replace(self.context(rec, SYSTEM), prepared=compiled)
        try:
            res = run(ctx, sql)
        except (QueryError, UnsafeSQLError) as exc:
            raise TransformError(f"Couldn't read the values of {column}: {exc}", upto) from exc
        return [r[0] for r in res.rows]

    def save_transform(self, db: Session, rec: SourceRecord, recipe: Recipe, original: str | None) -> dict:
        real = {t["name"] for t in rec.snapshot or []}
        others = {t["name"] for t in rec.transforms or [] if t["name"] != (original or recipe.name)}
        if not NAME_RE.match(recipe.name):
            raise TransformError("Use a name with lowercase letters, digits and _ (e.g. clean_orders).")
        if recipe.name in real or recipe.name in others:
            raise TransformError(f"\"{recipe.name}\" is already a table name in this source.")
        if original and original != recipe.name and (used := self.widgets_using(db, rec, original)):
            raise TransformError(f"\"{original}\" is used by {len(used)} widget(s) ({', '.join(used[:3])}), "
                                 "so it can't be renamed. Create a new prepared table instead.")
        sql, history, compiled = self.compile_recipe(rec, recipe, editing=original or recipe.name)
        snapshot = self._profile_prepared(rec, recipe.name, sql, compiled)
        entry = {"name": recipe.name, "base": recipe.base, "steps": [s.model_dump() for s in recipe.steps],
                 "columns": history[-1], "snapshot": snapshot.model_dump(), "error": snapshot.error}
        kept = [t for t in rec.transforms or [] if t["name"] not in (original, recipe.name)]
        rec.transforms = [*kept, entry]
        db.commit()
        return entry

    def delete_transform(self, db: Session, rec: SourceRecord, name: str) -> None:
        if used := self.widgets_using(db, rec, name):
            raise TransformError(f"\"{name}\" is used by {len(used)} widget(s) ({', '.join(used[:3])}). "
                                 "Remove or change those first.")
        if any(name in referenced_tables(sql, self.dialect(rec)) for n, sql in self.prepared(rec).items() if n != name):
            raise TransformError(f"Another prepared table is built on \"{name}\".")
        rec.transforms = [t for t in rec.transforms or [] if t["name"] != name]
        db.commit()

    def widgets_using(self, db: Session, rec: SourceRecord, name: str) -> list[str]:
        out = []
        for d in db.query(DashboardRecord).all():
            out += [f"{d.name}: {w['title']}" for w in d.widgets or []
                    if (w.get("sourceId") or d.source_id) == rec.id
                    and name in referenced_tables(w["sql"], self.dialect(rec))]
        return out

    def coverage(self, rec: SourceRecord, table: str) -> dict[str, list[str]]:
        """Which tables a security rule on `table` reaches (through relationships) and which it doesn't.

        Tables a rule doesn't reach stay fully visible to the viewer (like product lists); showing
        them lets the admin add a relationship or another rule where that matters.
        """
        rels = self.relationships(rec)
        reached, frontier = {table.lower()}, [table.lower()]
        while frontier:
            current = frontier.pop()
            for r in rels:
                if r.ref_table.lower() == current and r.table.lower() not in reached:
                    reached.add(r.table.lower())
                    frontier.append(r.table.lower())
        names = [t["name"] for t in rec.snapshot or [] if not t.get("error")]
        return {"restricted": [n for n in names if n.lower() in reached],
                "unrestricted": [n for n in names if n.lower() not in reached]}

    def _profile_prepared(self, rec: SourceRecord, name: str, sql: str, compiled: dict[str, str]) -> TableInfo:
        """Row count, samples and inferred types for a prepared table, by querying its SELECT."""
        dialect = self.dialect(rec)
        ctx = dataclasses.replace(self.context(rec, SYSTEM), prepared={**compiled, name: sql})
        q = lambda n: quote(n, dialect)
        try:
            head = run(ctx, f"SELECT * FROM {q(name)}", max_rows=1000)
            count = run(ctx, f"SELECT COUNT(*) FROM {q(name)}").rows[0][0]
        except (QueryError, UnsafeSQLError) as exc:
            return TableInfo(name=name, columns=[], prepared=True, error=str(exc)[:300])
        columns = []
        for i, c in enumerate(head.columns):
            values = [r[i] for r in head.rows if r[i] is not None]
            samples = list(dict.fromkeys(str(v)[:40] for v in values))[:5]
            columns.append(ColumnInfo(name=c, type=_infer_type(values), samples=samples))
        return TableInfo(name=name, columns=columns, row_count=count, prepared=True)

    def to_out(self, rec: SourceRecord) -> SourceOut:
        defs = self.effective_definitions(rec)
        all_tables = self.tables(rec)
        columns_of = {t.name: {c.name for c in t.columns} for t in all_tables}
        base_of = {t["name"]: t["base"] for t in rec.transforms or []}
        tables = []
        for t in all_tables:
            tdef = defs.tables.get(t.name)
            # Columns a prepared table passes through from its base are flagged on the base only:
            # explaining the original covers both, and one unclear column isn't counted twice.
            passed = columns_of.get(base_of.get(t.name, ""), set()) if t.prepared else set()
            cols = []
            for c in t.columns:
                cdef = defs.column(t.name, c.name)
                explained = bool(cdef and (cdef.description or cdef.values or cdef.hidden))
                cols.append(ColumnOut(
                    name=c.name, type=c.type, samples=c.samples,
                    definition=cdef.model_dump(exclude_defaults=True, exclude={"source"}) if cdef else None,
                    source=cdef.source if cdef else None,
                    flag=None if c.name in passed else flag_column(c.name, explained),
                ))
            tables.append(TableOut(
                name=t.name, row_count=t.row_count, description=tdef.description if tdef else None,
                foreign_keys=t.foreign_keys, is_view=t.is_view, prepared=t.prepared, error=t.error, columns=cols,
            ))
        hidden = {"path"} if self.settings.hosted else set()  # no server paths
        public = {k: v for k, v in rec.config.items() if k not in hidden and not k.endswith("_enc")}
        stored = self.definitions(rec)
        ignored = set(stored.ignored_relationships)
        rels = [RelationshipOut(table=r.table, column=r.column, ref_table=r.ref_table, ref_column=r.ref_column,
                                origin=r.origin, key=r.key, ignored=r.key in ignored)
                for r in self.relationships(rec, include_ignored=True)]
        detail = "Sample data" if rec.sample and self.settings.hosted else connectors.describe(self.load_config(rec))
        return SourceOut(
            id=rec.id, name=rec.name, kind=rec.kind, sample=bool(rec.sample), detail=detail,
            config=public, database_description=defs.database.description, glossary=defs.glossary,
            tables=tables, profiled_at=as_utc(rec.profiled_at), profile_error=rec.profile_error,
            measures=[m.model_dump(mode="json") for m in stored.measures], relationships=rels,
        )

    def prompt_schema(self, rec: SourceRecord) -> str:
        return render_schema(self.tables(rec), self.effective_definitions(rec))

    # ----- writes -----

    def test(self, config: SourceConfig) -> int:
        """Connect and count tables. Raises SourceConnectionError with a readable message."""
        engine = connectors.check_connection(config, self.settings.query_timeout_s, self.guard)
        try:
            insp = inspector(engine, info_schema=config.info_schema)
            schema = connectors.schema_of(config)
            tables = set(insp.get_table_names(schema=schema))
            try:
                tables |= set(insp.get_view_names(schema=schema))
            except NotImplementedError:
                pass
            return len(tables)
        finally:
            engine.dispose()

    def create(self, db: Session, name: str, config: SourceConfig, source_id: str | None = None,
               sample: bool = False) -> SourceRecord:
        connectors.check_connection(config, self.settings.query_timeout_s, self.guard).dispose()
        rec = SourceRecord(
            id=source_id or new_id("src"), name=name.strip() or "Untitled source", kind=config.kind,
            config=self._store_config(config), definitions={}, snapshot=[], sample=sample,
            workspace_id=db.info.get("workspace_id"),
        )
        db.add(rec)
        self.profile(rec)
        db.commit()
        return rec

    def update_connection(self, db: Session, rec: SourceRecord, name: str, config: SourceConfig) -> SourceRecord:
        if config.kind != rec.kind:
            raise ValueError("A source's type can't be changed; add a new source instead.")
        old = self.load_config(rec)
        if rec.kind == "csv" or rec.sample or (self.settings.hosted and connectors.DIALECTS[rec.kind] in ("sqlite", "duckdb")):
            # Files Stardeck manages (uploads, sample data) can only be renamed: their location
            # isn't the admin's to change.
            rec.name = name.strip() or rec.name
            db.commit()
            return rec
        # An empty password (token, key) in the edit form means "keep the stored one".
        keep = {f: getattr(old, f) for f in config.secrets if not getattr(config, f) and getattr(old, f, "")}
        if keep:
            config = config.model_copy(update=keep)
        connectors.check_connection(config, self.settings.query_timeout_s, self.guard).dispose()
        rec.name, rec.config = name.strip() or rec.name, self._store_config(config)
        self.engines.drop(rec.id)
        self.profile(rec)
        db.commit()
        return rec

    def profile(self, rec: SourceRecord) -> None:
        try:
            config = self.load_config(rec)
            rec.snapshot = [t.model_dump() for t in introspect(self.engine(rec), connectors.schema_of(config),
                                                               info_schema=rec.kind in connectors.INFO_SCHEMA)]
            rec.profile_error = None
        except Exception as exc:  # noqa: BLE001 - surfaced to the user on the source page
            rec.profile_error = str(exc).splitlines()[0][:500]
        rec.profiled_at = utcnow()

    def delete(self, db: Session, rec: SourceRecord) -> None:
        self.engines.drop(rec.id)
        path = Path(rec.config.get("path") or "").resolve()
        # Only ever an upload of this install: never sample data, never a file elsewhere on the server.
        if rec.kind == "csv" and not rec.sample and path.is_relative_to(self.uploads_root):
            path.unlink(missing_ok=True)
        db.delete(rec)
        db.commit()

    @property
    def uploads_root(self) -> Path:
        return (self.settings.data_dir / "uploads").resolve()

    @property
    def demo_path(self) -> Path:
        return (self.settings.data_dir / "demo" / "retail.sqlite").resolve()

    def upload_path(self, source_id: str, workspace_id: str) -> Path:
        """Each workspace's uploads live in their own folder."""
        return self.uploads_root / workspace_id / f"{source_id}.sqlite"

    # ----- definitions -----

    def save_column(self, db: Session, rec: SourceRecord, table: str, column: str,
                    definition: ColumnDef | None) -> None:
        if not any(t.name == table and any(c.name == column for c in t.columns) for t in self.tables(rec)):
            raise KeyError(f"Unknown column {table}.{column}")
        defs = self.definitions(rec)
        tdef = defs.tables.setdefault(table, TableDef())
        empty = definition is None or not (definition.description or definition.unit
                                           or definition.values or definition.hidden or definition.display_name)
        if empty:
            tdef.columns.pop(column, None)
        else:
            tdef.columns[column] = definition
        self._save_defs(db, rec, defs)

    def save_table_description(self, db: Session, rec: SourceRecord, table: str, description: str | None) -> None:
        defs = self.definitions(rec)
        defs.tables.setdefault(table, TableDef()).description = (description or "").strip() or None
        self._save_defs(db, rec, defs)

    def save_context(self, db: Session, rec: SourceRecord, description: str | None,
                     glossary: list[GlossaryTerm] | None) -> None:
        defs = self.definitions(rec)
        if description is not None:
            defs.database.description = description.strip() or None
        if glossary is not None:
            defs.glossary = [g for g in glossary if g.term.strip() and g.definition.strip()]
        self._save_defs(db, rec, defs)

    # ----- measures -----

    def check_measure(self, rec: SourceRecord, m: Measure) -> float | int | str | None:
        """Validate a measure and compute its overall value (shown when saving). Raises QueryError."""
        table = next((t for t in self.tables(rec) if t.name.lower() == m.table.lower() and not t.error), None)
        if table is None:
            raise QueryError(f"There's no table called {m.table}.")
        check_measure(m, self.dialect(rec), {c.name.lower() for c in table.columns})
        ctx = self.context(rec, SYSTEM)
        ctx.defs.measures = [x for x in ctx.defs.measures if x.name != m.name] + [m]
        q = quote(table.name, ctx.dialect)
        try:
            res = run(ctx, f"SELECT MEASURE({m.name}) AS value FROM {q}", max_rows=1)
        except UnsafeSQLError as exc:
            raise QueryError(str(exc)) from exc
        return res.rows[0][0] if res.rows else None

    def save_measure(self, db: Session, rec: SourceRecord, m: Measure, original: str | None = None) -> None:
        defs = self.definitions(rec)
        if any(x.name == m.name for x in defs.measures if x.name != (original or m.name)):
            raise QueryError(f"There's already a measure called {m.name}.")
        m = m.model_copy(update={"table": next(
            (t.name for t in self.tables(rec) if t.name.lower() == m.table.lower()), m.table)})
        kept = [x for x in defs.measures if x.name not in (original, m.name)]
        defs.measures = [*kept, m]
        self._save_defs(db, rec, defs)

    def delete_measure(self, db: Session, rec: SourceRecord, name: str) -> None:
        defs = self.definitions(rec)
        defs.measures = [x for x in defs.measures if x.name != name]
        self._save_defs(db, rec, defs)

    def suggest_measures(self, rec: SourceRecord, llm: LLMClient) -> list[dict]:
        """AI drafts of measures from the glossary and schema. Each is checked against the data and
        returned with its overall value; nothing is saved until the user accepts one."""
        existing = {m.name for m in self.definitions(rec).measures}
        messages = [
            {"role": "system", "content": MEASURE_PROMPT.format(dialect=dialect_name(self.dialect(rec)),
                                                                schema=self.prompt_schema(rec))},
            {"role": "user", "content": "Suggest the measures this business would use most."},
        ]
        drafts = llm.structured(messages, MeasureDrafts)
        out: list[dict] = []
        for d in drafts.measures:
            try:
                m = Measure(name=d.name, label=d.label, description=d.description, table=d.table,
                            expression=d.expression, format=d.format, currency=d.currency or None,
                            up_is_good=d.up_is_good, source="ai_suggested")
            except ValueError:
                continue
            if m.name in existing or any(o["measure"]["name"] == m.name for o in out):
                continue
            try:
                value = self.check_measure(rec, m)
            except (QueryError, UnsafeSQLError):
                continue
            out.append({"measure": m.model_dump(mode="json"), "value": value})
        return out[:8]

    # ----- relationships -----

    def add_relationship(self, db: Session, rec: SourceRecord, frm: str, to: str) -> None:
        cols = {(t.name.lower(), c.name.lower()) for t in self.tables(rec) if not t.prepared for c in t.columns}
        for end in (frm, to):
            t, _, c = end.partition(".")
            if (t.lower(), c.lower()) not in cols:
                raise QueryError(f"There's no column {end}.")
        defs = self.definitions(rec)
        defs.relationships = [r for r in defs.relationships if r.from_.lower() != frm.lower()]
        defs.relationships.append(RelationshipDef(from_=frm, to=to))
        defs.ignored_relationships = [k for k in defs.ignored_relationships if not k.lower().startswith(f"{frm.lower()}->")]
        self._save_defs(db, rec, defs)

    def set_relationship_ignored(self, db: Session, rec: SourceRecord, key: str, ignored: bool) -> None:
        defs = self.definitions(rec)
        user = [r for r in defs.relationships if f"{r.from_}->{r.to}" == key]
        if user and ignored:  # one you added: just remove it
            defs.relationships = [r for r in defs.relationships if f"{r.from_}->{r.to}" != key]
        elif ignored and key not in defs.ignored_relationships:
            defs.ignored_relationships.append(key)
        elif not ignored:
            defs.ignored_relationships = [k for k in defs.ignored_relationships if k != key]
        self._save_defs(db, rec, defs)

    def column_values(self, ctx: SourceContext, table: str, column: str, search: str = "",
                      limit: int = 200) -> tuple[list, bool]:
        """Distinct values of a column for a filter or a security rule (as the user may see them)."""
        t = next((x for x in ctx.tables if x.name.lower() == table.lower() and not x.error), None)
        if t is None or not any(c.name == column for c in t.columns):
            raise QueryError(f"There's no column {table}.{column}.")
        col = exp.Column(this=ident(column, ctx.dialect))
        q = (exp.select(col.as_("v")).distinct().from_(exp.Table(this=ident(t.name, ctx.dialect)))
             .where(exp.Not(this=exp.Is(this=col.copy(), expression=exp.Null()))).order_by(col.copy()))
        if search.strip():
            text_col = exp.cast(col.copy(), "CHAR" if ctx.dialect == "mysql" else "VARCHAR(4000)" if ctx.dialect == "oracle" else "TEXT")
            q = q.where(exp.Like(this=exp.Lower(this=text_col),
                                 expression=exp.Literal.string(f"%{search.strip().lower()}%")), append=True)
        res = run(ctx, q.limit(limit + 1).sql(dialect=ctx.dialect), max_rows=limit + 1)
        values = [r[0] for r in res.rows]
        return values[:limit], len(values) > limit

    def import_yaml(self, db: Session, rec: SourceRecord, text: str) -> None:
        self._save_defs(db, rec, Definitions.from_yaml(text))

    @staticmethod
    def _save_defs(db: Session, rec: SourceRecord, defs: Definitions) -> None:
        # Drop tables left with nothing in them so exports stay tidy.
        defs.tables = {k: v for k, v in defs.tables.items() if v.description or v.columns or v.synonyms}
        rec.definitions = defs.model_dump(mode="json", by_alias=True)
        db.commit()

    def suggest_definitions(self, db: Session, rec: SourceRecord, llm: LLMClient, limit: int = 40) -> int:
        """Ask the model to guess what flagged columns mean. Saved as `ai_suggested` for review."""
        out = self.to_out(rec)
        targets = [(t.name, c) for t in out.tables for c in t.columns if c.flag and c.definition is None][:limit]
        if not targets:
            return 0
        lines = [f"- {t}.{c.name} ({c.type}) samples: {', '.join(c.samples) or 'none'}" for t, c in targets]
        messages = [
            {"role": "system", "content": SUGGEST_PROMPT.format(schema=self.prompt_schema(rec))},
            {"role": "user", "content": "Columns to explain:\n" + "\n".join(lines)},
        ]
        result = llm.structured(messages, Suggestions)
        wanted = {(t, c.name) for t, c in targets}
        saved = 0
        defs = self.definitions(rec)
        for s in result.columns:
            if (s.table, s.column) not in wanted or not s.description.strip() or s.confidence == "low":
                continue
            defs.tables.setdefault(s.table, TableDef()).columns[s.column] = ColumnDef(
                description=s.description.strip(), unit=s.unit or None,
                values={v.code: v.meaning for v in s.values}, source="ai_suggested",
            )
            saved += 1
        self._save_defs(db, rec, defs)
        return saved

    # ----- sample data -----

    def seed_sample(self, db: Session, fixed_ids: bool = False) -> SourceRecord | None:
        """The retail demo as sample data in `db`'s workspace, on a fresh workspace only.

        Every workspace reads the same read-only demo file. Self-hosted installs keep the
        historical fixed id (`fixed_ids`); hosted workspaces get their own.
        """
        if db.scalar(select(SourceRecord.id).limit(1)) is not None:
            return None
        path = retail.ensure(self.demo_path)
        rec = self.create(db, "Retail demo", SQLiteConfig(path=str(path)), source_id=DEMO_ID if fixed_ids else None,
                          sample=True)
        defs = Definitions.load(retail.DEFINITIONS)
        # Leave one cryptic column unexplained so the "Help the AI" flow has something to show.
        defs.tables["orders"].columns.pop("sts", None)
        self._save_defs(db, rec, defs)
        return rec


def _recipe(entry: dict) -> Recipe:
    return Recipe(name=entry["name"], base=entry["base"], steps=steps_adapter.validate_python(entry["steps"]))


_DATE_VALUE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T].*)?$")


def _infer_type(values: list) -> str:
    if values and all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        return "INTEGER"
    if values and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values):
        return "REAL"
    if values and all(isinstance(v, str) and _DATE_VALUE.match(v) for v in values):
        return "DATE"
    return "TEXT"


def as_utc(dt: datetime | None) -> datetime | None:
    """SQLite drops tzinfo; values are always written in UTC, so restore it before serializing."""
    return dt.replace(tzinfo=UTC) if dt and dt.tzinfo is None else dt


class SuggestedValue(BaseModel):
    code: str
    meaning: str


class SuggestedColumn(BaseModel):
    table: str
    column: str
    description: str = Field(description="Plain-English meaning, a few words.")
    unit: str | None = None
    values: list[SuggestedValue] = Field(default_factory=list, description="Only for coded columns.")
    confidence: str = Field(description="high, medium or low")


class Suggestions(BaseModel):
    columns: list[SuggestedColumn]


SUGGEST_PROMPT = """You help non-technical users understand their database.
For each listed column, guess what it means from its name, type, sample values and the rest of
the schema. Keep descriptions short (under 8 words), with no example values and no trailing period.
Add a unit only when clear (years, INR, kg).
If the samples look like codes, list the codes you see with a likely meaning only if you are fairly sure.
Use confidence "low" when you are guessing; those are discarded.

SCHEMA:
{schema}"""



class MeasureDraft(BaseModel):
    name: str = Field(description="snake_case id, e.g. revenue, completed_orders, refund_rate")
    label: str = Field(description="Short display name, e.g. Revenue")
    description: str = Field(description="One plain sentence: what it counts and which rows.")
    table: str = Field(description="The one table whose columns the formula uses.")
    expression: str = Field(description="Aggregate SQL over that table's columns, no table names.")
    format: Literal["number", "currency", "percent"] = "number"
    currency: str | None = Field(None, description="ISO code (INR, USD) for money.")
    up_is_good: bool = True


class MeasureDrafts(BaseModel):
    measures: list[MeasureDraft] = Field(max_length=8)


MEASURE_PROMPT = """You define a business's key measures for a dashboard tool.
Suggest 3-6 measures (revenue, counts, averages, rates) this data supports. Each is ONE aggregate
SQL expression ({dialect}) over ONE table's columns, written without table names, e.g.
  SUM(CASE WHEN sts = 1 THEN amt END) / 100.0
Follow the column descriptions, code values, units and glossary exactly (convert units as stated;
filter with CASE WHEN inside the aggregate, never WHERE). Rates are percentages:
  100.0 * SUM(CASE WHEN sts = 9 THEN 1 ELSE 0 END) / COUNT(*)
Set up_is_good false when higher is bad (refunds, cancellations, churn).

SCHEMA:
{schema}"""
