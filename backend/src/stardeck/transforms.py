"""Prepared tables: Power Query-style recipes (base table + steps) compiled to read-only SQL.

A prepared table is never written to the user's database. Its recipe compiles to one SELECT,
and any query that mentions it gets that SELECT added as a CTE before running, so the data stays
live and works on every connector. Steps are compiled for the source's SQL dialect, and the
columns are tracked through every step so each one can be validated and the editor can offer
the right column names.
"""

import re
from typing import Annotated, Any, Literal

import sqlglot
from pydantic import BaseModel, Field, TypeAdapter
from sqlglot import exp

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,62}$")
PIVOT_MAX = 30  # most columns a pivot may create automatically
CHAIN_PREV = "__STARDECK_PREV__"  # placeholder for the previous CTE in multi-pass steps
MONTH_NAMES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


class TransformError(ValueError):
    """A step can't be applied; `step` is its index (or None for the recipe as a whole)."""

    def __init__(self, message: str, step: int | None = None):
        super().__init__(message)
        self.step = step


# ---------- steps ----------

class _Step(BaseModel):
    model_config = {"extra": "forbid"}


class SelectColumns(_Step):
    op: Literal["select_columns"] = "select_columns"
    columns: list[str] = Field(min_length=1)


class RemoveColumns(_Step):
    op: Literal["remove_columns"] = "remove_columns"
    columns: list[str] = Field(min_length=1)


class RenameColumn(_Step):
    op: Literal["rename_column"] = "rename_column"
    column: str
    to: str


Operator = Literal["eq", "ne", "gt", "gte", "lt", "lte", "contains", "starts_with", "in", "not_in",
                   "is_blank", "not_blank", "between"]


class FilterRows(_Step):
    op: Literal["filter_rows"] = "filter_rows"
    column: str
    operator: Operator
    value: Any = None
    value2: Any = None  # upper bound for "between"


class SortRows(_Step):
    op: Literal["sort"] = "sort"
    column: str
    descending: bool = False


class KeepTop(_Step):
    op: Literal["keep_top"] = "keep_top"
    n: int = Field(ge=1, le=100_000)


class RemoveDuplicates(_Step):
    op: Literal["remove_duplicates"] = "remove_duplicates"
    columns: list[str] = Field(default_factory=list, description="Empty = whole rows.")


class ChangeType(_Step):
    op: Literal["change_type"] = "change_type"
    column: str
    to: Literal["text", "integer", "decimal", "date"]


class ReplaceValues(_Step):
    op: Literal["replace_values"] = "replace_values"
    column: str
    mapping: dict[str, str] = Field(min_length=1, description="old value -> new value")


class FillBlanks(_Step):
    op: Literal["fill_blanks"] = "fill_blanks"
    column: str
    value: Any


class TextTransform(_Step):
    op: Literal["text_transform"] = "text_transform"
    column: str
    mode: Literal["trim", "upper", "lower"]


class CalculatedColumn(_Step):
    op: Literal["calculated_column"] = "calculated_column"
    name: str
    expression: str = Field(description="SQL expression over existing columns, e.g. amt / 100.0")


class DatePart(_Step):
    op: Literal["date_part"] = "date_part"
    column: str
    part: Literal["year", "quarter", "month", "month_name", "weekday", "day", "year_month"]
    name: str


class Aggregation(BaseModel):
    column: str | None = Field(None, description="Null only for count of rows.")
    func: Literal["sum", "avg", "count", "count_distinct", "min", "max"]
    name: str


class GroupBy(_Step):
    op: Literal["group_by"] = "group_by"
    by: list[str] = Field(default_factory=list)
    aggregations: list[Aggregation] = Field(min_length=1)


class Merge(_Step):
    op: Literal["merge"] = "merge"
    table: str
    left_on: str
    right_on: str
    how: Literal["inner", "left"] = "left"
    columns: list[str] = Field(min_length=1, description="Columns to bring from the other table.")
    prefix: str = ""


class Append(_Step):
    op: Literal["append"] = "append"
    table: str


class Unpivot(_Step):
    op: Literal["unpivot"] = "unpivot"
    columns: list[str] = Field(min_length=2)
    name_column: str = "attribute"
    value_column: str = "value"


class Pivot(_Step):
    """Rows -> columns: one new column per value of `column`, holding the aggregated `value_column`."""

    op: Literal["pivot"] = "pivot"
    column: str = Field(description="Its values become the new column names.")
    value_column: str = Field(description="Values to fill in (count counts its non-blank values).")
    func: Literal["sum", "avg", "count", "min", "max"] = "sum"
    # Which values become columns. Empty = every distinct value (filled in from the data, max 30).
    values: list[str] = Field(default_factory=list)
    # Column name per value, e.g. {"1": "retail"} when the codes have documented meanings.
    labels: dict[str, str] = Field(default_factory=dict)


class SplitColumn(_Step):
    op: Literal["split_column"] = "split_column"
    column: str
    delimiter: str = Field(min_length=1, max_length=10)
    mode: Literal["each", "first"] = Field("each", description="each delimiter, or only the first one")
    parts: int = Field(2, ge=2, le=10, description="Number of columns for mode=each.")
    names: list[str] = Field(default_factory=list, description="New column names; default column_1, column_2...")


class Rule(BaseModel):
    column: str
    operator: Operator
    value: Any = None
    value2: Any = None
    result: Any = None
    result_column: str | None = Field(None, description="Use this column's value instead of a fixed result.")


class ConditionalColumn(_Step):
    op: Literal["conditional_column"] = "conditional_column"
    name: str
    rules: list[Rule] = Field(min_length=1)
    otherwise: Any = None
    otherwise_column: str | None = None


