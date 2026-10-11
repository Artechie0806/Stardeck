"""/api/public: what works without signing in. Shared dashboards (by link) and the public demo.

Anyone can reach these, so they're narrow on purpose and rate limited per address:

- A shared dashboard comes without its SQL; results are what its charts show anyway.
- Filters are only the dashboard's saved filters (by id, with values) and clicks on its own charts,
  traced on the server from the chart's SQL as for signed-in people. Nothing from the browser
  becomes SQL.
- No AI: chart labels come from the cache or are shortened without it, and maps only use places
  already looked up.
- A server-wide cap on shared dashboards running at once protects the databases behind them.
"""

import threading
from datetime import date

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import Field

from stardeck import shares
from stardeck.api.maps import GeocodeRequest, ShortenRequest
from stardeck.context import Restriction, SourceContext
from stardeck.crossfilter import check_filter, trace_category
from stardeck.dashboards import source_ids, to_range, widget_source
from stardeck.db import DashboardRecord, SourceRecord
from stardeck.deps import StateDep
from stardeck.labels import shorten
from stardeck.query import QueryError, UnsafeSQLError
from stardeck.ratelimit import RateLimiter, client_key, too_many
from stardeck.sources import ApiModel, as_utc
from stardeck.streaming import Emit, relay

router = APIRouter(prefix="/public", tags=["public"])

# Per address: any call, and dashboard runs (each runs every chart's query).
CALLS_PER_IP = (300, 60)
RUNS_PER_IP = (40, 60)
# Server-wide: shared dashboards running at once, and how long one waits for its turn.
RUNS_AT_ONCE = threading.BoundedSemaphore(6)
RUN_WAIT_S = 10
limits = RateLimiter()

GONE = "This link doesn't work any more. Ask whoever shared it for a new one."
WIDGET_KEYS = ("id", "title", "summary", "spec", "layout", "dateFilter", "sourceId")
FILTER_KEYS = ("id", "sourceId", "table", "column", "label", "values")
Scalar = str | int | float | bool | None


def _limit(request: Request, *buckets: tuple[str, tuple[int, int]]) -> None:
    ip = client_key(request)
    for name, (limit, window) in buckets:
        if wait := limits.hit(f"{name}:{ip}", limit, window):
            raise too_many(wait, "requests")


def _open(state, token: str, count_view: bool = False) -> shares.OpenLink:
    link = shares.open_link(state.db, token, count_view)
    if link is None:
        raise HTTPException(404, GONE)
    return link


def _record(state, link: shares.OpenLink) -> DashboardRecord:
    with state.db.new_session(link.workspace_id) as db:
        rec = db.get(DashboardRecord, link.dashboard_id)
    if rec is None:
        raise HTTPException(404, GONE)
    return rec


def _contexts(state, link: shares.OpenLink, rec: DashboardRecord) -> dict[str, SourceContext]:
    with state.db.new_session(link.workspace_id) as db:
        srcs = [s for sid in source_ids(rec) if (s := db.get(SourceRecord, sid)) is not None]
        return {s.id: state.sources.context(s, link.principal) for s in srcs}


# ---------- shared dashboards ----------

@router.get("/s/{token}")
def shared_dashboard(token: str, request: Request, state: StateDep) -> dict:
    """The dashboard behind a link, without SQL, plus what the page needs to show it."""
    _limit(request, ("calls", CALLS_PER_IP))
    link = _open(state, token, count_view=True)
    rec = _record(state, link)
    s = state.settings
    return {
        "id": token, "name": rec.name, "description": rec.description, "sourceId": rec.source_id,
        "sourceIds": source_ids(rec), "datePreset": rec.date_preset, "dateRange": rec.date_range,
        "filters": [{k: f[k] for k in FILTER_KEYS if k in f} for f in rec.filters or []],
        "updatedAt": as_utc(rec.updated_at).isoformat(),
        "widgets": [{k: w[k] for k in WIDGET_KEYS if k in w} for w in rec.widgets or []],
        "share": {"badge": state.workspaces.plan(link.workspace_id).badge,
                  "demo": bool(s.demo_workspace_id) and link.workspace_id == s.demo_workspace_id,
                  "siteUrl": s.site_url},
        "config": {"fiscalYearStart": state.workspaces.fiscal_year_start(link.workspace_id),
                   "mapTiles": s.map_tiles_url, "mapAttribution": s.map_attribution,
                   "geocoding": state.geocoder.enabled},
    }


