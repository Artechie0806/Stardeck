"""Dashboards: storage, automatic layout, running widgets (with a cache), and AI generation."""

import json
import re
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from stardeck.context import Restriction, SourceContext
from stardeck.db import DashboardRecord, SourceRecord, utcnow
from stardeck.ids import new_id
from stardeck.llm import LLMClient, LLMError, ai_message
from stardeck.principal import Principal
from stardeck.query import DateRange, QueryError, QueryResult, UnsafeSQLError, run_widget_query
from stardeck.schema import render_schema
from stardeck.sources import SourceService
from stardeck.widgets import WidgetError, generate_widget, select_tables, usable_tables

DEMO_DASHBOARD = Path(__file__).parent / "demo" / "sales_overview.json"
PRESETS = {"last_7d", "last_30d", "last_90d", "last_12m", "this_month", "last_month", "this_quarter",
           "last_quarter", "this_year", "last_year", "this_fy", "last_fy", "all", "custom"}


def source_ids(rec: DashboardRecord) -> list[str]:
    """Every source a dashboard uses; the first is its main one."""
    ids = list(rec.source_ids or [])
    return ids if ids else [rec.source_id]


def widget_source(rec: DashboardRecord, w: dict) -> str:
    return w.get("sourceId") or rec.source_id


def new_widget_id() -> str:
    return new_id("w")


# ---------- layout ----------

def auto_layout(widgets: list[dict]) -> list[dict]:
    """Deterministic, readable layout: KPI row, trends, paired breakdowns, tables last."""
    kpis = [w for w in widgets if w["spec"]["type"] == "kpi"]
    trends = [w for w in widgets if w["spec"]["type"] in ("line", "area")]
    donuts = [w for w in widgets if w["spec"]["type"] == "donut"]
    bars = [w for w in widgets if w["spec"]["type"] in ("bar", "hbar")]
    tables = [w for w in widgets if w["spec"]["type"] == "table"]
    y = 0
    for row_start in range(0, len(kpis), 4):
        row = kpis[row_start:row_start + 4]
        width = 12 // len(row)
        for i, w in enumerate(row):
            w["layout"] = {"x": i * width, "y": y, "w": width, "h": 4}
        y += 4
    for t in trends:  # a trend shares its row with a donut when there is one
        side = donuts.pop(0) if donuts else None
        t["layout"] = {"x": 0, "y": y, "w": 8 if side else 12, "h": 8}
        if side:
            side["layout"] = {"x": 8, "y": y, "w": 4, "h": 8}
        y += 8
    pairs = bars + donuts
    for i in range(0, len(pairs), 2):
        row = pairs[i:i + 2]
        for j, w in enumerate(row):
            w["layout"] = {"x": j * 6, "y": y, "w": 6 if len(row) == 2 else 12, "h": 8}
        y += 8
    for t in tables:
        t["layout"] = {"x": 0, "y": y, "w": 12, "h": 8}
        y += 8
    order = kpis + trends + bars + [w for w in widgets if w["spec"]["type"] == "donut"] + tables
    return sorted(order, key=lambda w: (w["layout"]["y"], w["layout"]["x"]))


def append_slot(widgets: list[dict], chart_type: str) -> dict:
    bottom = max((w["layout"]["y"] + w["layout"]["h"] for w in widgets), default=0)
    if chart_type == "kpi":
        return {"x": 0, "y": bottom, "w": 3, "h": 4}
    return {"x": 0, "y": bottom, "w": 12 if chart_type == "table" else 6, "h": 8}


# ---------- result cache ----------