Step = Annotated[
    SelectColumns | RemoveColumns | RenameColumn | FilterRows | SortRows | KeepTop | RemoveDuplicates
    | ChangeType | ReplaceValues | FillBlanks | TextTransform | CalculatedColumn | DatePart | GroupBy
    | Merge | Append | Unpivot | Pivot | SplitColumn | ConditionalColumn,
    Field(discriminator="op"),
]
steps_adapter: TypeAdapter[list[Step]] = TypeAdapter(list[Step])


class Recipe(BaseModel):
    name: str
    base: str
    steps: list[Step] = Field(default_factory=list)


def drops_rows(steps: list[Step]) -> bool:
    """Whether a recipe can leave out rows of its base table (so it's a subset, not a cleaned copy)."""
    return any(isinstance(st, (FilterRows, KeepTop, GroupBy, RemoveDuplicates, Unpivot, Pivot))
               or (isinstance(st, Merge) and st.how == "inner") for st in steps)


def describe(step: Step) -> str:
    """Power Query-style step label for the "applied steps" list."""
    match step:
        case SelectColumns(): return f"Kept columns: {', '.join(step.columns)}"
        case RemoveColumns(): return f"Removed {', '.join(step.columns)}"
        case RenameColumn(): return f"Renamed {step.column} to {step.to}"
        case FilterRows():
            words = {"eq": "=", "ne": "≠", "gt": ">", "gte": "≥", "lt": "<", "lte": "≤", "contains": "contains",
                     "starts_with": "starts with", "in": "is one of", "not_in": "is not one of",
                     "is_blank": "is blank", "not_blank": "is not blank", "between": "between"}
            tail = "" if step.operator in ("is_blank", "not_blank") else f" {step.value}"
            tail += f" and {step.value2}" if step.operator == "between" else ""
            return f"Filtered: {step.column} {words[step.operator]}{tail}"
        case SortRows(): return f"Sorted by {step.column} {'descending' if step.descending else 'ascending'}"
        case KeepTop(): return f"Kept top {step.n} rows"
        case RemoveDuplicates(): return "Removed duplicates" + (f" by {', '.join(step.columns)}" if step.columns else "")
        case ChangeType(): return f"Changed {step.column} to {step.to}"
        case ReplaceValues(): return f"Replaced values in {step.column}"
        case FillBlanks(): return f"Filled blanks in {step.column} with {step.value}"
        case TextTransform(): return f"{step.mode.capitalize()} text in {step.column}"
        case CalculatedColumn(): return f"Added {step.name} = {step.expression}"
        case DatePart(): return f"Added {step.name} ({step.part.replace('_', ' ')} of {step.column})"
        case GroupBy():
            aggs = ", ".join(f"{a.func.replace('_', ' ')} of {a.column or 'rows'}" for a in step.aggregations)
            return f"Grouped by {', '.join(step.by) or 'everything'}: {aggs}"
        case Merge(): return f"Merged {step.table} ({step.how} join on {step.left_on} = {step.right_on})"
        case Append(): return f"Appended {step.table}"
        case Unpivot(): return f"Unpivoted {len(step.columns)} columns into {step.name_column}/{step.value_column}"
        case Pivot():
            what = f"{'count' if step.func == 'count' else step.func} of {step.value_column}"
            n = f"{len(step.values)} " if step.values else ""
            return f"Pivoted {step.column} into {n}columns ({what})"
        case SplitColumn():
            how = f"first {step.delimiter!r}" if step.mode == "first" else f"each {step.delimiter!r} into {step.parts} columns"
            return f"Split {step.column} at {how}"
        case ConditionalColumn():
            return f"Added {step.name} ({len(step.rules)} rule{'s' if len(step.rules) > 1 else ''}, else {step.otherwise_column or step.otherwise})"
    return "Step"


# ---------- compilation ----------

