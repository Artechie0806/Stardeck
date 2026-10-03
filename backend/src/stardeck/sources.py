"""Data sources: storage, connection, profiling, and the definitions attached to them."""

import re
import secrets
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic.alias_generators import to_camel
from sqlalchemy import inspect, select
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session
from sqlglot import exp

from stardeck import connectors
from stardeck.config import Settings
from stardeck.connectors import SourceConfig, SQLiteConfig
from stardeck.db import DashboardRecord, SourceRecord, utcnow
from stardeck.definitions import ColumnDef, Definitions, DefinitionSource, GlossaryTerm, TableDef
from stardeck.demo import retail
from stardeck.llm import LLMClient
from stardeck.query import QueryError, UnsafeSQLError, run
from stardeck.schema import ColumnInfo, TableInfo, flag_column, introspect, render_schema
from stardeck.secrets import SecretBox
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


class SourceOut(ApiModel):
    id: str
    name: str
    kind: str
    detail: str
    # Connection settings without secrets, so the edit form can be prefilled.
    config: dict
    database_description: str | None
    glossary: list[GlossaryTerm]
    tables: list[TableOut]
    profiled_at: datetime | None
    profile_error: str | None


# ---------- engines ----------

class EngineRegistry:
    """One pooled engine per source, rebuilt when its config changes."""

    def __init__(self) -> None:
        self._engines: dict[str, Engine] = {}
        self._lock = Lock()

    def get(self, source_id: str, factory) -> Engine:
        with self._lock:
            if source_id not in self._engines:
                self._engines[source_id] = factory()
            return self._engines[source_id]

    def drop(self, source_id: str) -> None:
        with self._lock:
            engine = self._engines.pop(source_id, None)
        if engine:
            engine.dispose()