class SavedFilterPick(ApiModel):
    id: str = Field(max_length=80)
    values: list[Scalar] = Field(default_factory=list, max_length=500)


class ChartPick(ApiModel):
    widget_id: str = Field(max_length=80)
    column: str = Field(max_length=200)
    value: Scalar = None


class PublicRun(ApiModel):
    start: date | None = None
    end: date | None = None
    compare_start: date | None = None
    compare_end: date | None = None
    filters: list[SavedFilterPick] = Field(default_factory=list, max_length=12)
    picks: list[ChartPick] = Field(default_factory=list, max_length=10)


def _restrictions(rec: DashboardRecord, ctxs: dict[str, SourceContext],
                  req: PublicRun) -> tuple[list[Restriction], dict[str, str]]:
    """Saved filters with the values picked, and chart clicks traced from the chart's own SQL."""
    out: list[Restriction] = []
    owner: dict[str, str] = {}
    saved = {f["id"]: f for f in rec.filters or []}
    for pick in req.filters:
        f = saved.get(pick.id)
        ctx = ctxs.get(f["sourceId"]) if f else None
        if f is None or ctx is None:
            raise HTTPException(400, "That filter isn't on this dashboard.")
        if not pick.values:
            continue
        try:
            table = check_filter(ctx, f["table"], f["column"], None)
        except UnsafeSQLError as exc:
            raise HTTPException(400, "That filter doesn't work any more.") from exc
        out.append(Restriction(table=table, column=f["column"], values=tuple(pick.values), key=f["id"]))
        owner[f["id"]] = f["sourceId"]
    widgets = {w["id"]: w for w in rec.widgets or []}
    for pick in req.picks:
        w = widgets.get(pick.widget_id)
        sid = widget_source(rec, w) if w else None
        traced = trace_category(ctxs[sid], w["sql"], pick.column) if sid in ctxs else None
        if traced is None:
            raise HTTPException(400, "That chart can't filter the others.")
        key = f"x:{w['id']}"
        if "expr" in traced:
            out.append(Restriction(table=traced["table"], expr=traced["expr"], value=pick.value, key=key))
        else:
            out.append(Restriction(table=traced["table"], column=traced["column"], values=(pick.value,), key=key))
        owner[key] = sid
    return out, owner


@router.post("/s/{token}/run")
def run_shared(token: str, req: PublicRun, request: Request, state: StateDep) -> StreamingResponse:
    """Every chart with the dates and filters picked, as NDJSON lines {id, result|error} as each
    finishes, then {"done": true}. Cached results are used whenever there are any."""
    _limit(request, ("calls", CALLS_PER_IP), ("runs", RUNS_PER_IP))
    link = _open(state, token)
    rec = _record(state, link)
    ctxs = _contexts(state, link, rec)
    restrictions, owner = _restrictions(rec, ctxs, req)
    if not RUNS_AT_ONCE.acquire(timeout=RUN_WAIT_S):
        raise HTTPException(503, "Lots of people are looking at shared dashboards right now. Try again in a moment.",
                            headers={"Retry-After": "15"})
    widgets = list(rec.widgets or [])
    rng, compare = to_range(req.start, req.end), to_range(req.compare_start, req.compare_end)

    def work(emit: Emit, cancel: threading.Event) -> None:
        try:
            results = state.dashboards.run_all(rec, ctxs, widgets, rng, compare, restrictions, owner)
            try:
                for wid, payload in results:
                    if cancel.is_set():
                        return
                    # Errors can quote SQL or table names: the page only needs to know it failed.
                    emit({"id": wid, **({"error": "This chart couldn't load."} if "error" in payload else payload)})
                emit({"done": True})
            finally:
                results.close()
        finally:
            RUNS_AT_ONCE.release()

    return StreamingResponse(relay(work, name=f"shared-{token[:6]}"), media_type="application/x-ndjson")


