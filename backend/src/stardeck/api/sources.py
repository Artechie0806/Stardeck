"""/api/sources: connect data sources and manage their definitions (M1)."""

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field

from stardeck.connectors import SourceConfig, SourceConnectionError, SQLiteConfig, UploadConfig
from stardeck.dashboards import source_ids
from stardeck.db import DashboardRecord, SourceRecord
from stardeck.definitions import ColumnDef, Definitions, DefinitionSource, GlossaryTerm, Measure
from stardeck.deps import (
    AdminDep,
    BackgroundLLMDep,
    EditorDep,
    PlanDep,
    SessionDep,
    StateDep,
    WhoDep,
    ai_error,
    require_feature,
)
from stardeck.ids import new_id
from stardeck.llm import LLMError
from stardeck.plans import check_room, need
from stardeck.principal import SYSTEM, Principal
from stardeck.query import QueryError, UnsafeSQLError
from stardeck.sources import ApiModel, SourceOut
from stardeck.uploads import UploadError, load_files
from stardeck.usage import real_sources

router = APIRouter(prefix="/sources", tags=["sources"])


class ConnectRequest(BaseModel):
    name: str
    config: SourceConfig


class TestResult(BaseModel):
    ok: bool
    message: str
    tables: int | None = None


class ColumnUpdate(BaseModel):
    definition: ColumnDef | None
    source: DefinitionSource = "user"


class TableUpdate(BaseModel):
    description: str | None


class ContextUpdate(BaseModel):
    description: str | None = None
    glossary: list[GlossaryTerm] | None = None


class SuggestResult(BaseModel):
    suggested: int
    source: SourceOut


class MeasureIn(ApiModel):
    measure: Measure
    original_name: str | None = None


class RelationshipIn(ApiModel):
    from_: str = Field(alias="from")
    to: str


class RelationshipToggle(ApiModel):
    key: str
    ignored: bool


def _visible(out: SourceOut, who: Principal) -> SourceOut:
    """Viewers get a source's name and type only: no schema, sample values or connection details."""
    if who.can("editor"):
        return out
    return out.model_copy(update={"tables": [], "config": {}, "measures": [], "relationships": [], "glossary": [],
                                  "database_description": None, "detail": ""})


def _network_only(state, config: SourceConfig) -> None:
    """Hosted: a source is a network database or an upload, never a path on the server."""
    if state.settings.hosted and isinstance(config, SQLiteConfig | UploadConfig):
        raise HTTPException(400, "Upload your SQLite, CSV or Excel files instead of giving a file path.")


def _read_upload(f: UploadFile, budget: int, limit_mb: int) -> bytes:
    data = f.file.read(budget + 1)  # never more than allowed into memory
    if len(data) > budget:
        raise HTTPException(413, f"Upload is larger than {limit_mb} MB")
    return data


def _get(db, source_id: str) -> SourceRecord:
    rec = db.get(SourceRecord, source_id)
    if rec is None:
        raise HTTPException(404, f"Data source {source_id} not found")
    return rec


@router.get("", response_model=list[SourceOut])
def list_sources(state: StateDep, db: SessionDep, who: WhoDep):
    recs = db.query(SourceRecord).order_by(SourceRecord.created_at).all()
    return [_visible(state.sources.to_out(r), who) for r in recs]


@router.post("/test", response_model=TestResult)
def test_connection(req: ConnectRequest, state: StateDep, who: AdminDep):
    _network_only(state, req.config)
    try:
        n = state.sources.test(req.config)
    except SourceConnectionError as exc:
        return TestResult(ok=False, message=str(exc))
    noun = "table or view" if n == 1 else "tables and views"
    return TestResult(ok=True, message=f"Connected. Found {n} {noun}.", tables=n)


@router.post("", response_model=SourceOut, status_code=201)
def create_source(req: ConnectRequest, state: StateDep, db: SessionDep, who: AdminDep, plan: PlanDep):
    if isinstance(req.config, UploadConfig):
        raise HTTPException(400, "Use /api/sources/upload for CSV and Excel files")
    _network_only(state, req.config)
    with state.workspaces.lock(who.workspace_id):
        check_room(plan, "sources", real_sources(db))
        try:
            rec = state.sources.create(db, req.name, req.config)
        except SourceConnectionError as exc:
            raise HTTPException(400, str(exc)) from exc
    return state.sources.to_out(rec)