class Compiler:
    """Turns a recipe into one SELECT for a dialect, tracking columns step by step."""

    def __init__(self, dialect: str, table_columns: dict[str, list[str]],
                 table_types: dict[str, dict[str, str]] | None = None):
        self.dialect = dialect
        self.table_columns = table_columns  # every table and prepared table name -> its columns
        # Column kinds ("number", "text", "date") per table, from the profiled types. Strict databases
        # (Postgres) reject comparing or mixing numbers and text, so steps generate type-correct SQL.
        self.table_kinds = {t: {c: kind_of(ty) for c, ty in cols.items()} for t, cols in (table_types or {}).items()}
        self.kinds: dict[str, str] = {}

    def q(self, name: str) -> str:
        return exp.to_identifier(name, quoted=True).sql(dialect=self.dialect)

    def kind(self, column: str) -> str:
        return self.kinds.get(column, "unknown")

    def value_for(self, column: str, value: Any, i: int) -> str:
        """A literal that suits the column's kind: numbers for number columns, quoted text otherwise."""
        k = self.kind(column)
        is_num = isinstance(value, (int, float)) and not isinstance(value, bool) or (
            isinstance(value, str) and re.fullmatch(r"-?\d+(\.\d+)?", value.strip()))
        if k == "number":
            if not is_num:
                raise TransformError(f"\"{column}\" holds numbers, so compare it with a number, not \"{value}\".", i)
            return self.lit(value)
        if k in ("text", "date"):
            return exp.Literal.string(str(value).strip()).sql(dialect=self.dialect)
        return self.lit(value)  # unknown: numbers look like numbers

    def lit(self, value: Any) -> str:
        if isinstance(value, bool):
            return "1" if value else "0"
        if isinstance(value, (int, float)):
            return repr(value)
        if isinstance(value, str) and re.fullmatch(r"-?\d+(\.\d+)?", value.strip()):
            return value.strip()  # numbers typed into a text box compare as numbers
        return exp.Literal.string(str(value)).sql(dialect=self.dialect)

    def _need(self, cols: list[str], *names: str, step: int) -> None:
        for n in names:
            if n not in cols:
                raise TransformError(f"Column \"{n}\" doesn't exist at this step (available: {', '.join(cols)}).", step)

    def _new_name(self, cols: list[str], name: str, step: int) -> str:
        if not NAME_RE.match(name):
            raise TransformError(f"\"{name}\" isn't a valid column name: use lowercase letters, digits and _.", step)
        if name in cols:
            raise TransformError(f"A column named \"{name}\" already exists.", step)
        return name

    def compile(self, recipe: Recipe, upto: int | None = None) -> tuple[str, list[list[str]]]:
        """SQL for the recipe (optionally only its first `upto` steps), and the columns after each step."""
        ctes, src, history = self.parts(recipe, upto)
        sql = f"WITH {', '.join(ctes)} SELECT * FROM {src}" if ctes else f"SELECT * FROM {src}"
        try:
            sqlglot.parse_one(sql, read=self.dialect)
        except sqlglot.errors.SqlglotError as exc:  # a bad calculated expression slipped through
            raise TransformError(f"The recipe produced invalid SQL: {str(exc).splitlines()[0]}") from exc
        return sql, history

    def distinct_sql(self, recipe: Recipe, upto: int, column: str, limit: int) -> str:
        """SELECT the distinct values of `column` as they are after the first `upto` steps."""
        ctes, src, history = self.parts(recipe, upto)
        if column not in history[-1]:
            raise TransformError(f"Column \"{column}\" doesn't exist at this step.", upto)
        c = self.q(column)
        body = f"SELECT DISTINCT {c} FROM {src} WHERE {c} IS NOT NULL ORDER BY 1 LIMIT {limit}"
        return f"WITH {', '.join(ctes)} {body}" if ctes else body

    def parts(self, recipe: Recipe, upto: int | None = None) -> tuple[list[str], str, list[list[str]]]:
        """The recipe's CTEs, the name of the last one (or the base table), and columns per step."""
        if recipe.base not in self.table_columns:
            raise TransformError(f"Table \"{recipe.base}\" doesn't exist.")
        cols = list(self.table_columns[recipe.base])
        self.kinds = dict(self.table_kinds.get(recipe.base, {}))
        history = [cols]
        steps = recipe.steps if upto is None else recipe.steps[:upto]
        # Each step is a named CTE reading the previous one: depth stays flat however many steps
        # there are (SQLite's parser overflows on deeply nested subqueries), and steps that read
        # their input more than once (unpivot) refer to it by name instead of copying it.
        src, ctes = self.q(recipe.base), []
        for i, step in enumerate(steps):
            step_sql, cols = self._apply(step, src, cols, i)
            if isinstance(step_sql, list):
                # A step that needs several passes: each SELECT reads the previous one via __PREV__.
                for j, part in enumerate(step_sql[:-1]):
                    name = self.q(f"{recipe.name}__{i + 1}_{j + 1}")
                    ctes.append(f"{name} AS ({part.replace(CHAIN_PREV, src)})")
                    src = name
                step_sql = step_sql[-1].replace(CHAIN_PREV, src)
            name = self.q(f"{recipe.name}__{i + 1}")
            ctes.append(f"{name} AS ({step_sql})")
            src = name
            history.append(cols)
        return ctes, src, history

    def _apply(self, step: Step, src: str, cols: list[str], i: int) -> tuple[str | list[str], list[str]]:
        """SQL for one step, reading the previous step's CTE (or the base table) `src`."""
        q, t = self.q, f"{src} AS t"
        select = lambda exprs: ", ".join(exprs)
        keep = [q(c) for c in cols]
        match step:
            case SelectColumns():
                self._need(cols, *step.columns, step=i)
                return f"SELECT {select(q(c) for c in step.columns)} FROM {t}", list(step.columns)
            case RemoveColumns():
                self._need(cols, *step.columns, step=i)
                rest = [c for c in cols if c not in step.columns]
                if not rest:
                    raise TransformError("Can't remove every column.", i)
                return f"SELECT {select(q(c) for c in rest)} FROM {t}", rest
            case RenameColumn():
                self._need(cols, step.column, step=i)
                new = self._new_name([c for c in cols if c != step.column], step.to, i)
                out = [f"{q(c)} AS {q(new)}" if c == step.column else q(c) for c in cols]
                self.kinds[new] = self.kinds.pop(step.column, "unknown")
                return f"SELECT {select(out)} FROM {t}", [new if c == step.column else c for c in cols]
            case FilterRows():
                self._need(cols, step.column, step=i)
                return f"SELECT * FROM {t} WHERE {self._condition(step, i)}", cols
            case SortRows():
                self._need(cols, step.column, step=i)
                return f"SELECT * FROM {t} ORDER BY {q(step.column)} {'DESC' if step.descending else 'ASC'}", cols
            case KeepTop():
                return f"SELECT * FROM {t} LIMIT {step.n}", cols
            case RemoveDuplicates():
                self._need(cols, *step.columns, step=i)
                if not step.columns or set(step.columns) == set(cols):
                    return f"SELECT DISTINCT * FROM {t}", cols
                # Keep the first row per key: group by the key, take MIN of everything else.
                out = [q(c) if c in step.columns else f"MIN({q(c)}) AS {q(c)}" for c in cols]
                return f"SELECT {select(out)} FROM {t} GROUP BY {select(q(c) for c in step.columns)}", cols
            case ChangeType():
                self._need(cols, step.column, step=i)
                out = [f"{self._cast(q(c), step.to)} AS {q(c)}" if c == step.column else q(c) for c in cols]
                self.kinds[step.column] = {"text": "text", "date": "date"}.get(step.to, "number")
                return f"SELECT {select(out)} FROM {t}", cols
            case ReplaceValues():
                self._need(cols, step.column, step=i)
                numeric = lambda v: bool(re.fullmatch(r"-?\d+(\.\d+)?", str(v).strip()))
                if self.kind(step.column) == "number" and all(numeric(v) for v in step.mapping.values()):
                    whens = " ".join(f"WHEN {self.lit(k)} THEN {self.lit(v)}" for k, v in step.mapping.items())
                    case_sql = f"CASE {q(step.column)} {whens} ELSE {q(step.column)} END"
                else:
                    # Labels for codes: compare and return text, so the CASE never mixes types.
                    as_text = f"CAST({q(step.column)} AS {self._text_type()})"
                    s_ = lambda v: exp.Literal.string(str(v)).sql(dialect=self.dialect)
                    whens = " ".join(f"WHEN {s_(k)} THEN {s_(v)}" for k, v in step.mapping.items())
                    case_sql = f"CASE {as_text} {whens} ELSE {as_text} END"
                    self.kinds[step.column] = "text"
                out = [f"{case_sql} AS {q(c)}" if c == step.column else q(c) for c in cols]
                return f"SELECT {select(out)} FROM {t}", cols
            case FillBlanks():
                self._need(cols, step.column, step=i)
                numeric = bool(re.fullmatch(r"-?\d+(\.\d+)?", str(step.value).strip()))
                if self.kind(step.column) == "number" and numeric:
                    filled = f"COALESCE({q(step.column)}, {self.lit(step.value)})"
                elif self.kind(step.column) in ("text", "unknown") and not numeric:
                    filled = f"COALESCE({q(step.column)}, {exp.Literal.string(str(step.value)).sql(dialect=self.dialect)})"
                else:  # e.g. "Unknown" into a number column: the column becomes text
                    filled = (f"COALESCE(CAST({q(step.column)} AS {self._text_type()}), "
                              f"{exp.Literal.string(str(step.value)).sql(dialect=self.dialect)})")
                    self.kinds[step.column] = "text"
                out = [f"{filled} AS {q(c)}" if c == step.column else q(c) for c in cols]
                return f"SELECT {select(out)} FROM {t}", cols
            case TextTransform():
                self._need(cols, step.column, step=i)
                fn = {"trim": "TRIM", "upper": "UPPER", "lower": "LOWER"}[step.mode]
                out = [f"{fn}({q(c)}) AS {q(c)}" if c == step.column else q(c) for c in cols]
                return f"SELECT {select(out)} FROM {t}", cols
            case CalculatedColumn():
                name = self._new_name(cols, step.name, i)
                expr = self._expression(step.expression, cols, i)
                self.kinds[name] = "unknown"
                return f"SELECT {select([*keep, f'{expr} AS {q(name)}'])} FROM {t}", [*cols, name]
            case DatePart():
                self._need(cols, step.column, step=i)
                name = self._new_name(cols, step.name, i)
                self.kinds[name] = "number" if step.part in ("year", "quarter", "month", "day") else "text"
                return f"SELECT {select([*keep, f'{self._date_part(q(step.column), step.part)} AS {q(name)}'])} FROM {t}", [*cols, name]
            case GroupBy():
                self._need(cols, *step.by, *[a.column for a in step.aggregations if a.column], step=i)
                names = list(step.by)
                aggs = []
                for a in step.aggregations:
                    names.append(self._new_name(names, a.name, i))
                    if a.column is None and a.func != "count":
                        raise TransformError(f"{a.func} needs a column.", i)
                    target = q(a.column) if a.column else "*"
                    self.kinds[a.name] = self.kind(a.column) if a.func in ("min", "max") and a.column else "number"
                    sql_fn = {"sum": f"SUM({target})", "avg": f"AVG({target})", "count": f"COUNT({target})",
                              "count_distinct": f"COUNT(DISTINCT {target})", "min": f"MIN({target})",
                              "max": f"MAX({target})"}[a.func]
                    aggs.append(f"{sql_fn} AS {q(a.name)}")
                group = f" GROUP BY {select(q(c) for c in step.by)}" if step.by else ""
                return f"SELECT {select([*(q(c) for c in step.by), *aggs])} FROM {t}{group}", names
            case Merge():
                other = self.table_columns.get(step.table)
                if other is None:
                    raise TransformError(f"Table \"{step.table}\" doesn't exist.", i)
                self._need(cols, step.left_on, step=i)
                if step.right_on not in other:
                    raise TransformError(f"\"{step.table}\" has no column \"{step.right_on}\".", i)
                for c in step.columns:
                    if c not in other:
                        raise TransformError(f"\"{step.table}\" has no column \"{c}\".", i)
                brought = []
                for c in step.columns:
                    brought.append(self._new_name([*cols, *brought], f"{step.prefix}{c}", i))
                join = "INNER JOIN" if step.how == "inner" else "LEFT JOIN"
                for c, b in zip(step.columns, brought):
                    self.kinds[b] = self.table_kinds.get(step.table, {}).get(c, "unknown")
                out = [f"t.{q(c)}" for c in cols] + [f"m.{q(c)} AS {q(b)}" for c, b in zip(step.columns, brought)]
                return (f"SELECT {select(out)} FROM {t} {join} {q(step.table)} AS m "
                        f"ON t.{q(step.left_on)} = m.{q(step.right_on)}"), [*cols, *brought]
            case Append():
                other = self.table_columns.get(step.table)
                if other is None:
                    raise TransformError(f"Table \"{step.table}\" doesn't exist.", i)
                missing = [c for c in cols if c not in other]
                if missing:
                    raise TransformError(f"\"{step.table}\" is missing columns: {', '.join(missing)}.", i)
                return (f"SELECT {select(keep)} FROM {t} UNION ALL "
                        f"SELECT {select(keep)} FROM {q(step.table)}"), cols
            case Unpivot():
                self._need(cols, *step.columns, step=i)
                rest = [c for c in cols if c not in step.columns]
                name_col = self._new_name(rest, step.name_column, i)
                value_col = self._new_name([*rest, name_col], step.value_column, i)
                self.kinds[name_col], self.kinds[value_col] = "text", self.kind(step.columns[0])
                parts = [f"SELECT {select([*(q(c) for c in rest), f'{self.lit(c)} AS {q(name_col)}', f'{q(c)} AS {q(value_col)}'])} FROM {t}"
                         for c in step.columns]
                return " UNION ALL ".join(parts), [*rest, name_col, value_col]
            case Pivot():
                return self._pivot(step, t, cols, i)
            case SplitColumn():
                return self._split(step, t, cols, i)
            case ConditionalColumn():
                return self._conditional(step, t, cols, i)
        raise TransformError("Unknown step.", i)

    # ----- pivot / split / conditional -----

    def _pivot(self, step: Pivot, t: str, cols: list[str], i: int) -> tuple[str, list[str]]:
        q = self.q
        self._need(cols, step.column, step=i)
        self._need(cols, step.value_column, step=i)
        if step.column == step.value_column:
            raise TransformError("The values column must be different from the column being pivoted.", i)
        if not step.values:
            raise TransformError(f"No values of \"{step.column}\" to turn into columns.", i)
        keep = [c for c in cols if c not in (step.column, step.value_column)]
        names: list[str] = []
        exprs = []
        for v in step.values:
            base = re.sub(r"[^a-z0-9]+", "_", str(step.labels.get(str(v), v)).lower()).strip("_") or "blank"
            name = base if base[0].isalpha() else f"v_{base}"
            n = 2
            while name in keep or name in names:
                name, n = f"{base}_{n}", n + 1
            names.append(name)
            match = f"{q(step.column)} = {self.value_for(step.column, v, i)}"
            exprs.append(f"{step.func.upper()}(CASE WHEN {match} THEN {q(step.value_column)} END) AS {q(name)}")
            self.kinds[name] = self.kind(step.value_column) if step.func in ("min", "max") else "number"
        group = f" GROUP BY {', '.join(q(c) for c in keep)}" if keep else ""
        return f"SELECT {', '.join([*(q(c) for c in keep), *exprs])} FROM {t}{group}", [*keep, *names]

    def _split(self, step: SplitColumn, t: str, cols: list[str], i: int) -> tuple[str | list[str], list[str]]:
        q = self.q
        self._need(cols, step.column, step=i)
        count = 2 if step.mode == "first" else step.parts
        names = list(step.names[:count]) + [f"{step.column}_{k}" for k in range(len(step.names) + 1, count + 1)]
        taken = list(cols)
        for k, n in enumerate(names):
            names[k] = self._new_name(taken, n, i)
            taken.append(names[k])
            self.kinds[names[k]] = "text"
        d = exp.Literal.string(step.delimiter).sql(dialect=self.dialect)
        before, after = self._split_fns(d)
        keep = [q(c) for c in cols]
        text_col = f"CAST({q(step.column)} AS {self._text_type()})"
        if step.mode == "first":
            exprs = [f"{before(text_col)} AS {q(names[0])}", f"{after(text_col)} AS {q(names[1])}"]
            return f"SELECT {', '.join([*keep, *exprs])} FROM {t}", [*cols, *names]
        if self.dialect not in ("sqlite", "mysql"):
            # SPLIT_PART returns '' for missing parts; make those NULL.
            exprs = [f"NULLIF(SPLIT_PART({text_col}, {d}, {k + 1}), '') AS {q(n)}" for k, n in enumerate(names)]
            return f"SELECT {', '.join([*keep, *exprs])} FROM {t}", [*cols, *names]
        # No split function: peel one part per pass, carrying the remainder in a helper column.
        # (Nesting the remainder expression instead would grow the SQL exponentially.)
        rest = q("__rest")
        chain = [f"SELECT {', '.join([*keep, f'{text_col} AS {rest}'])} FROM {CHAIN_PREV} AS t"]
        done: list[str] = []
        for n in names:
            chain.append(f"SELECT {', '.join([*keep, *(q(x) for x in done), f'{before(rest)} AS {q(n)}', f'{after(rest)} AS {rest}'])} "
                         f"FROM {CHAIN_PREV} AS t")
            done.append(n)
        chain.append(f"SELECT {', '.join([*keep, *(q(x) for x in names)])} FROM {CHAIN_PREV} AS t")
        return chain, [*cols, *names]

    def _split_fns(self, d: str):
        """(before first delimiter, after first delimiter) for a text expression; NULL when absent."""
        if self.dialect == "sqlite":
            pos = lambda e: f"INSTR({e}, {d})"
            return (lambda e: f"CASE WHEN {pos(e)} > 0 THEN SUBSTR({e}, 1, {pos(e)} - 1) ELSE {e} END",
                    lambda e: f"CASE WHEN {pos(e)} > 0 THEN SUBSTR({e}, {pos(e)} + LENGTH({d})) END")
        if self.dialect == "mysql":
            pos = lambda e: f"LOCATE({d}, {e})"
            return (lambda e: f"CASE WHEN {pos(e)} > 0 THEN SUBSTRING({e}, 1, {pos(e)} - 1) ELSE {e} END",
                    lambda e: f"CASE WHEN {pos(e)} > 0 THEN SUBSTRING({e}, {pos(e)} + CHAR_LENGTH({d})) END")
        pos = lambda e: f"POSITION({d} IN {e})"
        return (lambda e: f"CASE WHEN {pos(e)} > 0 THEN SUBSTRING({e} FROM 1 FOR {pos(e)} - 1) ELSE {e} END",
                lambda e: f"CASE WHEN {pos(e)} > 0 THEN SUBSTRING({e} FROM {pos(e)} + LENGTH({d})) END")

    def _conditional(self, step: ConditionalColumn, t: str, cols: list[str], i: int) -> tuple[str, list[str]]:
        q = self.q
        name = self._new_name(cols, step.name, i)
        outputs = [r.result for r in step.rules if not r.result_column] + ([] if step.otherwise_column else [step.otherwise])
        numeric = all(o is None or re.fullmatch(r"-?\d+(\.\d+)?", str(o).strip()) for o in outputs) and \
            not any(r.result_column for r in step.rules) and not step.otherwise_column

        def out(value: Any, column: str | None) -> str:
            if column:
                self._need(cols, column, step=i)
                return f"CAST({q(column)} AS {self._text_type()})" if not numeric else q(column)
            if value is None or value == "":
                return "NULL"
            return self.lit(value) if numeric else exp.Literal.string(str(value)).sql(dialect=self.dialect)

        whens = []
        for r in step.rules:
            self._need(cols, r.column, step=i)
            cond = self._condition(FilterRows(column=r.column, operator=r.operator, value=r.value, value2=r.value2), i)
            whens.append(f"WHEN {cond} THEN {out(r.result, r.result_column)}")
        case_sql = f"CASE {' '.join(whens)} ELSE {out(step.otherwise, step.otherwise_column)} END"
        self.kinds[name] = "number" if numeric else "text"
        return f"SELECT {', '.join([*(q(c) for c in cols), f'{case_sql} AS {q(name)}'])} FROM {t}", [*cols, name]

    def _condition(self, s: FilterRows, i: int) -> str:
        c = self.q(s.column)
        match s.operator:
            case "is_blank":
                return f"({c} IS NULL OR CAST({c} AS {self._text_type()}) = '')"
            case "not_blank":
                return f"({c} IS NOT NULL AND CAST({c} AS {self._text_type()}) <> '')"
            case "contains" | "starts_with":
                pattern = f"%{s.value}%" if s.operator == "contains" else f"{s.value}%"
                return f"LOWER(CAST({c} AS {self._text_type()})) LIKE LOWER({exp.Literal.string(pattern).sql(dialect=self.dialect)})"
            case "in" | "not_in":
                values = s.value if isinstance(s.value, list) else [v.strip() for v in str(s.value).split(",")]
                if not values:
                    raise TransformError("Give at least one value.", i)
                return f"{c} {'NOT IN' if s.operator == 'not_in' else 'IN'} ({', '.join(self.value_for(s.column, v, i) for v in values)})"
            case "between":
                if s.value is None or s.value2 is None:
                    raise TransformError("\"Between\" needs two values.", i)
                return f"{c} BETWEEN {self.value_for(s.column, s.value, i)} AND {self.value_for(s.column, s.value2, i)}"
        if s.value is None:
            raise TransformError("This filter needs a value.", i)
        sym = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[s.operator]
        return f"{c} {sym} {self.value_for(s.column, s.value, i)}"

    def _text_type(self) -> str:
        return "CHAR" if self.dialect == "mysql" else "TEXT"

    def _cast(self, col: str, to: str) -> str:
        d = self.dialect
        if to == "date":
            return f"DATE({col})" if d == "sqlite" else f"CAST({col} AS DATE)"
        types = {"text": {"mysql": "CHAR"}, "integer": {"sqlite": "INTEGER", "mysql": "SIGNED"},
                 "decimal": {"sqlite": "REAL", "mysql": "DECIMAL(18, 4)"}}
        default = {"text": "TEXT", "integer": "BIGINT", "decimal": "NUMERIC"}[to]
        return f"CAST({col} AS {types[to].get(d, default)})"

    def _date_part(self, col: str, part: str) -> str:
        d = self.dialect
        if d == "sqlite":
            m = f"CAST(STRFTIME('%m', {col}) AS INTEGER)"
            month_whens = " ".join(f"WHEN {n} THEN '{name}'" for n, name in enumerate(MONTH_NAMES, 1))
            return {
                "year": f"CAST(STRFTIME('%Y', {col}) AS INTEGER)", "month": m,
                "day": f"CAST(STRFTIME('%d', {col}) AS INTEGER)", "quarter": f"(({m} + 2) / 3)",
                "year_month": f"STRFTIME('%Y-%m', {col})",
                "month_name": f"CASE {m} {month_whens} END",
                "weekday": f"CASE STRFTIME('%w', {col}) WHEN '0' THEN 'Sun' WHEN '1' THEN 'Mon' WHEN '2' THEN 'Tue' "
                           "WHEN '3' THEN 'Wed' WHEN '4' THEN 'Thu' WHEN '5' THEN 'Fri' ELSE 'Sat' END",
            }[part]
        if d == "mysql":
            return {"year": f"YEAR({col})", "month": f"MONTH({col})", "day": f"DAY({col})", "quarter": f"QUARTER({col})",
                    "year_month": f"DATE_FORMAT({col}, '%Y-%m')", "month_name": f"DATE_FORMAT({col}, '%b')",
                    "weekday": f"DATE_FORMAT({col}, '%a')"}[part]
        return {"year": f"CAST(EXTRACT(YEAR FROM {col}) AS INTEGER)", "month": f"CAST(EXTRACT(MONTH FROM {col}) AS INTEGER)",
                "day": f"CAST(EXTRACT(DAY FROM {col}) AS INTEGER)", "quarter": f"CAST(EXTRACT(QUARTER FROM {col}) AS INTEGER)",
                "year_month": f"TO_CHAR({col}, 'YYYY-MM')", "month_name": f"TO_CHAR({col}, 'Mon')",
                "weekday": f"TO_CHAR({col}, 'Dy')"}[part]

    _ALLOWED = (exp.Column, exp.Literal, exp.Paren, exp.Binary, exp.Unary, exp.Case, exp.If, exp.Cast,
                exp.Coalesce, exp.Round, exp.Abs, exp.Lower, exp.Upper, exp.Trim, exp.Length, exp.Substring,
                exp.Concat, exp.DPipe, exp.Null, exp.Boolean, exp.DataType, exp.Identifier, exp.Floor, exp.Ceil,
                exp.Not, exp.Is, exp.In, exp.Between, exp.Like, exp.ILike, exp.Nullif, exp.Greatest, exp.Least,
                exp.Anonymous, exp.Var)

    def _expression(self, text: str, cols: list[str], i: int) -> str:
        """Validate a calculated-column expression: plain row-level SQL over existing columns."""
        try:
            tree = sqlglot.parse_one(text, read=self.dialect)
        except sqlglot.errors.SqlglotError as exc:
            raise TransformError(f"Can't read the expression: {str(exc).splitlines()[0]}", i) from exc
        for node in tree.walk():
            if isinstance(node, (exp.Select, exp.Subquery, exp.AggFunc, exp.Window)):
                raise TransformError("Calculated columns work row by row: no subqueries, totals or windows "
                                     "(use a Group by step for totals).", i)
            if not isinstance(node, self._ALLOWED) and not isinstance(node, exp.Func):
                raise TransformError(f"\"{node.sql(dialect=self.dialect)}\" isn't allowed in a calculated column.", i)
        by_lower = {c.lower(): c for c in cols}
        for column in tree.find_all(exp.Column):
            real = by_lower.get(column.name.lower())
            if not real:
                raise TransformError(f"Column \"{column.name}\" doesn't exist at this step.", i)
            column.replace(exp.column(real, quoted=True))
        return tree.sql(dialect=self.dialect)


