"""/api/dashboards: saved dashboards, running them (with filters), the assistant, and AI generation."""

import json
import logging
import re
import threading
from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator

from stardeck.charts import ChartType, Stack
from stardeck.chat import Assistant, ChatScope
from stardeck.context import Restriction, SourceContext
from stardeck.crossfilter import check_filter, describe_value, trace_category
from stardeck.dashboards import (
    PRESETS,
    append_slot,
    new_widget_id,
    source_ids,
    to_range,
    widget_source,
)
from stardeck.db import DashboardRecord, SourceRecord
from stardeck.deps import EditorDep, LLMDep, PlanDep, SessionDep, StateDep, WhoDep, ai_error
from stardeck.llm import LLMError
from stardeck.plans import PlanLimit, check_room, check_sources_per_dashboard
from stardeck.principal import Principal
from stardeck.query import QueryError, UnsafeSQLError, run
from stardeck.sources import ApiModel, as_utc
from stardeck.streaming import Emit, relay
from stardeck.usage import real_dashboards
from stardeck.widgets import refit_widget

router = APIRouter(prefix="/dashboards", tags=["dashboards"])
log = logging.getLogger("stardeck.dashboards")
PING_EVERY_S = 10.0
FILTER_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,80}$")


class GridPos(BaseModel):
    x: int = Field(ge=0)
    y: int = Field(ge=0)
    w: int = Field(ge=1, le=12)
    h: int = Field(ge=1)


class DateFilter(BaseModel):
    table: str
    column: str


class DateRangeIn(BaseModel):
    start: date
    end: date


class WidgetIn(ApiModel):
    source_id: str | None = None  # defaults to the dashboard's main source
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


class FilterDef(ApiModel):
    """A filter (slicer) shown on the dashboard. `values` is its default selection (empty = all)."""

    id: str
    source_id: str
    table: str
    column: str
    label: str = Field("", max_length=80)
    values: list[Any] = Field(default_factory=list, max_length=500)

    @field_validator("id")
    @classmethod
    def _id(cls, v: str) -> str:
        if not FILTER_ID.match(v):
            raise ValueError("Bad filter id")
        return v


class DashboardPatch(ApiModel):
    name: str | None = Field(None, min_length=1, max_length=200)
    description: str | None = None
    date_preset: str | None = None
    date_range: DateRangeIn | None = None
    source_ids: list[str] | None = Field(None, min_length=1, max_length=10)
    filters: list[FilterDef] | None = Field(None, max_length=12)