class SourceService:
    def __init__(self, settings: Settings, box: SecretBox, engines: EngineRegistry):
        self.settings = settings
        self.box = box
        self.engines = engines

    # ----- config <-> storage -----

    def _store_config(self, config: SourceConfig) -> dict:
        data = config.model_dump(by_alias=True)
        password = data.pop("password", None)
        if password:
            data["password_enc"] = self.box.encrypt(password)
        return data

    def load_config(self, rec: SourceRecord) -> SourceConfig:
        data = dict(rec.config)
        enc = data.pop("password_enc", None)
        if enc:
            data["password"] = self.box.decrypt(enc)
        return _config_adapter.validate_python(data)

    def engine(self, rec: SourceRecord) -> Engine:
        return self.engines.get(
            rec.id, lambda: connectors.build_engine(self.load_config(rec), self.settings.query_timeout_s)
        )

    @staticmethod
    def dialect(rec: SourceRecord) -> str:
        return connectors.DIALECTS[rec.kind]

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
        try:
            res = run(self.engine(rec), sql, self.dialect(rec), self.settings.query_timeout_s, prepared=compiled)
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
        for d in db.query(DashboardRecord).filter(DashboardRecord.source_id == rec.id):
            out += [f"{d.name}: {w['title']}" for w in d.widgets or []
                    if name in referenced_tables(w["sql"], self.dialect(rec))]
        return out

    def _profile_prepared(self, rec: SourceRecord, name: str, sql: str, compiled: dict[str, str]) -> TableInfo:
        """Row count, samples and inferred types for a prepared table, by querying its SELECT."""
        dialect, engine = self.dialect(rec), self.engine(rec)
        prepared = {**compiled, name: sql}
        q = lambda n: exp.to_identifier(n, quoted=True).sql(dialect=dialect)
        try:
            head = run(engine, f"SELECT * FROM {q(name)}", dialect, self.settings.query_timeout_s,
                       max_rows=1000, prepared=prepared)
            count = run(engine, f"SELECT COUNT(*) FROM {q(name)}", dialect, self.settings.query_timeout_s,
                        prepared=prepared).rows[0][0]
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
        public = {k: v for k, v in rec.config.items() if k != "password_enc"}
        return SourceOut(
            id=rec.id, name=rec.name, kind=rec.kind, detail=connectors.describe(self.load_config(rec)),
            config=public, database_description=defs.database.description, glossary=defs.glossary,
            tables=tables, profiled_at=as_utc(rec.profiled_at), profile_error=rec.profile_error,
        )

    def prompt_schema(self, rec: SourceRecord) -> str:
        return render_schema(self.tables(rec), self.effective_definitions(rec))

    # ----- writes -----

    def test(self, config: SourceConfig) -> int:
        """Connect and count tables. Raises SourceConnectionError with a readable message."""
        engine = connectors.check_connection(config, self.settings.query_timeout_s)
        try:
            insp = inspect(engine)
            schema = connectors.schema_of(config)
            return len(insp.get_table_names(schema=schema)) + len(insp.get_view_names(schema=schema))
        finally:
            engine.dispose()

    def create(self, db: Session, name: str, config: SourceConfig, source_id: str | None = None) -> SourceRecord:
        connectors.check_connection(config, self.settings.query_timeout_s).dispose()
        rec = SourceRecord(
            id=source_id or f"src-{secrets.token_hex(4)}", name=name.strip() or "Untitled source",
            kind=config.kind, config=self._store_config(config), definitions={}, snapshot=[],
        )
        db.add(rec)
        self.profile(rec)
        db.commit()
        return rec

    def update_connection(self, db: Session, rec: SourceRecord, name: str, config: SourceConfig) -> SourceRecord:
        if config.kind != rec.kind:
            raise ValueError("A source's type can't be changed; add a new source instead.")
        old = self.load_config(rec)
        # An empty password in the edit form means "keep the stored one".
        if hasattr(config, "password") and not config.password and getattr(old, "password", ""):
            config = config.model_copy(update={"password": old.password})
        connectors.check_connection(config, self.settings.query_timeout_s).dispose()
        rec.name, rec.config = name.strip() or rec.name, self._store_config(config)
        self.engines.drop(rec.id)
        self.profile(rec)
        db.commit()
        return rec

    def profile(self, rec: SourceRecord) -> None:
        try:
            config = self.load_config(rec)
            rec.snapshot = [t.model_dump() for t in introspect(self.engine(rec), connectors.schema_of(config))]
            rec.profile_error = None
        except Exception as exc:  # noqa: BLE001 - surfaced to the user on the source page
            rec.profile_error = str(exc).splitlines()[0][:500]
        rec.profiled_at = utcnow()

    def delete(self, db: Session, rec: SourceRecord) -> None:
        self.engines.drop(rec.id)
        if rec.kind == "csv":
            Path(rec.config["path"]).unlink(missing_ok=True)
        db.delete(rec)
        db.commit()

    def upload_path(self, source_id: str) -> Path:
        return (self.settings.data_dir / "uploads" / f"{source_id}.sqlite").resolve()

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

    def import_yaml(self, db: Session, rec: SourceRecord, text: str) -> None:
        self._save_defs(db, rec, Definitions.from_yaml(text))

    @staticmethod
    def _save_defs(db: Session, rec: SourceRecord, defs: Definitions) -> None:
        # Drop tables left with nothing in them so exports stay tidy.
        defs.tables = {k: v for k, v in defs.tables.items() if v.description or v.columns or v.synonyms}
        rec.definitions = defs.model_dump(mode="json")
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

    # ----- demo -----

    def seed_demo(self, db: Session) -> None:
        if db.scalar(select(SourceRecord.id).limit(1)) is not None:
            return  # only on a fresh install
        path = retail.ensure((self.settings.data_dir / "demo" / "retail.sqlite").resolve())
        rec = self.create(db, "Retail demo", SQLiteConfig(path=str(path)), source_id=DEMO_ID)
        defs = Definitions.load(retail.DEFINITIONS)
        # Leave one cryptic column unexplained so the "Help the AI" flow has something to show.
        defs.tables["orders"].columns.pop("sts", None)
        self._save_defs(db, rec, defs)


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