@router.post("/s/{token}/crossfilter")
def shared_crossfilter(token: str, req: ChartPick, request: Request, state: StateDep) -> dict:
    """Whether clicking a chart filters the others; the page sends the click back with each run."""
    _limit(request, ("calls", CALLS_PER_IP))
    link = _open(state, token)
    rec = _record(state, link)
    w = next((x for x in rec.widgets or [] if x["id"] == req.widget_id), None)
    if w is None:
        raise HTTPException(404, "Chart not found")
    sid = widget_source(rec, w)
    ctxs = _contexts(state, link, rec)
    if sid not in ctxs or trace_category(ctxs[sid], w["sql"], req.column) is None:
        raise HTTPException(422, "This chart can't filter the others.")
    return {"key": f"x:{w['id']}", "sourceId": sid, "table": "", "column": req.column, "pick": req.column,
            "label": req.column.replace("_", " ").strip().capitalize(), "value": req.value, "values": []}


@router.get("/s/{token}/filter-values")
def shared_filter_values(token: str, request: Request, state: StateDep, filter_id: str = Query(alias="filter"),
                         q: str = Query("", max_length=100)) -> dict:
    """A saved filter's choices."""
    _limit(request, ("calls", CALLS_PER_IP))
    link = _open(state, token)
    rec = _record(state, link)
    f = next((x for x in rec.filters or [] if x["id"] == filter_id), None)
    ctxs = _contexts(state, link, rec)
    if f is None or f["sourceId"] not in ctxs:
        raise HTTPException(404, "That filter isn't on this dashboard.")
    ctx = ctxs[f["sourceId"]]
    try:
        values, more = state.sources.column_values(ctx, f["table"], f["column"], q)
    except (QueryError, UnsafeSQLError) as exc:
        raise HTTPException(400, "That filter doesn't work any more.") from exc
    cdef = ctx.defs.column(f["table"], f["column"])
    return {"values": values, "more": more, "labels": dict(cdef.values) if cdef and cdef.values else {}}


@router.post("/s/{token}/geocode")
def shared_geocode(token: str, req: GeocodeRequest, request: Request, state: StateDep) -> dict:
    """Map places from the cache only: a public page never sends lookups to the geocoding service."""
    _limit(request, ("calls", CALLS_PER_IP))
    link = _open(state, token)
    with state.db.new_session(link.workspace_id) as db:
        places, _ = state.geocoder.locate(db, req.places, lookup=False)
    return {"places": places, "pending": 0}


@router.post("/s/{token}/labels")
def shared_labels(token: str, req: ShortenRequest, request: Request, state: StateDep) -> dict:
    """Short chart labels from the cache, or shortened without the AI."""
    _limit(request, ("calls", CALLS_PER_IP))
    link = _open(state, token)
    with state.db.new_session(link.workspace_id) as db:
        mapping, _ = shorten(db, None, req.labels, req.max_chars)
    return {"labels": mapping, "ai": False}


@router.get("/demo")
def demo(request: Request, state: StateDep) -> dict:
    """The public demo's link (see `stardeck-admin demo setup`)."""
    _limit(request, ("calls", CALLS_PER_IP))
    ws = state.settings.demo_workspace_id
    token = shares.newest_link(state.db, ws) if ws else None
    if token is None:
        raise HTTPException(404, "There's no public demo right now.")
    return {"token": token}