def kind_of(sql_type: str) -> str:
    t = sql_type.upper()
    if re.search(r"INT|REAL|NUM|DEC|FLOAT|DOUBLE|MONEY|SERIAL", t):
        return "number"
    if re.search(r"DATE|TIME", t):
        return "date"
    return "text" if re.search(r"CHAR|TEXT|STRING|CLOB|UUID|ENUM|BOOL", t) else "unknown"


# ---------- using prepared tables in queries ----------

def referenced_tables(sql: str, dialect: str) -> set[str]:
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except sqlglot.errors.SqlglotError:
        return set()
    ctes = {c.alias_or_name for c in tree.find_all(exp.CTE)}
    return {t.name for t in tree.find_all(exp.Table) if t.name not in ctes}


def with_prepared(sql: str, dialect: str, prepared: dict[str, str]) -> str:
    """Prefix `sql` with CTEs for every prepared table it uses (dependencies first)."""
    if not prepared:
        return sql
    needed: list[str] = []

    def visit(name: str, trail: tuple[str, ...]) -> None:
        if name in needed:
            return
        if name in trail:
            raise TransformError(f"Prepared tables refer to each other in a loop: {' -> '.join((*trail, name))}.")
        for dep in sorted(referenced_tables(prepared[name], dialect) & prepared.keys()):
            visit(dep, (*trail, name))
        needed.append(name)

    for name in sorted(referenced_tables(sql, dialect) & prepared.keys()):
        visit(name, ())
    if not needed:
        return sql
    tree = sqlglot.parse_one(sql, read=dialect)
    key = "with_" if "with_" in tree.arg_types else "with"
    ctes: list[exp.CTE] = []
    for n in needed:
        body = sqlglot.parse_one(prepared[n], read=dialect)
        # Lift the recipe's own step CTEs into the outer WITH, so nothing is nested.
        inner = body.args.get(key)
        if inner is not None:
            ctes += list(inner.expressions)
            body.set(key, None)
        ctes.append(exp.CTE(this=body, alias=exp.TableAlias(this=exp.to_identifier(n))))
    existing = tree.args.get(key)
    tree.set(key, exp.With(expressions=[*ctes, *(existing.expressions if existing else [])],
                           recursive=bool(existing and existing.args.get("recursive"))))
    return tree.sql(dialect=dialect)


