"""/api/dashboards: saved dashboards, running them, and AI generation (M3)."""

import json
from datetime import date, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from stardeck.charts import ChartType, Stack
from stardeck.chat import Assistant
from stardeck.dashboards import PRESETS, append_slot, new_widget_id, to_range
from stardeck.db import DashboardRecord, SourceRecord
from stardeck.deps import LLMDep, SessionDep, StateDep
from stardeck.llm import LLMClient, LLMError
from stardeck.query import QueryError, UnsafeSQLError, run
from stardeck.sources import ApiModel, as_utc
from stardeck.widgets import refit_widget

router = APIRouter(prefix="/dashboards", tags=["dashboards"])



class GridPos(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(ge=1, le=12)
    h: int = Field(ge=1)


class DateFilter(BaseModel):
    table: str
    column: str


class WidgetIn(ApiModel):
    title: str = Field(min_length=1, max_length=120)
    summary: str = ""
    sql: str = Field(min_length=1, max_length=20_000)
    spec: dict
    date_filter: DateFilter | None = None
    layout: GridPos | None = None


class WidgetPatch(ApiModel):
    title: str | None = Field(None, min_length=1, max_length=120)
    summary: str | None = None
    sql: str | None = Field(None, min_length=1, max_length=20_000)
    spec: dict | None = None  # full replacement, e.g. from the assistant's rebuilt widget
    chart_type: ChartType | None = None
    stack: Stack | None = None
    date_filter: DateFilter | None = None
    clear_date_filter: bool = False


class DashboardPatch(ApiModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    date_preset: str | None = None


class DashboardCreate(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    source_id: str
    widgets: list[WidgetIn] = Field(default_factory=list)


class SummaryOut(ApiModel):
    id: str
    name: str
    description: str
    source_id: str
    widget_count: int
    updated_at: datetime


class DashboardOut(ApiModel):
    id: str
    name: str
    description: str
    source_id: str
    date_preset: str
    updated_at: datetime
    widgets: list[dict]


class RunRequest(ApiModel):
    start: date | None = None
    end: date | None = None
    compare_start: date | None = None
    compare_end: date | None = None
    fresh: bool = False


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class ChatRequest(RunRequest):
    message: str = Field(min_length=1, max_length=2000)
    history: list[ChatTurn] = Field(default_factory=list)
    # The request the assistant asked a clarifying question about; the message is the answer.
    pending: str | None = Field(None, max_length=4000)


class GenerateRequest(ApiModel):
    source_id: str
    goal: str = Field("", max_length=500)


def _out(rec: DashboardRecord) -> DashboardOut:
    return DashboardOut(id=rec.id, name=rec.name, description=rec.description, source_id=rec.source_id,
                        date_preset=rec.date_preset, updated_at=as_utc(rec.updated_at), widgets=rec.widgets)


def _get(db, dashboard_id: str) -> DashboardRecord:
    rec = db.get(DashboardRecord, dashboard_id)
    if rec is None:
        raise HTTPException(404, f"Dashboard {dashboard_id} not found")
    return rec


def _source(db, source_id: str) -> SourceRecord:
    src = db.get(SourceRecord, source_id)
    if src is None:
        raise HTTPException(404, f"Data source {source_id} not found")
    return src


def _check_sql(state, src: SourceRecord, sql: str) -> None:
    """Refuse to save SQL that is unsafe or doesn't run."""
    try:
        run(state.sources.engine(src), sql, state.sources.dialect(src), state.settings.query_timeout_s, max_rows=1,
            prepared=state.sources.prepared(src))
    except (UnsafeSQLError, QueryError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get("", response_model=list[SummaryOut])
def list_dashboards(db: SessionDep):
    recs = db.query(DashboardRecord).order_by(DashboardRecord.updated_at.desc()).all()
    return [SummaryOut(id=r.id, name=r.name, description=r.description, source_id=r.source_id,
                       widget_count=len(r.widgets or []), updated_at=as_utc(r.updated_at)) for r in recs]


@router.post("", response_model=DashboardOut, status_code=201)
def create_dashboard(req: DashboardCreate, state: StateDep, db: SessionDep):
    src = _source(db, req.source_id)
    widgets = []
    for w in req.widgets:
        _check_sql(state, src, w.sql)
        d = w.model_dump(by_alias=True, mode="json") | {"id": new_widget_id()}
        d["layout"] = d["layout"] or append_slot(widgets, w.spec.get("type", "bar"))
        widgets.append(d)
    return _out(state.dashboards.create(db, req.name, req.description, src.id, widgets))


@router.get("/{dashboard_id}", response_model=DashboardOut)
def get_dashboard(dashboard_id: str, db: SessionDep):
    return _out(_get(db, dashboard_id))


@router.patch("/{dashboard_id}", response_model=DashboardOut)
def update_dashboard(dashboard_id: str, req: DashboardPatch, db: SessionDep):
    rec = _get(db, dashboard_id)
    if req.date_preset is not None and req.date_preset not in PRESETS:
        raise HTTPException(400, f"Unknown date preset {req.date_preset}")
    for field in ("name", "description", "date_preset"):
        if (v := getattr(req, field)) is not None:
            setattr(rec, field, v.strip() if isinstance(v, str) and field != "date_preset" else v)
    db.commit()
    return _out(rec)


@router.delete("/{dashboard_id}", status_code=204)
def delete_dashboard(dashboard_id: str, db: SessionDep):
    db.delete(_get(db, dashboard_id))
    db.commit()


@router.put("/{dashboard_id}/layout", response_model=DashboardOut)
def save_layout(dashboard_id: str, layout: dict[str, GridPos], state: StateDep, db: SessionDep):
    rec = _get(db, dashboard_id)
    widgets = [dict(w, layout=layout[w["id"]].model_dump()) if w["id"] in layout else w for w in rec.widgets]
    state.dashboards.save_widgets(db, rec, widgets)
    return _out(rec)


@router.post("/{dashboard_id}/widgets", response_model=DashboardOut, status_code=201)
def add_widget(dashboard_id: str, req: WidgetIn, state: StateDep, db: SessionDep):
    rec = _get(db, dashboard_id)
    _check_sql(state, _source(db, rec.source_id), req.sql)
    w = req.model_dump(by_alias=True, mode="json") | {"id": new_widget_id()}
    w["layout"] = append_slot(rec.widgets, req.spec.get("type", "bar"))
    state.dashboards.save_widgets(db, rec, [*rec.widgets, w])
    return _out(rec)


@router.patch("/{dashboard_id}/widgets/{widget_id}", response_model=DashboardOut)
def update_widget(dashboard_id: str, widget_id: str, req: WidgetPatch, state: StateDep, db: SessionDep, llm: LLMDep):
    rec = _get(db, dashboard_id)
    if not any(w["id"] == widget_id for w in rec.widgets):
        raise HTTPException(404, f"Widget {widget_id} not found")
    refit: dict = {}
    if req.sql is not None and req.spec is None:
        # Hand-edited SQL: run it and bring the chart, format, date filter and title in line with it.
        src = _source(db, rec.source_id)
        svc = state.sources
        try:
            result = run(svc.engine(src), req.sql, svc.dialect(src), state.settings.query_timeout_s,
                         prepared=svc.prepared(src))
        except (UnsafeSQLError, QueryError) as exc:
            raise HTTPException(400, str(exc)) from exc
        current = next(w for w in rec.widgets if w["id"] == widget_id)
        refit = refit_widget(current, req.sql, result, svc.tables(src), svc.effective_definitions(src), svc.dialect(src), llm)
    elif req.sql is not None:
        _check_sql(state, _source(db, rec.source_id), req.sql)
    widgets = []
    for w in rec.widgets:
        if w["id"] == widget_id:
            w = dict(w) | refit
            for field in ("title", "summary", "sql"):
                if (v := getattr(req, field)) is not None:
                    w[field] = v
            if req.spec is not None:
                w["spec"] = req.spec
            if req.chart_type:
                w["spec"] = {**w["spec"], "type": req.chart_type}
            if req.stack is not None:
                w["spec"] = {k: v for k, v in w["spec"].items() if k != "stack"}
                if req.stack != "none":
                    w["spec"]["stack"] = req.stack
            if req.date_filter:
                w["dateFilter"] = req.date_filter.model_dump()
            elif req.clear_date_filter:
                w["dateFilter"] = None
        widgets.append(w)
    state.dashboards.save_widgets(db, rec, widgets)
    return _out(rec)


@router.delete("/{dashboard_id}/widgets/{widget_id}", response_model=DashboardOut)
def remove_widget(dashboard_id: str, widget_id: str, state: StateDep, db: SessionDep):
    rec = _get(db, dashboard_id)
    state.dashboards.save_widgets(db, rec, [w for w in rec.widgets if w["id"] != widget_id])
    return _out(rec)


@router.post("/{dashboard_id}/run")
def run_dashboard(dashboard_id: str, req: RunRequest, state: StateDep, db: SessionDep) -> dict:
    """Run every widget with the given date range. Returns {widgetId: {result} | {error}}."""
    rec = _get(db, dashboard_id)
    src = _source(db, rec.source_id)
    return state.dashboards.run_all(src, rec.widgets, to_range(req.start, req.end),
                                    to_range(req.compare_start, req.compare_end), fresh=req.fresh)


@router.post("/generate")
def generate_dashboard(req: GenerateRequest, state: StateDep, db: SessionDep):
    """Stream NDJSON progress events while the AI plans and builds a dashboard."""
    _source(db, req.source_id)

    def stream():
        # Request-scoped dependencies are closed before streaming ends, so own the session and client.
        with state.db.new_session() as session:
            llm = LLMClient(state.settings)
            try:
                src = session.get(SourceRecord, req.source_id)
                for event in state.dashboards.generate(session, src, req.goal, llm):
                    yield json.dumps(event) + "\n"
            finally:
                llm.close()

    return StreamingResponse(stream(), media_type="application/x-ndjson")


@router.post("/{dashboard_id}/chat")
def chat(dashboard_id: str, req: ChatRequest, state: StateDep, db: SessionDep, llm: LLMDep) -> dict:
    """Interpret a message and return {text, proposal?}. Proposals are applied by the client after
    the user confirms, through the normal widget and definition endpoints."""
    rec = _get(db, dashboard_id)
    src = _source(db, rec.source_id)
    try:
        return Assistant(state.sources).respond(
            llm, rec, src, req.message, [h.model_dump() for h in req.history],
            to_range(req.start, req.end), to_range(req.compare_start, req.compare_end), pending=req.pending)
    except LLMError as exc:
        raise HTTPException(503, f"The AI model is not available: {exc}") from exc