@router.post("/upload", response_model=SourceOut, status_code=201)
def upload_source(
    state: StateDep, db: SessionDep, who: AdminDep, plan: PlanDep,
    name: Annotated[str, Form()], files: Annotated[list[UploadFile], File()],
):
    """Runs in a worker thread: parsing a big file mustn't hold up everyone else's requests."""
    check_room(plan, "sources", real_sources(db))  # before reading the files
    limit_mb = min(state.settings.max_upload_mb, plan.upload_mb or state.settings.max_upload_mb)
    budget = limit_mb * 1024 * 1024
    payload = []
    for f in files:
        data = _read_upload(f, budget, limit_mb)
        budget -= len(data)
        payload.append((f.filename or "data.csv", data))
    source_id = new_id("src")
    path = state.sources.upload_path(source_id, who.workspace_id)
    try:
        load_files(payload, path)
    except UploadError as exc:
        raise HTTPException(400, str(exc)) from exc
    config = UploadConfig(path=str(path), files=[n for n, _ in payload])
    with state.workspaces.lock(who.workspace_id):
        try:
            check_room(plan, "sources", real_sources(db))  # again: another upload may have finished meanwhile
        except HTTPException:
            path.unlink(missing_ok=True)
            raise
        rec = state.sources.create(db, name or payload[0][0], config, source_id=source_id)
    return state.sources.to_out(rec)


@router.get("/{source_id}", response_model=SourceOut)
def get_source(source_id: str, state: StateDep, db: SessionDep, who: WhoDep):
    return _visible(state.sources.to_out(_get(db, source_id)), who)


@router.put("/{source_id}", response_model=SourceOut)
def update_source(source_id: str, req: ConnectRequest, state: StateDep, db: SessionDep, who: AdminDep):
    rec = _get(db, source_id)
    try:
        state.sources.update_connection(db, rec, req.name, req.config)  # file sources: rename only
    except (SourceConnectionError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return state.sources.to_out(rec)


@router.delete("/{source_id}", status_code=204)
def delete_source(source_id: str, state: StateDep, db: SessionDep, who: AdminDep):
    """Removes the source, dashboards built only on it, and its charts on dashboards that mix sources."""
    rec = _get(db, source_id)  # before changing anything
    for d in db.query(DashboardRecord).all():
        ids = source_ids(d)
        if source_id not in ids:
            continue
        rest = [i for i in ids if i != source_id]
        if not rest:
            db.delete(d)
            continue
        d.widgets = [w for w in d.widgets or [] if (w.get("sourceId") or d.source_id) != source_id]
        d.filters = [f for f in d.filters or [] if f.get("sourceId") != source_id]
        d.source_ids, d.source_id = rest, rest[0] if d.source_id == source_id else d.source_id
    db.commit()
    state.dashboards.cache.drop_source(source_id)
    state.sources.delete(db, rec)


@router.post("/{source_id}/refresh", response_model=SourceOut)
def refresh_source(source_id: str, state: StateDep, db: SessionDep, who: AdminDep):
    """Re-read tables and columns, e.g. after the database schema changed."""
    rec = _get(db, source_id)
    state.sources.profile(rec)
    db.commit()
    state.dashboards.cache.drop_source(rec.id)
    return state.sources.to_out(rec)


@router.put("/{source_id}/columns/{table}/{column}", response_model=SourceOut)
def save_column(source_id: str, table: str, column: str, req: ColumnUpdate, state: StateDep, db: SessionDep, who: EditorDep):
    rec = _get(db, source_id)
    definition = req.definition.model_copy(update={"source": req.source}) if req.definition else None
    try:
        state.sources.save_column(db, rec, table, column, definition)
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0])) from exc
    return state.sources.to_out(rec)


@router.put("/{source_id}/tables/{table}", response_model=SourceOut)
def save_table(source_id: str, table: str, req: TableUpdate, state: StateDep, db: SessionDep, who: EditorDep):
    rec = _get(db, source_id)
    state.sources.save_table_description(db, rec, table, req.description)
    return state.sources.to_out(rec)


@router.put("/{source_id}/context", response_model=SourceOut)
def save_context(source_id: str, req: ContextUpdate, state: StateDep, db: SessionDep, who: EditorDep):
    rec = _get(db, source_id)
    state.sources.save_context(db, rec, req.description, req.glossary)
    return state.sources.to_out(rec)


@router.post("/{source_id}/suggest", response_model=SuggestResult)
def suggest_definitions(source_id: str, state: StateDep, db: SessionDep, llm: BackgroundLLMDep, who: EditorDep):
    """Let the model guess meanings for unexplained, cryptic columns."""
    rec = _get(db, source_id)
    charge = state.quotas.charge(who.workspace_id, "assistant")
    try:
        n = state.sources.suggest_definitions(db, rec, llm)
    except LLMError as exc:
        state.quotas.refund(charge)
        raise ai_error(exc) from exc
    return SuggestResult(suggested=n, source=state.sources.to_out(rec))


@router.get("/{source_id}/definitions.yaml")
def export_definitions(source_id: str, state: StateDep, db: SessionDep, who: EditorDep):
    rec = _get(db, source_id)
    return Response(
        state.sources.definitions(rec).to_yaml(), media_type="application/yaml",
        headers={"Content-Disposition": f'attachment; filename="{rec.id}.definitions.yaml"'},
    )