# ---------- AI-suggested steps ----------

class ValueMap(BaseModel):
    old: str
    new: str


class StepDraft(BaseModel):
    """Flat form of a step that small models fill in reliably; converted to a real Step afterwards."""

    reasoning: str = Field(description="One sentence: what the user wants and which step does it.")
    op: Literal["select_columns", "remove_columns", "rename_column", "filter_rows", "sort", "keep_top",
                "remove_duplicates", "change_type", "replace_values", "fill_blanks", "text_transform",
                "calculated_column", "date_part", "group_by", "merge", "append", "unpivot", "pivot",
                "split_column", "conditional_column", "unclear"]
    column: str | None = None
    columns: list[str] = Field(default_factory=list)
    to: str | None = Field(None, description="rename_column: new name. change_type: text|integer|decimal|date.")
    operator: Operator | None = None
    value: str | None = None
    value2: str | None = Field(None, description="Only the upper bound for between.")
    values: list[str] = Field(default_factory=list, description="filter in / not_in: every value to keep or drop.")
    mapping: list[ValueMap] = Field(default_factory=list)
    name: str | None = Field(None, description="Name of the new column (snake_case).")
    expression: str | None = None
    part: Literal["year", "quarter", "month", "month_name", "weekday", "day", "year_month"] | None = None
    by: list[str] = Field(default_factory=list)
    aggregations: list[Aggregation] = Field(default_factory=list)
    table: str | None = None
    left_on: str | None = None
    right_on: str | None = None
    how: Literal["inner", "left"] = "left"
    n: int | None = None
    descending: bool = False
    mode: Literal["trim", "upper", "lower"] | None = None
    func: Literal["sum", "avg", "count", "min", "max"] | None = Field(None, description="pivot: how to combine values.")
    delimiter: str | None = None
    split_mode: Literal["each", "first"] | None = None
    parts: int | None = None
    names: list[str] = Field(default_factory=list)
    rules: list[Rule] = Field(default_factory=list)
    otherwise: str | None = None
    name_column: str | None = None
    value_column: str | None = None
    question: str | None = Field(None, description="op=unclear: one short question for the user.")


