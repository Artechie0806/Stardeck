"""/api/sources/{id}/transforms: prepared tables (Power Query-style), previews and AI-suggested steps."""

from fastapi import APIRouter, HTTPException
from pydantic import Field, ValidationError

from stardeck.db import SourceRecord
from stardeck.deps import LLMDep, SessionDep, StateDep
from stardeck.llm import LLMError
from stardeck.query import QueryError, UnsafeSQLError, run
from stardeck.sources import ApiModel
from stardeck.transforms import (
    SUGGEST_PROMPT,
    Recipe,
    Step,
    StepDraft,
    TransformError,
    describe,
    draft_to_step,
    steps_adapter,
)

router = APIRouter(prefix="/sources/{source_id}/transforms", tags=["transforms"])
PREVIEW_ROWS = 100


class RecipeIn(ApiModel):
    name: str = "preview"
    base: str
    steps: list[Step] = Field(default_factory=list)
    # The prepared table being edited (excluded from the tables it can build on).
    editing: str | None = None


class PreviewIn(RecipeIn):
    upto: int | None = Field(None, ge=0, description="Only the first N steps (clicking a step in the list).")


class SaveIn(ApiModel):
    base: str
    steps: list[Step] = Field(default_factory=list)
    original_name: str | None = None


class DistinctIn(RecipeIn):
    upto: int = Field(ge=0)
    column: str


class SuggestIn(RecipeIn):
    request: str = Field(min_length=2, max_length=500)


def _source(db, source_id: str) -> SourceRecord:
    rec = db.get(SourceRecord, source_id)
    if rec is None:
        raise HTTPException(404, f"Data source {source_id} not found")
    return rec


def _summary(state, db, rec, entry: dict) -> dict:
    steps = steps_adapter.validate_python(entry["steps"])
    snap = entry.get("snapshot") or {}
    return {"name": entry["name"], "base": entry["base"], "steps": entry["steps"],
            "labels": [describe(s) for s in steps], "columns": entry.get("columns", []),
            "rowCount": snap.get("row_count"), "error": entry.get("error"),
            "usedBy": state.sources.widgets_using(db, rec, entry["name"])}


def _preview(state, rec, recipe: Recipe, upto: int | None, editing: str | None) -> dict:
    svc = state.sources
    labels = [describe(s) for s in recipe.steps]
    try:
        sql, history, compiled = svc.compile_recipe(rec, recipe, upto, editing=editing)
    except TransformError as exc:
        return {"error": str(exc), "errorStep": exc.step, "labels": labels, "columns": [], "rows": []}
    try:
        res = run(svc.engine(rec), sql, svc.dialect(rec), state.settings.query_timeout_s,
                  max_rows=PREVIEW_ROWS, prepared=compiled)
    except (QueryError, UnsafeSQLError) as exc:
        return {"error": f"The database rejected this step: {exc}", "errorStep": (upto or len(recipe.steps)) - 1,
                "labels": labels, "columns": [], "rows": [], "sql": sql}
    return {"columns": res.columns, "rows": res.rows, "truncated": res.truncated, "sql": sql,
            "stepColumns": history, "labels": [describe(s) for s in recipe.steps], "error": None, "errorStep": None}


@router.get("")
def list_transforms(source_id: str, state: StateDep, db: SessionDep) -> list[dict]:
    rec = _source(db, source_id)
    return [_summary(state, db, rec, t) for t in rec.transforms or []]


@router.post("/preview")
def preview(source_id: str, req: PreviewIn, state: StateDep, db: SessionDep) -> dict:
    rec = _source(db, source_id)
    return _preview(state, rec, Recipe(name=req.editing or req.name, base=req.base, steps=req.steps), req.upto, req.editing)


@router.put("/{name}")
def save(source_id: str, name: str, req: SaveIn, state: StateDep, db: SessionDep) -> dict:
    rec = _source(db, source_id)
    try:
        entry = state.sources.save_transform(db, rec, Recipe(name=name, base=req.base, steps=req.steps), req.original_name)
    except TransformError as exc:
        raise HTTPException(400, str(exc)) from exc
    state.dashboards.cache.drop_source(rec.id)  # cached widget results may read this table
    return _summary(state, db, rec, entry)


