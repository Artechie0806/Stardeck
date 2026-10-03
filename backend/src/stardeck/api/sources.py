"""/api/sources: connect data sources and manage their definitions (M1)."""

import secrets
from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Response, UploadFile
from pydantic import BaseModel

from stardeck.connectors import SourceConfig, SourceConnectionError, UploadConfig
from stardeck.db import DashboardRecord, SourceRecord
from stardeck.definitions import ColumnDef, DefinitionSource, GlossaryTerm
from stardeck.deps import LLMDep, SessionDep, StateDep
from stardeck.llm import LLMError
from stardeck.sources import SourceOut
from stardeck.uploads import UploadError, load_files

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


def _get(db, source_id: str) -> SourceRecord:
    rec = db.get(SourceRecord, source_id)
    if rec is None:
        raise HTTPException(404, f"Data source {source_id} not found")
    return rec


@router.get("", response_model=list[SourceOut])
def list_sources(state: StateDep, db: SessionDep):
    recs = db.query(SourceRecord).order_by(SourceRecord.created_at).all()
    return [state.sources.to_out(r) for r in recs]


@router.post("/test", response_model=TestResult)
def test_connection(req: ConnectRequest, state: StateDep):
    try:
        n = state.sources.test(req.config)
    except SourceConnectionError as exc:
        return TestResult(ok=False, message=str(exc))
    noun = "table or view" if n == 1 else "tables and views"
    return TestResult(ok=True, message=f"Connected. Found {n} {noun}.", tables=n)


@router.post("", response_model=SourceOut, status_code=201)
def create_source(req: ConnectRequest, state: StateDep, db: SessionDep):
    if isinstance(req.config, UploadConfig):
        raise HTTPException(400, "Use /api/sources/upload for CSV and Excel files")
    try:
        rec = state.sources.create(db, req.name, req.config)
    except SourceConnectionError as exc:
        raise HTTPException(400, str(exc)) from exc
    return state.sources.to_out(rec)


@router.post("/upload", response_model=SourceOut, status_code=201)
async def upload_source(
    state: StateDep, db: SessionDep,
    name: Annotated[str, Form()], files: Annotated[list[UploadFile], File()],
):
    limit = state.settings.max_upload_mb * 1024 * 1024
    payload, total = [], 0
    for f in files:
        data = await f.read()
        total += len(data)
        if total > limit:
            raise HTTPException(413, f"Upload is larger than {state.settings.max_upload_mb} MB")
        payload.append((f.filename or "data.csv", data))
    source_id = f"src-{secrets.token_hex(4)}"
    path = state.sources.upload_path(source_id)
    try:
        load_files(payload, path)
    except UploadError as exc:
        raise HTTPException(400, str(exc)) from exc
    config = UploadConfig(path=str(path), files=[n for n, _ in payload])
    rec = state.sources.create(db, name or payload[0][0], config, source_id=source_id)
    return state.sources.to_out(rec)


@router.get("/{source_id}", response_model=SourceOut)
def get_source(source_id: str, state: StateDep, db: SessionDep):
    return state.sources.to_out(_get(db, source_id))


@router.put("/{source_id}", response_model=SourceOut)
def update_source(source_id: str, req: ConnectRequest, state: StateDep, db: SessionDep):
    rec = _get(db, source_id)
    try:
        state.sources.update_connection(db, rec, req.name, req.config)
    except (SourceConnectionError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return state.sources.to_out(rec)


@router.delete("/{source_id}", status_code=204)
def delete_source(source_id: str, state: StateDep, db: SessionDep):
    """Removes the source and every dashboard built on it."""
    db.query(DashboardRecord).filter(DashboardRecord.source_id == source_id).delete()
    state.dashboards.cache.drop_source(source_id)
    state.sources.delete(db, _get(db, source_id))


@router.post("/{source_id}/refresh", response_model=SourceOut)
def refresh_source(source_id: str, state: StateDep, db: SessionDep):
    """Re-read tables and columns, e.g. after the database schema changed."""
    rec = _get(db, source_id)
    state.sources.profile(rec)
    db.commit()
    state.dashboards.cache.drop_source(rec.id)
    return state.sources.to_out(rec)


@router.put("/{source_id}/columns/{table}/{column}", response_model=SourceOut)
def save_column(source_id: str, table: str, column: str, req: ColumnUpdate, state: StateDep, db: SessionDep):
    rec = _get(db, source_id)
    definition = req.definition.model_copy(update={"source": req.source}) if req.definition else None
    try:
        state.sources.save_column(db, rec, table, column, definition)
    except KeyError as exc:
        raise HTTPException(404, str(exc.args[0])) from exc
    return state.sources.to_out(rec)


@router.put("/{source_id}/tables/{table}", response_model=SourceOut)
def save_table(source_id: str, table: str, req: TableUpdate, state: StateDep, db: SessionDep):
    rec = _get(db, source_id)
    state.sources.save_table_description(db, rec, table, req.description)
    return state.sources.to_out(rec)


@router.put("/{source_id}/context", response_model=SourceOut)
def save_context(source_id: str, req: ContextUpdate, state: StateDep, db: SessionDep):
    rec = _get(db, source_id)
    state.sources.save_context(db, rec, req.description, req.glossary)
    return state.sources.to_out(rec)


@router.post("/{source_id}/suggest", response_model=SuggestResult)
def suggest_definitions(source_id: str, state: StateDep, db: SessionDep, llm: LLMDep):
    """Let the model guess meanings for unexplained, cryptic columns."""
    rec = _get(db, source_id)
    try:
        n = state.sources.suggest_definitions(db, rec, llm)
    except LLMError as exc:
        raise HTTPException(503, f"The AI model is not available: {exc}") from exc
    return SuggestResult(suggested=n, source=state.sources.to_out(rec))


@router.get("/{source_id}/definitions.yaml")
def export_definitions(source_id: str, state: StateDep, db: SessionDep):
    rec = _get(db, source_id)
    return Response(
        state.sources.definitions(rec).to_yaml(), media_type="application/yaml",
        headers={"Content-Disposition": f'attachment; filename="{rec.id}.definitions.yaml"'},
    )


@router.put("/{source_id}/definitions.yaml", response_model=SourceOut)
async def import_definitions(source_id: str, state: StateDep, db: SessionDep, file: Annotated[UploadFile, File()]):
    rec = _get(db, source_id)
    try:
        state.sources.import_yaml(db, rec, (await file.read()).decode())
    except (ValueError, UnicodeDecodeError) as exc:
        raise HTTPException(400, f"Not a valid definitions file: {exc}") from exc
    return state.sources.to_out(rec)