_FIELDS = {
    "select_columns": ["columns"], "remove_columns": ["columns"], "rename_column": ["column", "to"],
    "filter_rows": ["column", "operator", "value", "value2"], "sort": ["column", "descending"], "keep_top": ["n"],
    "remove_duplicates": ["columns"], "change_type": ["column", "to"], "replace_values": ["column", "mapping"],
    "fill_blanks": ["column", "value"], "text_transform": ["column", "mode"],
    "calculated_column": ["name", "expression"], "date_part": ["column", "part", "name"],
    "group_by": ["by", "aggregations"], "merge": ["table", "left_on", "right_on", "how", "columns"],
    "append": ["table"], "unpivot": ["columns", "name_column", "value_column"],
    "pivot": ["column", "value_column", "func", "values"], "split_column": ["column", "delimiter", "mode", "parts", "names"],
    "conditional_column": ["name", "rules", "otherwise"],
}


def draft_to_step(draft: StepDraft, request: str = "") -> Step:
    if draft.op == "filter_rows" and draft.operator in ("in", "not_in"):
        # Small models sometimes spread a list over value/value2; gather everything.
        items = [*draft.values, *(v for v in (draft.value, draft.value2) if v)]
        draft = draft.model_copy(update={"value": ", ".join(dict.fromkeys(i.strip() for i in items if i.strip())),
                                         "value2": None})
    if draft.op == "pivot":
        # Models invent value lists; take every value from the data (the form can narrow it).
        draft = draft.model_copy(update={"values": [], "func": _func_from_words(request) or draft.func or "sum"})
    data: dict[str, Any] = {"op": draft.op}
    for field in _FIELDS[draft.op]:
        if draft.op == "split_column" and field == "mode":
            value = draft.split_mode  # `mode` means text case for clean-text steps
        else:
            value = getattr(draft, field)
        if field == "mapping":
            value = {m.old: m.new for m in draft.mapping}
        if value is not None and value != [] and value != {}:
            data[field] = value
    if draft.op == "unpivot":
        data.setdefault("name_column", "attribute")
        data.setdefault("value_column", "value")
    return TypeAdapter(Step).validate_python(data)