@router.delete("/{name}", status_code=204)
def delete(source_id: str, name: str, state: StateDep, db: SessionDep):
    rec = _source(db, source_id)
    try:
        state.sources.delete_transform(db, rec, name)
    except TransformError as exc:
        raise HTTPException(409, str(exc)) from exc
    state.dashboards.cache.drop_source(rec.id)


@router.post("/distinct")
def distinct(source_id: str, req: DistinctIn, state: StateDep, db: SessionDep) -> dict:
    """Up to 50 distinct values of a column at a point in the recipe (the pivot form's choices)."""
    rec = _source(db, source_id)
    recipe = Recipe(name=req.editing or req.name, base=req.base, steps=req.steps)
    try:
        values = state.sources.distinct_values(rec, recipe, req.upto, req.column, req.editing, limit=51)
    except TransformError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"values": [str(v) for v in values[:50]], "more": len(values) > 50}


@router.post("/suggest")
def suggest(source_id: str, req: SuggestIn, state: StateDep, db: SessionDep, llm: LLMDep) -> dict:
    """Turn a plain-English request into one validated step, with a preview of the result."""
    rec = _source(db, source_id)
    recipe = Recipe(name=req.editing or req.name, base=req.base, steps=req.steps)
    current = _preview(state, rec, recipe, None, req.editing)
    if current["error"]:
        raise HTTPException(400, f"Fix the current steps first: {current['error']}")
    cols = current["columns"]
    samples = {c: list(dict.fromkeys(str(r[i])[:30] for r in current["rows"] if r[i] is not None))[:5]
               for i, c in enumerate(cols)}
    defs = state.sources.effective_definitions(rec)

    def meaning(c: str) -> str:
        # Columns can arrive through merges, so look in the base table first, then any table.
        d = defs.column(req.base, c) or next(
            (t.columns[c] for t in defs.tables.values() if c in t.columns and t.columns[c].description), None)
        if not d or not (d.description or d.values):
            return ""
        codes = f" (codes: {', '.join(f'{k}={v}' for k, v in d.values.items())})" if d.values else ""
        return f" -- {d.description or ''}{codes}"
    column_lines = "\n".join(f"- {c}: {', '.join(samples[c]) or 'no values'}{meaning(c)}" for c in cols)
    others = [t for t in state.sources.tables(rec) if t.name not in (req.base, req.editing) and not t.error]
    table_lines = "\n".join(f"- {t.name}({', '.join(c.name for c in t.columns)})" for t in others) or "(none)"
    messages = [
        {"role": "system", "content": SUGGEST_PROMPT.format(columns=column_lines, tables=table_lines)},
        {"role": "user", "content": req.request},
    ]
    last_error = ""
    for _ in range(2):
        try:
            draft = llm.structured(messages, StepDraft)
        except LLMError as exc:
            raise HTTPException(503, f"The AI model is not available: {exc}") from exc
        if draft.op == "unclear":
            return {"question": (draft.question or "Could you describe the change in a bit more detail?").strip()}
        try:
            step = draft_to_step(draft, req.request)
            result = _preview(state, rec, Recipe(name=recipe.name, base=req.base, steps=[*req.steps, step]), None, req.editing)
            if result["error"]:
                raise TransformError(result["error"])
        except ValidationError as exc:
            e = exc.errors()[0]
            last_error = f"{'.'.join(str(x) for x in e['loc'][1:]) or 'step'}: {e['msg']}"
            messages += [{"role": "assistant", "content": draft.model_dump_json()},
                         {"role": "user", "content": f"That step doesn't work: {last_error}. Fix it."}]
            continue
        except TransformError as exc:
            last_error = str(exc).splitlines()[0]
            messages += [{"role": "assistant", "content": draft.model_dump_json()},
                         {"role": "user", "content": f"That step doesn't work: {last_error}. Fix it."}]
            continue
        return {"step": step.model_dump(), "label": describe(step), "preview": result}
    raise HTTPException(422, f"Couldn't turn that into a working step: {last_error}")