@router.put("/{source_id}/definitions.yaml", response_model=SourceOut)
def import_definitions(source_id: str, state: StateDep, db: SessionDep, who: EditorDep, plan: PlanDep,
                       file: Annotated[UploadFile, File()]):
    rec = _get(db, source_id)
    try:
        text = _read_upload(file, 2 * 1024 * 1024, 2).decode()
        new, old = Definitions.from_yaml(text), state.sources.definitions(rec)
        # A definitions file can't bring in what the plan doesn't include.
        if new.measures != old.measures:
            need(plan, "measures")
        if (new.relationships, new.ignored_relationships) != (old.relationships, old.ignored_relationships):
            need(plan, "relationships")
        state.sources.import_yaml(db, rec, text)
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, f"Not a valid definitions file: {exc}") from exc
    return state.sources.to_out(rec)


# ---------- measures ----------

@router.post("/{source_id}/measures/check", dependencies=[Depends(require_feature("measures"))])
def check_measure(source_id: str, req: MeasureIn, state: StateDep, db: SessionDep, who: EditorDep) -> dict:
    """Validate a measure and return its overall value, before saving."""
    try:
        return {"value": state.sources.check_measure(_get(db, source_id), req.measure)}
    except QueryError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.put("/{source_id}/measures/{name}", response_model=SourceOut, dependencies=[Depends(require_feature("measures"))])
def save_measure(source_id: str, name: str, req: MeasureIn, state: StateDep, db: SessionDep, who: EditorDep):
    rec = _get(db, source_id)
    measure = req.measure.model_copy(update={"name": name}) if name != req.measure.name else req.measure
    try:
        state.sources.check_measure(rec, measure)
        state.sources.save_measure(db, rec, measure, req.original_name)
    except QueryError as exc:
        raise HTTPException(400, str(exc)) from exc
    state.dashboards.cache.drop_source(rec.id)  # widgets using it show the new formula
    return state.sources.to_out(rec)


@router.delete("/{source_id}/measures/{name}", response_model=SourceOut)
def delete_measure(source_id: str, name: str, state: StateDep, db: SessionDep, who: EditorDep):
    rec = _get(db, source_id)
    state.sources.delete_measure(db, rec, name)
    state.dashboards.cache.drop_source(rec.id)
    return state.sources.to_out(rec)


@router.post("/{source_id}/measures/suggest", dependencies=[Depends(require_feature("measures"))])
def suggest_measures(source_id: str, state: StateDep, db: SessionDep, llm: BackgroundLLMDep, who: EditorDep) -> dict:
    """AI drafts of measures from the glossary and schema, each checked against the data (not saved)."""
    rec = _get(db, source_id)
    charge = state.quotas.charge(who.workspace_id, "assistant")
    try:
        return {"measures": state.sources.suggest_measures(rec, llm)}
    except LLMError as exc:
        state.quotas.refund(charge)
        raise ai_error(exc) from exc


# ---------- relationships ----------

@router.post("/{source_id}/relationships", response_model=SourceOut, dependencies=[Depends(require_feature("relationships"))])
def add_relationship(source_id: str, req: RelationshipIn, state: StateDep, db: SessionDep, who: EditorDep):
    rec = _get(db, source_id)
    try:
        state.sources.add_relationship(db, rec, req.from_.strip(), req.to.strip())
    except QueryError as exc:
        raise HTTPException(400, str(exc)) from exc
    state.dashboards.cache.drop_source(rec.id)
    return state.sources.to_out(rec)


@router.put("/{source_id}/relationships", response_model=SourceOut, dependencies=[Depends(require_feature("relationships"))])
def toggle_relationship(source_id: str, req: RelationshipToggle, state: StateDep, db: SessionDep, who: EditorDep):
    rec = _get(db, source_id)
    state.sources.set_relationship_ignored(db, rec, req.key, req.ignored)
    state.dashboards.cache.drop_source(rec.id)
    return state.sources.to_out(rec)


# ---------- column values (filters and security rules) ----------

@router.get("/{source_id}/values")
def column_values(source_id: str, table: str, column: str, state: StateDep, db: SessionDep, who: EditorDep,
                  q: str = "") -> dict:
    """Distinct values of a column, with their documented meanings, for setting up filters and rules."""
    rec = _get(db, source_id)
    ctx = state.sources.context(rec, SYSTEM)
    try:
        values, more = state.sources.column_values(ctx, table, column, q)
    except (QueryError, UnsafeSQLError) as exc:
        raise HTTPException(400, str(exc)) from exc
    cdef = ctx.defs.column(table, column)
    return {"values": values, "more": more, "labels": dict(cdef.values) if cdef and cdef.values else {}}


@router.get("/{source_id}/coverage", dependencies=[Depends(require_feature("people"))])
def rule_coverage(source_id: str, table: str, state: StateDep, db: SessionDep, who: AdminDep) -> dict:
    """For a security rule on `table`: the tables it restricts (via relationships) and the ones it doesn't."""
    return state.sources.coverage(_get(db, source_id), table)