def _func_from_words(request: str) -> str | None:
    """The user's own word for the aggregation beats the model's guess."""
    r = request.lower()
    for pattern, func in ((r"\baverage|\bavg\b|\bmean\b", "avg"), (r"\bcount\b|\bnumber of\b|how many", "count"),
                          (r"\bmin(imum)?\b|\blowest\b|\bsmallest\b", "min"), (r"\bmax(imum)?\b|\bhighest\b|\blargest\b", "max"),
                          (r"\btotal\b|\bsum\b", "sum")):
        if re.search(pattern, r):
            return func
    return None


SUGGEST_PROMPT = """You help a non-technical user clean and reshape a table, like Power Query.
Turn their request into ONE step. Steps (op):
- select_columns / remove_columns (columns)
- rename_column (column, to = new name)
- filter_rows (column, operator: eq ne gt gte lt lte contains starts_with in not_in is_blank not_blank
  between, value; value2 only for between; for in/not_in put every value in `values`).
  Use values exactly as they appear in the data samples.
- sort (column, descending) / keep_top (n)
- remove_duplicates (columns, empty = whole rows)
- change_type (column, to: text|integer|decimal|date)
- replace_values (column, mapping: old -> new), e.g. codes to labels
- fill_blanks (column, value) / text_transform (column, mode: trim|upper|lower)
- calculated_column (name, expression in SQL over existing columns, e.g. amt / 100.0, or CASE WHEN)
- date_part (column, part: year|quarter|month|month_name|weekday|day|year_month, name)
- group_by (by: columns, aggregations: [{{column, func: sum|avg|count|count_distinct|min|max, name}}])
- merge (table, left_on, right_on, how: left|inner, columns to bring from the other table)
- append (table with the same columns) / unpivot (columns, name_column, value_column)
- pivot: turn the values of `column` into new columns, filled with `func` (sum|avg|count|min|max)
  of `value_column` (e.g. one column per city with summed revenue). Leave `values` empty for all.
- split_column (column, delimiter, split_mode: each|first, parts for each, optional names).
  To break a date like 2025-03-14 into year, month and day in one step: split_column on "-" with
  parts 3 and names year, month, day. (date_part adds one part at a time.)
- conditional_column (name, rules: [{{column, operator, value[, value2], result}}], otherwise):
  like "if amount > 10000 then High, if amount > 5000 then Medium, otherwise Low". Rules are
  checked in order; the first match wins.
- unclear: the request can't be done in one step or is ambiguous; ask a short `question`.
New column names: lowercase snake_case. Use only existing column and table names.
Follow the column meanings and units below. For money, prefer a column already converted to the main
currency (e.g. amount_inr) over a raw one (e.g. amt in paise).

Current columns, with sample values:
{columns}

Other tables you can merge or append:
{tables}"""