class ResultCache:
    """Small TTL + LRU cache so filter flips and reloads don't re-hit the database."""

    def __init__(self, ttl_s: int = 300, max_entries: int = 1000):
        self.ttl_s, self.max_entries = ttl_s, max_entries
        self._data: OrderedDict[tuple, tuple[float, QueryResult]] = OrderedDict()
        self._lock = threading.Lock()

    def get(self, key: tuple) -> QueryResult | None:
        with self._lock:
            hit = self._data.get(key)
            if not hit or hit[0] < time.monotonic():
                self._data.pop(key, None)
                return None
            self._data.move_to_end(key)
            return hit[1]

    def put(self, key: tuple, value: QueryResult) -> None:
        with self._lock:
            self._data[key] = (time.monotonic() + self.ttl_s, value)
            self._data.move_to_end(key)
            while len(self._data) > self.max_entries:
                self._data.popitem(last=False)

    def drop_source(self, source_id: str) -> None:
        with self._lock:
            for key in [k for k in self._data if k[0] == source_id]:
                del self._data[key]


# ---------- planner ----------

class PlannedWidget(BaseModel):
    title: str = Field(description="Short title, at most 6 words.")
    request: str = Field(description="Precise instruction for building this one widget's query and chart.")
    kind: str = Field(description="kpi, trend, breakdown, share or table")
    source: str = Field("", description="When there are several sources: the name of the one this widget uses.")


class DashboardPlan(BaseModel):
    reasoning: str = Field(description="1-3 sentences: what this data is about and what matters most.")
    name: str = Field(description="Dashboard name, at most 5 words.")
    description: str = Field(description="One plain-English sentence describing the dashboard.")
    widgets: list[PlannedWidget] = Field(min_length=3, max_length=10)


PLAN_PROMPT = """You design dashboards for non-technical business users.
Given the database below{goal_clause}, plan ONE dashboard of 6 to 9 widgets that answers the most
useful questions about this data.

Rules:
- Start with 3-4 KPIs (single headline numbers), then 1-2 trends over time, then 2-4 breakdowns by
  a category, and optionally one top-N table.
- Each widget answers a different question. Prefer measures the glossary defines.
- Write each `request` as a precise instruction (measure, grouping, sorting, top-N), e.g.
  "Total revenue from completed orders, by month". Never ask for a specific date range: the
  dashboard has a date filter.
- Use only what exists in the schema. Each widget uses one source{multi}.

SCHEMA:
{schema}"""


# ---------- service ----------

