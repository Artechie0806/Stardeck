"""Dashboards: storage, automatic layout, running widgets (with a cache), and AI generation."""

import json
import secrets
import threading
import time
from collections import OrderedDict
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from stardeck.db import DashboardRecord, SourceRecord, utcnow
from stardeck.llm import LLMClient, LLMError
from stardeck.query import DateRange, QueryError, QueryResult, UnsafeSQLError, run_widget_query
from stardeck.schema import render_schema
from stardeck.sources import SourceService
from stardeck.widgets import WidgetError, generate_widget, select_tables, usable_tables

DEMO_DASHBOARD = Path(__file__).parent / "demo" / "sales_overview.json"
PRESETS = {"last_30d", "last_90d", "this_year", "last_year", "all"}


def new_widget_id() -> str:
    return f"w-{secrets.token_hex(4)}"


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
- Use only what exists in the schema.

SCHEMA:
{schema}"""


# ---------- service ----------

class DashboardService:
    def __init__(self, sources: SourceService, cache: ResultCache):
        self.sources = sources
        self.cache = cache

    # ----- CRUD -----

    def create(self, db: Session, name: str, description: str, source_id: str, widgets: list[dict],
               date_preset: str = "all", dashboard_id: str | None = None) -> DashboardRecord:
        rec = DashboardRecord(
            id=dashboard_id or f"dash-{secrets.token_hex(4)}", name=name.strip()[:200] or "Untitled dashboard",
            description=description.strip(), source_id=source_id, date_preset=date_preset,
            widgets=widgets,
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

    def run_all(self, src: SourceRecord, widgets: list[dict], rng: DateRange | None,
                compare: DateRange | None, fresh: bool = False) -> dict[str, dict]:
        engine, dialect = self.sources.engine(src), self.sources.dialect(src)
        timeout = self.sources.settings.query_timeout_s
        prepared = self.sources.prepared(src)

        def one(w: dict) -> tuple[str, dict]:
            df = w.get("dateFilter")
            filt = (df["table"], df["column"]) if df else None
            kpi = w["spec"]["type"] == "kpi"
            key = (src.id, w["sql"], kpi, filt, rng and (rng.start, rng.end) if filt else None,
                   compare and (compare.start, compare.end) if filt and kpi else None)
            if not fresh and (hit := self.cache.get(key)) is not None:
                return w["id"], {"result": _result_json(hit)}
            try:
                res = run_widget_query(engine, w["sql"], dialect, kpi=kpi, date_filter=filt, prepared=prepared,
                                       rng=rng if filt else None, compare=compare if filt else None,
                                       timeout_s=timeout)
            except (UnsafeSQLError, QueryError) as exc:
                return w["id"], {"error": str(exc)}
            self.cache.put(key, res)
            return w["id"], {"result": _result_json(res)}

        with ThreadPoolExecutor(max_workers=min(8, max(1, len(widgets)))) as pool:
            return dict(pool.map(one, widgets))

    # ----- generation -----

    def generate(self, db: Session, src: SourceRecord, goal: str, llm: LLMClient) -> Iterator[dict]:
        """Plan a dashboard, build each widget, lay it out, save it. Yields progress events."""
        tables, defs = self.sources.tables(src), self.sources.effective_definitions(src)
        schema = render_schema(select_tables(usable_tables(tables, goal), defs, goal or "overview of everything"), defs)
        goal_clause = f" and the user's goal \"{goal.strip()}\"" if goal.strip() else ""
        try:
            plan = llm.structured([
                {"role": "system", "content": PLAN_PROMPT.format(goal_clause=goal_clause, schema=schema)},
                {"role": "user", "content": goal.strip() or "Build the most useful overview dashboard."},
            ], DashboardPlan)
        except LLMError as exc:
            yield {"type": "error", "message": f"The AI model is not available: {exc}"}
            return
        items = plan.widgets[:10]
        yield {"type": "plan", "name": plan.name, "titles": [w.title for w in items]}

        engine, dialect = self.sources.engine(src), self.sources.dialect(src)
        timeout = self.sources.settings.query_timeout_s
        prepared = self.sources.prepared(src)

        def build(i: int, item: PlannedWidget) -> tuple[int, dict | None, str | None]:
            try:
                w = generate_widget(llm, engine, dialect, tables, defs, f"{item.title}: {item.request}",
                                    prepared=prepared,
                                    timeout_s=timeout)
            except (WidgetError, LLMError) as exc:
                return i, None, str(exc)
            if not w.result.rows:
                return i, None, "No data to show."
            return i, {
                "id": new_widget_id(), "title": item.title.strip()[:80] or w.title, "summary": w.summary,
                "sql": w.sql, "spec": w.spec, "layout": {"x": 0, "y": 0, "w": 6, "h": 8},
                "dateFilter": {"table": w.date_filter[0], "column": w.date_filter[1]} if w.date_filter else None,
            }, None

        built: dict[int, dict] = {}
        # LM Studio / vLLM / Ollama serve a few requests in parallel; 3 keeps small GPUs responsive.
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(build, i, item) for i, item in enumerate(items)]
            for f in as_completed(futures):
                i, widget, error = f.result()
                if widget:
                    built[i] = widget
                    yield {"type": "widget", "index": i, "title": widget["title"]}
                else:
                    yield {"type": "widget_failed", "index": i, "reason": error}

        if not built:
            yield {"type": "error", "message": "None of the planned widgets could be built from this data."}
            return
        widgets = auto_layout([built[i] for i in sorted(built)])
        rec = self.create(db, plan.name, plan.description, src.id, widgets, date_preset="all")
        yield {"type": "done", "dashboardId": rec.id}

    def seed_demo(self, db: Session, source_id: str) -> None:
        if db.query(DashboardRecord.id).first() is not None:
            return
        data = json.loads(DEMO_DASHBOARD.read_text())
        self.create(db, data["name"], data["description"], source_id, data["widgets"],
                    date_preset=data.get("datePreset", "all"), dashboard_id=data["id"])


def _result_json(r: QueryResult) -> dict:
    return {"columns": r.columns, "rows": r.rows, "truncated": r.truncated}


def to_range(start: date | None, end: date | None) -> DateRange | None:
    return DateRange(start, end) if start and end else None
