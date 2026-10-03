"""/api/sources/{id}/widgets/generate and /query: the widget engine over HTTP (M2)."""

from datetime import date

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field, model_validator

from stardeck.db import SourceRecord
from stardeck.deps import LLMDep, SessionDep, StateDep
from stardeck.llm import LLMError
from stardeck.query import DateRange, QueryError, UnsafeSQLError, run_widget_query
from stardeck.sources import ApiModel
from stardeck.widgets import WidgetError, generate_widget

router = APIRouter(prefix="/sources/{source_id}", tags=["widgets"])


class DateFilter(ApiModel):
    table: str
    column: str


class Range(ApiModel):
    start: date
    end: date

    @model_validator(mode="after")
    def ordered(self) -> "Range":
        if self.end < self.start:
            raise ValueError("end must not be before start")
        return self


class ResultOut(ApiModel):
    columns: list[str]
    rows: list[list]
    truncated: bool = False


class GenerateRequest(BaseModel):
    request: str = Field(min_length=3, max_length=1000)


class WidgetOut(ApiModel):
    title: str
    summary: str
    sql: str
    spec: dict
    date_filter: DateFilter | None
    result: ResultOut
    attempts: int
    notes: list[str]


class QueryRequest(ApiModel):
    sql: str = Field(min_length=1, max_length=20_000)
    kpi: bool = False
    date_filter: DateFilter | None = None
    range: Range | None = None
    compare: Range | None = None


def _source(db, source_id: str) -> SourceRecord:
    rec = db.get(SourceRecord, source_id)
    if rec is None:
        raise HTTPException(404, f"Data source {source_id} not found")
    return rec


@router.post("/widgets/generate", response_model=WidgetOut)
def generate(source_id: str, req: GenerateRequest, state: StateDep, db: SessionDep, llm: LLMDep):
    """Turn a plain-English request into a working widget (SQL, chart spec, summary, data)."""
    rec = _source(db, source_id)
    svc = state.sources
    try:
        w = generate_widget(llm, svc.engine(rec), svc.dialect(rec), svc.tables(rec), svc.effective_definitions(rec),
                            req.request, prepared=svc.prepared(rec), timeout_s=state.settings.query_timeout_s)
    except LLMError as exc:
        raise HTTPException(503, f"The AI model is not available: {exc}") from exc
    except WidgetError as exc:
        raise HTTPException(422, str(exc)) from exc
    return WidgetOut(
        title=w.title, summary=w.summary, sql=w.sql, spec=w.spec,
        date_filter=DateFilter(table=w.date_filter[0], column=w.date_filter[1]) if w.date_filter else None,
        result=ResultOut(columns=w.result.columns, rows=w.result.rows, truncated=w.result.truncated),
        attempts=w.attempts, notes=w.notes,
    )


@router.post("/query", response_model=ResultOut)
def query(source_id: str, req: QueryRequest, state: StateDep, db: SessionDep):
    """Run a widget's (possibly user-edited) SQL, applying the dashboard date range if given."""
    rec = _source(db, source_id)
    svc = state.sources
    as_range = lambda r: DateRange(r.start, r.end) if r else None
    try:
        res = run_widget_query(
            svc.engine(rec), req.sql, svc.dialect(rec), kpi=req.kpi,
            date_filter=(req.date_filter.table, req.date_filter.column) if req.date_filter else None,
            rng=as_range(req.range), compare=as_range(req.compare), timeout_s=state.settings.query_timeout_s,
            prepared=svc.prepared(rec),
        )
    except (UnsafeSQLError, QueryError) as exc:
        raise HTTPException(400, str(exc)) from exc
    return ResultOut(columns=res.columns, rows=res.rows, truncated=res.truncated)