class DashboardService:
    def __init__(self, sources: SourceService, cache: ResultCache):
        self.sources = sources
        self.cache = cache

    # ----- CRUD -----

    def create(self, db: Session, name: str, description: str, source_ids: list[str], widgets: list[dict],
               date_preset: str = "all", dashboard_id: str | None = None, sample: bool = False) -> DashboardRecord:
        rec = DashboardRecord(
            id=dashboard_id or new_id("dash"), name=name.strip()[:200] or "Untitled dashboard",
            description=description.strip(), source_id=source_ids[0], source_ids=list(source_ids),
            date_preset=date_preset, widgets=widgets, filters=[], sample=sample,
            workspace_id=db.info.get("workspace_id"),
        )
        db.add(rec)
        db.commit()
        return rec

    @staticmethod
    def save_widgets(db: Session, rec: DashboardRecord, widgets: list[dict]) -> None:
        rec.widgets = widgets  # reassign so SQLAlchemy sees the JSON change
        rec.updated_at = utcnow()
        db.commit()

    # ----- running -----

    def run_all(self, rec: DashboardRecord, ctxs: dict[str, SourceContext], widgets: list[dict],
                rng: DateRange | None, compare: DateRange | None, filters: list[Restriction] | None = None,
                filter_sources: dict[str, str] | None = None, fresh: bool = False) -> Iterator[tuple[str, dict]]:
        """Run every widget against its own source, yielding (widget id, {result|error}) as each finishes.

        `filters` are dashboard filters; `filter_sources` maps each filter's key to its source, so a
        filter only reaches widgets of that source. A chart isn't filtered by its own click (key
        `x:<widget id>`): it keeps every bar and the page highlights the one picked, like Power BI.
        Results are cached per user rules and filters.
        """
        filters = filters or []
        filter_sources = filter_sources or {}

        def one(w: dict) -> tuple[str, dict]:
            sid = widget_source(rec, w)
            ctx = ctxs.get(sid)
            if ctx is None:
                return w["id"], {"error": "This widget's data source is no longer available."}
            own = f"x:{w['id']}"
            mine = [f for f in filters if filter_sources.get(f.key) == sid and f.key != own]
            df = w.get("dateFilter")
            filt = (df["table"], df["column"]) if df else None
            kpi = w["spec"]["type"] == "kpi"
            key = (sid, ctx.security_key, w["sql"], kpi, filt, rng and (rng.start, rng.end) if filt else None,
                   compare and (compare.start, compare.end) if filt and kpi else None,
                   tuple(sorted((f.key, f.table, f.column, tuple(map(str, f.values)), f.expr, str(f.value)) for f in mine)))
            others = sorted(f.key for f in filters if f.key != own and f.key not in {m.key for m in mine})
            if not fresh and (hit := self.cache.get(key)) is not None:
                return w["id"], _payload(hit, others)
            try:
                res = run_widget_query(ctx, w["sql"], kpi=kpi, date_filter=filt, filters=mine,
                                       rng=rng if filt else None, compare=compare if filt else None)
            except (UnsafeSQLError, QueryError) as exc:
                return w["id"], {"error": str(exc)}
            self.cache.put(key, res)
            return w["id"], _payload(res, others)

        if not widgets:
            return
        pool = ThreadPoolExecutor(max_workers=min(8, len(widgets)))
        try:
            for f in as_completed([pool.submit(one, w) for w in widgets]):
                yield f.result()
        finally:
            # If the browser went away mid-stream, don't wait for (or start) the rest.
            pool.shutdown(wait=False, cancel_futures=True)

    # ----- generation -----

    def generate(self, db: Session, srcs: list[SourceRecord], goal: str, llm: LLMClient,
                 who: Principal, before_save: Callable[[], str | None] | None = None,
                 cancel: threading.Event | None = None) -> Iterator[dict]:
        """Plan a dashboard over one or more sources, build each widget, lay it out, save it.

        `before_save` may refuse at the end (e.g. the plan's dashboards ran out meanwhile). `cancel`
        is set when the person went away: widgets not started are skipped and nothing is saved."""
        stopped = lambda: cancel is not None and cancel.is_set()
        ctxs = {src.id: self.sources.context(src, who) for src in srcs}
        focus = goal or "overview of everything"
        if len(srcs) == 1:
            c = ctxs[srcs[0].id]
            schema = render_schema(select_tables(usable_tables(c.tables, goal), c.defs, focus), c.defs)
        else:
            schema = "\n\n".join(
                f'SOURCE "{src.name}":\n' + render_schema(
                    select_tables(usable_tables(ctxs[src.id].tables, goal), ctxs[src.id].defs, focus,
                                  budget=max(3000, 12_000 // len(srcs))), ctxs[src.id].defs)
                for src in srcs)
        goal_clause = f" and the user's goal \"{goal.strip()}\"" if goal.strip() else ""
        multi = "; set `source` to its name" if len(srcs) > 1 else ""
        try:
            plan = llm.structured([
                {"role": "system", "content": PLAN_PROMPT.format(goal_clause=goal_clause, schema=schema, multi=multi)},
                {"role": "user", "content": goal.strip() or "Build the most useful overview dashboard."},
            ], DashboardPlan)
        except LLMError as exc:
            yield {"type": "error", "message": ai_message(exc)}
            return
        items = plan.widgets[:10]
        yield {"type": "plan", "name": plan.name, "titles": [w.title for w in items]}
        by_name = {src.name.lower(): src for src in srcs}

        def build(i: int, item: PlannedWidget) -> tuple[int, dict | None, str | None]:
            if stopped():
                return i, None, "Stopped."
            src = by_name.get(item.source.strip().lower()) or _best_source(srcs, ctxs, f"{item.title} {item.request}")
            try:
                w = generate_widget(llm, ctxs[src.id], f"{item.title}: {item.request}")
            except LLMError as exc:
                return i, None, ai_message(exc)
            except WidgetError as exc:
                return i, None, str(exc)
            if not w.result.rows:
                return i, None, "No data to show."
            return i, {
                "id": new_widget_id(), "sourceId": src.id, "title": item.title.strip()[:80] or w.title,
                "summary": w.summary, "sql": w.sql, "spec": w.spec, "layout": {"x": 0, "y": 0, "w": 6, "h": 8},
                "dateFilter": {"table": w.date_filter[0], "column": w.date_filter[1]} if w.date_filter else None,
            }, None

        built: dict[int, dict] = {}
        # LM Studio / vLLM / Ollama serve a few requests in parallel; 3 keeps small GPUs responsive.
        pool = ThreadPoolExecutor(max_workers=3)
        try:
            futures = [pool.submit(build, i, item) for i, item in enumerate(items)]
            for f in as_completed(futures):
                if stopped():
                    break
                i, widget, error = f.result()
                if widget:
                    built[i] = widget
                    yield {"type": "widget", "index": i, "title": widget["title"]}
                else:
                    yield {"type": "widget_failed", "index": i, "reason": error}
        finally:
            # The browser closed the page: drop the widgets not started yet instead of building them
            # for nobody (and never block the server waiting for them).
            pool.shutdown(wait=False, cancel_futures=True)

        if stopped():
            return
        if not built:
            yield {"type": "error", "message": "None of the planned widgets could be built from this data."}
            return
        widgets = auto_layout([built[i] for i in sorted(built)])
        if before_save and (problem := before_save()):
            yield {"type": "error", "message": problem}
            return
        rec = self.create(db, plan.name, plan.description, [src.id for src in srcs], widgets, date_preset="all")
        yield {"type": "done", "dashboardId": rec.id}

    def seed_sample(self, db: Session, source_id: str, fixed_ids: bool = False) -> DashboardRecord | None:
        """The sample "Sales overview" dashboard on the sample source (fresh workspaces only)."""
        if db.query(DashboardRecord.id).first() is not None:
            return None
        data = json.loads(DEMO_DASHBOARD.read_text())
        return self.create(db, data["name"], data["description"], [source_id], data["widgets"],
                           date_preset=data.get("datePreset", "all"), dashboard_id=data["id"] if fixed_ids else None,
                           sample=True)


def _result_json(r: QueryResult) -> dict:
    return {"columns": r.columns, "rows": r.rows, "truncated": r.truncated}


def _payload(r: QueryResult, other_filters: list[str]) -> dict:
    """A widget's result plus the dashboard filters that don't reach it (other sources, or unrelated)."""
    unaffected = sorted(set(r.unaffected) | set(other_filters))
    return {"result": _result_json(r), **({"unaffected": unaffected} if unaffected else {})}


def _best_source(srcs: list[SourceRecord], ctxs: dict[str, SourceContext], text: str) -> SourceRecord:
    """The source whose tables, columns and measures share the most words with `text`."""
    words = set(re.findall(r"[a-z0-9]+", text.lower()))

    def score(src: SourceRecord) -> int:
        c = ctxs[src.id]
        vocab = {w for t in c.tables for w in re.findall(r"[a-z0-9]+", f"{t.name} {' '.join(x.name for x in t.columns)}".lower())}
        vocab |= {w for m in c.defs.measures for w in re.findall(r"[a-z0-9]+", f"{m.name} {m.label or ''}".lower())}
        return len(words & vocab)

    return max(srcs, key=score)


def to_range(start: date | None, end: date | None) -> DateRange | None:
    return DateRange(start, end) if start and end else None