class DashboardCreate(ApiModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    source_id: str | None = None
    source_ids: list[str] = Field(default_factory=list, max_length=10)
    widgets: list[WidgetIn] = Field(default_factory=list)


class SummaryOut(ApiModel):
    id: str
    name: str
    description: str
    source_id: str
    source_ids: list[str]
    widget_count: int
    updated_at: datetime


class DashboardOut(ApiModel):
    id: str
    name: str
    description: str
    source_id: str
    source_ids: list[str]
    date_preset: str
    date_range: dict | None = None
    filters: list[dict] = Field(default_factory=list)
    updated_at: datetime
    widgets: list[dict]


class ActiveFilter(ApiModel):
    """A filter in effect while viewing: a slicer selection (column + values) or a cross-filter
    (column = value, or expr = value for things like a clicked month)."""

    key: str
    source_id: str
    table: str
    column: str | None = None
    values: list[Any] = Field(default_factory=list, max_length=500)
    expr: str | None = Field(None, max_length=500)
    value: Any = None
    label: str = Field("", max_length=200)


class RunRequest(ApiModel):
    start: date | None = None
    end: date | None = None
    compare_start: date | None = None
    compare_end: date | None = None
    fresh: bool = False
    filters: list[ActiveFilter] = Field(default_factory=list, max_length=20)
    # Stream results as NDJSON lines as each widget finishes (the dashboard page), or one JSON object.
    stream: bool = False


class ChatTurn(BaseModel):
    role: Literal["user", "assistant"]
    text: str


class ChatRequest(RunRequest):
    message: str = Field(min_length=1, max_length=2000)
    history: list[ChatTurn] = Field(default_factory=list)
    # The request the assistant asked a clarifying question about; the message is the answer.
    pending: str | None = Field(None, max_length=4000)
    # Set by buttons that already know the agent (e.g. "Add as chart" on an analyst answer).
    agent: Literal["builder", "analyst"] | None = None
    # "Add as chart" on an analyst answer: the query behind it (and its source), so the chart matches.
    basis_sql: str | None = Field(None, max_length=4000)
    basis_source_id: str | None = None


class GenerateRequest(ApiModel):
    source_id: str | None = None
    source_ids: list[str] = Field(default_factory=list, max_length=10)
    goal: str = Field("", max_length=500)


class CrossFilterRequest(ApiModel):
    widget_id: str
    column: str
    value: Any = None


# ---------- helpers ----------

def _out(rec: DashboardRecord) -> DashboardOut:
    return DashboardOut(id=rec.id, name=rec.name, description=rec.description, source_id=rec.source_id,
                        source_ids=source_ids(rec), date_preset=rec.date_preset, date_range=rec.date_range,
                        filters=rec.filters or [], updated_at=as_utc(rec.updated_at), widgets=rec.widgets)


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


def _sources(db, rec: DashboardRecord) -> list[SourceRecord]:
    """The dashboard's sources that still exist, main source first."""
    return [s for sid in source_ids(rec) if (s := db.get(SourceRecord, sid)) is not None]


def _contexts(state, srcs: list[SourceRecord], who: Principal) -> dict[str, SourceContext]:
    return {s.id: state.sources.context(s, who) for s in srcs}


def _check_sql(ctx: SourceContext, sql: str) -> None:
    """Refuse to save SQL that is unsafe or doesn't run."""
    try:
        run(ctx, sql, max_rows=1)
    except (UnsafeSQLError, QueryError) as exc:
        raise HTTPException(400, str(exc)) from exc


def _restrictions(ctxs: dict[str, SourceContext], filters: list[ActiveFilter]) -> tuple[list[Restriction], dict[str, str], str]:
    """Validated filters as restrictions, the source each belongs to, and a plain-English note."""
    out: list[Restriction] = []
    owner: dict[str, str] = {}
    notes: list[str] = []
    for f in filters:
        ctx = ctxs.get(f.source_id)
        if ctx is None:
            raise HTTPException(400, "A filter refers to a data source this dashboard doesn't use.")
        try:
            table = check_filter(ctx, f.table, f.column, None if f.column else f.expr)
        except UnsafeSQLError as exc:
            raise HTTPException(400, str(exc)) from exc
        if f.column is not None:
            # A slicer sends `values`; a click on a chart sends one `value` (which may be null).
            values = list(f.values) or ([f.value] if "value" in f.model_fields_set else [])
            if not values:
                continue  # nothing selected = no filter
            out.append(Restriction(table=table, column=f.column, values=tuple(values), key=f.key))
            shown = ", ".join(describe_value(v) for v in values[:5]) + ("…" if len(values) > 5 else "")
        else:
            out.append(Restriction(table=table, expr=f.expr, value=f.value, key=f.key))
            shown = describe_value(f.value)
        owner[f.key] = f.source_id
        notes.append(f"{f.label or f.column or 'filter'} is {shown}")
    return out, owner, "; ".join(notes)


# ---------- dashboards ----------

@router.get("", response_model=list[SummaryOut])
def list_dashboards(db: SessionDep, who: WhoDep):
    recs = db.query(DashboardRecord).order_by(DashboardRecord.updated_at.desc()).all()
    return [SummaryOut(id=r.id, name=r.name, description=r.description, source_id=r.source_id,
                       source_ids=source_ids(r), widget_count=len(r.widgets or []), updated_at=as_utc(r.updated_at))
            for r in recs]


@router.post("", response_model=DashboardOut, status_code=201)
def create_dashboard(req: DashboardCreate, state: StateDep, db: SessionDep, who: EditorDep, plan: PlanDep):
    ids = list(dict.fromkeys([*([req.source_id] if req.source_id else []), *req.source_ids]))
    if not ids:
        raise HTTPException(400, "Pick at least one data source.")
    check_sources_per_dashboard(plan, len(ids))
    srcs = [_source(db, i) for i in ids]
    ctxs = _contexts(state, srcs, who)
    widgets: list[dict] = []
    for w in req.widgets:
        sid = w.source_id or ids[0]
        if sid not in ctxs:
            raise HTTPException(400, "A widget uses a data source the dashboard doesn't.")
        _check_sql(ctxs[sid], w.sql)
        d = w.model_dump(by_alias=True, mode="json") | {"id": new_widget_id(), "sourceId": sid}
        d["layout"] = d["layout"] or append_slot(widgets, w.spec.get("type", "bar"))
        widgets.append(d)
    with state.workspaces.lock(who.workspace_id):
        check_room(plan, "dashboards", real_dashboards(db))
        return _out(state.dashboards.create(db, req.name, req.description, ids, widgets))


@router.get("/{dashboard_id}", response_model=DashboardOut)
def get_dashboard(dashboard_id: str, db: SessionDep, who: WhoDep):
    return _out(_get(db, dashboard_id))


@router.patch("/{dashboard_id}", response_model=DashboardOut)
def update_dashboard(dashboard_id: str, req: DashboardPatch, state: StateDep, db: SessionDep, who: EditorDep,
                     plan: PlanDep):
    rec = _get(db, dashboard_id)
    if req.date_preset is not None and req.date_preset not in PRESETS:
        raise HTTPException(400, f"Unknown date preset {req.date_preset}")
    if req.date_preset == "custom" and req.date_range is None and not rec.date_range:
        raise HTTPException(400, "A custom date range needs a start and an end.")
    if req.date_range is not None:
        if req.date_range.end < req.date_range.start:
            raise HTTPException(400, "The end date is before the start date.")
        rec.date_range = {"start": req.date_range.start.isoformat(), "end": req.date_range.end.isoformat()}
    for field in ("name", "description", "date_preset"):
        if (v := getattr(req, field)) is not None:
            setattr(rec, field, v.strip() if isinstance(v, str) and field != "date_preset" else v)
    if req.source_ids is not None:
        ids = list(dict.fromkeys(req.source_ids))
        if len(ids) > len(source_ids(rec)):  # removing sources is always fine (e.g. after a downgrade)
            check_sources_per_dashboard(plan, len(ids))
        for sid in ids:
            _source(db, sid)
        used = {widget_source(rec, w) for w in rec.widgets or []} | {f["sourceId"] for f in rec.filters or []}
        if missing := used - set(ids):
            names = ", ".join(s.name for sid in missing if (s := db.get(SourceRecord, sid)))
            raise HTTPException(400, f"Charts or filters on this dashboard still use {names or 'that source'}.")
        rec.source_ids, rec.source_id = ids, ids[0]
    if req.filters is not None:
        ctxs = _contexts(state, _sources(db, rec), who)
        saved = []
        for f in req.filters:
            ctx = ctxs.get(f.source_id)
            if ctx is None:
                raise HTTPException(400, "A filter uses a data source this dashboard doesn't.")
            try:
                table = check_filter(ctx, f.table, f.column, None)
            except UnsafeSQLError as exc:
                raise HTTPException(400, str(exc)) from exc
            saved.append(f.model_dump(by_alias=True, mode="json") | {"table": table})
        rec.filters = saved
    db.commit()
    return _out(rec)


@router.delete("/{dashboard_id}", status_code=204)
def delete_dashboard(dashboard_id: str, db: SessionDep, who: EditorDep):
    db.delete(_get(db, dashboard_id))
    db.commit()


@router.put("/{dashboard_id}/layout", response_model=DashboardOut)
def save_layout(dashboard_id: str, layout: dict[str, GridPos], state: StateDep, db: SessionDep, who: EditorDep):
    rec = _get(db, dashboard_id)
    widgets = [dict(w, layout=layout[w["id"]].model_dump()) if w["id"] in layout else w for w in rec.widgets]
    state.dashboards.save_widgets(db, rec, widgets)
    return _out(rec)


# ---------- widgets ----------

@router.post("/{dashboard_id}/widgets", response_model=DashboardOut, status_code=201)
def add_widget(dashboard_id: str, req: WidgetIn, state: StateDep, db: SessionDep, who: EditorDep, plan: PlanDep):
    rec = _get(db, dashboard_id)
    sid = req.source_id or rec.source_id
    src = _source(db, sid)
    if sid not in source_ids(rec):
        check_sources_per_dashboard(plan, len(source_ids(rec)) + 1)
    _check_sql(state.sources.context(src, who), req.sql)
    if sid not in source_ids(rec):  # a chart from another source joins the dashboard
        rec.source_ids = [*source_ids(rec), sid]
    w = req.model_dump(by_alias=True, mode="json") | {"id": new_widget_id(), "sourceId": sid}
    w["layout"] = append_slot(rec.widgets, req.spec.get("type", "bar"))
    state.dashboards.save_widgets(db, rec, [*rec.widgets, w])
    return _out(rec)


@router.patch("/{dashboard_id}/widgets/{widget_id}", response_model=DashboardOut)
def update_widget(dashboard_id: str, widget_id: str, req: WidgetPatch, state: StateDep, db: SessionDep,
                  llm: LLMDep, who: EditorDep):
    rec = _get(db, dashboard_id)
    current = next((w for w in rec.widgets if w["id"] == widget_id), None)
    if current is None:
        raise HTTPException(404, f"Widget {widget_id} not found")
    ctx = state.sources.context(_source(db, widget_source(rec, current)), who)
    refit: dict = {}
    if req.sql is not None and req.spec is None:
        # Hand-edited SQL: run it and bring the chart, format, date filter and title in line with it.
        try:
            result = run(ctx, req.sql)
        except (UnsafeSQLError, QueryError) as exc:
            raise HTTPException(400, str(exc)) from exc
        refit = refit_widget(current, req.sql, result, ctx.tables, ctx.defs, ctx.dialect, llm,
                             relationships=ctx.relationships, measures=ctx.measures)
    elif req.sql is not None:
        _check_sql(ctx, req.sql)
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
def remove_widget(dashboard_id: str, widget_id: str, state: StateDep, db: SessionDep, who: EditorDep):
    rec = _get(db, dashboard_id)
    state.dashboards.save_widgets(db, rec, [w for w in rec.widgets if w["id"] != widget_id])
    return _out(rec)


# ---------- running, filters ----------

@router.post("/{dashboard_id}/run")
def run_dashboard(dashboard_id: str, req: RunRequest, state: StateDep, db: SessionDep, who: WhoDep):
    """Run every widget with the date range and filters, as `who` (their security rules apply).

    Returns {widgetId: {result|error, unaffected?}}, or with `stream` NDJSON lines {id, ...} as each
    widget finishes, then {"done": true}.
    """
    rec = _get(db, dashboard_id)
    ctxs = _contexts(state, _sources(db, rec), who)
    restrictions, owner, _ = _restrictions(ctxs, req.filters)
    results = state.dashboards.run_all(rec, ctxs, list(rec.widgets or []), to_range(req.start, req.end),
                                       to_range(req.compare_start, req.compare_end), restrictions, owner,
                                       fresh=req.fresh)
    if not req.stream:
        return dict(results)

    def lines():
        for wid, payload in results:
            yield json.dumps({"id": wid, **payload}) + "\n"
        yield json.dumps({"done": True}) + "\n"

    return StreamingResponse(lines(), media_type="application/x-ndjson")


@router.post("/{dashboard_id}/crossfilter")
def cross_filter(dashboard_id: str, req: CrossFilterRequest, state: StateDep, db: SessionDep, who: WhoDep) -> dict:
    """What clicking `value` in a widget's `column` filters: {key, sourceId, table, column|expr, value, label}."""
    rec = _get(db, dashboard_id)
    w = next((x for x in rec.widgets or [] if x["id"] == req.widget_id), None)
    if w is None:
        raise HTTPException(404, "Widget not found")
    src = _source(db, widget_source(rec, w))
    traced = trace_category(state.sources.context(src, who), w["sql"], req.column)
    if traced is None:
        raise HTTPException(422, "This chart can't filter the others.")
    label = req.column.replace("_", " ").strip().capitalize()
    return {"key": f"x:{w['id']}", "sourceId": src.id, "label": label, "value": req.value,
            "values": [], **traced}


@router.get("/{dashboard_id}/filter-values")
def filter_values(dashboard_id: str, source_id: str, table: str, column: str, state: StateDep, db: SessionDep,
                  who: WhoDep, q: str = "") -> dict:
    """A filter's choices: the column's distinct values the person may see, with documented meanings."""
    rec = _get(db, dashboard_id)
    if source_id not in source_ids(rec):
        raise HTTPException(400, "This dashboard doesn't use that data source.")
    ctx = state.sources.context(_source(db, source_id), who)
    try:
        values, more = state.sources.column_values(ctx, table, column, q)
    except (QueryError, UnsafeSQLError) as exc:
        raise HTTPException(400, str(exc)) from exc
    cdef = ctx.defs.column(table, column)
    return {"values": values, "more": more, "labels": dict(cdef.values) if cdef and cdef.values else {}}


# ---------- AI ----------

@router.post("/generate")
def generate_dashboard(req: GenerateRequest, state: StateDep, db: SessionDep, who: EditorDep, plan: PlanDep):
    """Stream NDJSON progress events while the AI plans and builds a dashboard."""
    ids = list(dict.fromkeys([*([req.source_id] if req.source_id else []), *req.source_ids]))
    if not ids:
        raise HTTPException(400, "Pick at least one data source.")
    for sid in ids:
        _source(db, sid)
    check_sources_per_dashboard(plan, len(ids))
    check_room(plan, "dashboards", real_dashboards(db))

    ws = who.workspace_id
    if why := state.generations.try_acquire(ws):  # one at a time per workspace, a few overall
        raise HTTPException(429, why, headers={"Retry-After": "30"})
    try:
        charge = state.quotas.charge(ws, "generation")
    except HTTPException:
        state.generations.release(ws)
        raise
    def work(emit: Emit, cancel: threading.Event) -> None:
        """Builds the dashboard in its own thread; the response only relays what it reports."""
        planned = False
        try:
            with state.db.new_session(ws) as session:
                def room() -> str | None:  # building takes a while: another dashboard may have been saved
                    try:
                        check_room(state.workspaces.plan(ws), "dashboards", real_dashboards(session))
                    except PlanLimit as exc:
                        return str(exc.detail)
                    return None

                llm = state.llm(background=True, cancel=cancel)
                try:
                    srcs = [session.get(SourceRecord, sid) for sid in ids]
                    for event in state.dashboards.generate(session, srcs, req.goal, llm, who,
                                                           before_save=room, cancel=cancel):
                        planned = planned or event["type"] == "plan"
                        emit(event)
                finally:
                    llm.close()
        except Exception:  # reported to the person; details in the log
            log.exception("Building a dashboard failed")
            emit({"type": "error", "message": "Something went wrong while building the dashboard."})
        finally:
            if not planned:  # the AI never got to it (offline, busy, page closed): it doesn't count
                state.quotas.refund(charge)
            state.generations.release(ws)

    # Closing the page stops the build: widgets not started yet are skipped and nothing is saved.
    return StreamingResponse(relay(work, name=f"generate-{ws}", ping_every_s=PING_EVERY_S),
                             media_type="application/x-ndjson")


@router.post("/{dashboard_id}/chat")
def chat(dashboard_id: str, req: ChatRequest, state: StateDep, db: SessionDep, llm: LLMDep, who: WhoDep) -> dict:
    """Interpret a message and return {text, proposal?}. Proposals are applied by the client after
    the user confirms, through the normal widget and definition endpoints. Viewers get the analyst."""
    rec = _get(db, dashboard_id)
    srcs = _sources(db, rec)
    if not srcs:
        raise HTTPException(400, "This dashboard's data sources were removed.")
    ctxs = _contexts(state, srcs, who)
    restrictions, owner, note = _restrictions(ctxs, req.filters)
    by_source: dict[str, list[Restriction]] = {}
    for r in restrictions:
        by_source.setdefault(owner[r.key], []).append(r)
    scope = ChatScope(sources=srcs, ctxs=ctxs, who=who, filters=by_source, filter_note=note)
    charge = state.quotas.charge(who.workspace_id, "assistant")  # one per message, whatever it takes inside
    try:
        return Assistant(state.sources).respond(
            llm, rec, scope, req.message, [h.model_dump() for h in req.history],
            to_range(req.start, req.end), to_range(req.compare_start, req.compare_end), pending=req.pending,
            agent=req.agent, basis_sql=req.basis_sql, basis_source=req.basis_source_id)
    except LLMError as exc:
        state.quotas.refund(charge)  # the AI couldn't answer: it doesn't count
        raise ai_error(exc) from exc
