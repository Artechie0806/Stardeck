"""Chart catalogue and fitting rules: which chart types suit which result shapes.

The model proposes a chart; these rules check it against the actual result and fall back to
something that fits. The frontend mirrors the same rules (frontend/src/lib/chartFit.ts) to grey
out chart types that can't show a widget's data.

Result shapes are described by roles:
  dims      text/date columns (categories, time, labels)
  nums      numeric columns
  x         the category/time column
  series    a second category that splits x into several series (long format)
  measures  numeric columns to plot; several = side-by-side series (wide format)
"""

import re
from dataclasses import dataclass, field
from typing import Literal

from stardeck.query import QueryResult

ChartType = Literal[
    "kpi", "gauge", "line", "area", "bar", "hbar", "pie", "donut", "treemap", "funnel", "waterfall",
    "scatter", "bubble", "heatmap", "matrix", "radar", "sankey", "histogram", "boxplot", "map", "table",
]
Stack = Literal["none", "stacked", "percent"]

MAX_SERIES = 8  # categorical palette size; more series become a matrix or table
_TEMPORAL = re.compile(
    r"^\d{4}(-\d{2}(-\d{2})?)?([ T].*)?$|^\d{4}-?(Q|W)\d{1,2}$|^Q[1-4]( \d{4})?$|^\d{1,2}$|"
    r"^(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?( \d{4})?$|"
    r"^(mon|tue|wed|thu|fri|sat|sun)[a-z]*$", re.IGNORECASE)
_LAT = re.compile(r"(^|_)(lat|latitude)$", re.IGNORECASE)
_LON = re.compile(r"(^|_)(lon|lng|long|longitude)$", re.IGNORECASE)
_PLACE = re.compile(r"(city|town|village|state|province|country|region|location|place|address|district|"
                    r"county|pincode|pin_code|zip|postcode|postal_code|area|locality|store|branch)", re.IGNORECASE)
_ID_LIKE = re.compile(r"(^|_)(id|code|cd|key|no|num|sts|status|type|sgmt|segment)$", re.IGNORECASE)


@dataclass
class Roles:
    dims: list[str]
    nums: list[str]
    x: str | None
    series: str | None
    measures: list[str]
    rows: int
    distinct: dict[str, int] = field(default_factory=dict)
    temporal_x: bool = False
    nonneg: bool = True
    explicit_measure: bool = False  # the model named the measure, rather than us guessing it


def numeric_columns(result: QueryResult) -> list[str]:
    out = []
    for i, c in enumerate(result.columns):
        vals = [r[i] for r in result.rows if r[i] is not None]
        if vals and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in vals):
            out.append(c)
    return out


def _match(wanted: str | None, options: list[str]) -> str | None:
    if not wanted:
        return None
    w = wanted.lower()
    return (next((c for c in options if c.lower() == w), None)
            or next((c for c in options if w in c.lower() or c.lower() in w), None))


def derive_roles(result: QueryResult, x: str | None = None, y: str | None = None,
                 series: str | None = None, measures: list[str] | None = None) -> Roles:
    cols, rows = result.columns, result.rows
    nums = numeric_columns(result)
    dims = [c for c in cols if c not in nums]
    rx = x if x in cols else None
    if (rx is None or rx in nums) and dims:
        rx = dims[0]  # models sometimes call the value axis "x"; categories win
    rx = rx or (cols[0] if cols else None)
    rseries = series if series in dims and series != rx else next((d for d in dims if d != rx), None)
    wanted = [m for m in (measures or []) if m in nums and m != rx]
    if not wanted and (m := _match(y, [n for n in nums if n != rx])):
        wanted = [m]
    explicit = bool(wanted)
    candidates = [n for n in nums if n != rx]
    plain = [n for n in candidates if not _ID_LIKE.search(n)]
    if not wanted:
        wanted = (plain or candidates)[-1:]  # measures usually come last in SELECT lists
    # Several comparable numbers (e.g. revenue_2025, revenue_2024) become side-by-side series, even if
    # the model named only one: it was told to return only the columns the chart needs. Numbers on very
    # different scales are not combined, since one axis would flatten the smaller ones.
    if len(wanted) == 1 and rseries is None and 2 <= len(plain) <= MAX_SERIES and wanted[0] in plain and len(rows) > 1:
        peaks = [max((abs(r[cols.index(n)] or 0) for r in rows), default=0) for n in plain]
        if min(peaks) > 0 and max(peaks) / min(peaks) <= 10:
            # Keep the query's column order unless the model named a measure to lead with.
            wanted = [wanted[0], *[n for n in plain if n != wanted[0]]] if explicit else plain
    distinct = {c: len({r[i] for r in rows}) for i, c in enumerate(cols)}
    xi = cols.index(rx) if rx in cols else -1
    labels = [r[xi] for r in rows] if xi >= 0 else []
    temporal = bool(labels) and all(isinstance(v, str) and _TEMPORAL.match(v) for v in labels if v is not None)
    nonneg = all((r[cols.index(m)] or 0) >= 0 for m in wanted for r in rows)
    return Roles(dims, nums, rx, rseries, wanted, len(rows), distinct, temporal, nonneg, explicit)


def fit_problem(chart: str, r: Roles) -> str | None:
    """Why `chart` can't show this result, or None if it fits."""
    one_measure = len(r.measures) >= 1
    n_series = r.distinct.get(r.series, 0) if r.series else len(r.measures)
    match chart:
        case "table":
            return None
        case "kpi" | "gauge":
            return None if r.rows == 1 and r.nums else "needs a single row with a number"
        case "line" | "area" | "bar" | "hbar":
            if not (r.x and one_measure):
                return "needs a category or date column and a number"
            if chart in ("line", "area") and not r.temporal_x:
                return "needs a time axis (dates, months, years)"
            if n_series > MAX_SERIES:
                return f"has more than {MAX_SERIES} series"
            return None
        case "pie" | "donut":
            if r.series or not (r.x and one_measure):
                return "needs one category column and one number"
            return None if r.rows <= 6 and r.nonneg else "works for up to 6 positive parts"
        case "treemap":
            if r.series:
                return "needs one category column, not two"
            return None if r.x and one_measure and r.nonneg and r.rows <= 200 else "needs categories with positive numbers"
        case "funnel":
            if r.series:
                return "needs one stage column, not two"
            return None if r.x and one_measure and r.nonneg and r.rows <= 12 else "needs up to 12 stages with positive numbers"
        case "waterfall":
            return None if r.x and one_measure and not r.series and r.rows <= 40 else "needs up to 40 steps with one number each"
        case "scatter":
            return None if len(r.nums) >= 2 else "needs two number columns"
        case "bubble":
            return None if len(r.nums) >= 3 else "needs three number columns (x, y, size)"
        case "heatmap" | "matrix" | "sankey":
            return None if len(r.dims) >= 2 and one_measure else "needs two category columns and a number"
        case "radar":
            if not (r.x and one_measure) or r.series and n_series > MAX_SERIES:
                return "needs a category column and a number"
            return None if 3 <= r.rows <= 40 else "works for 3 to 12 categories"
        case "histogram":
            return None if r.nums and r.rows >= 5 else "needs at least 5 rows of a number column"
        case "boxplot":
            return None if r.nums and r.rows >= 5 else "needs at least 5 rows of a number column"
        case "map":
            if map_columns(r):
                return None
            return "needs latitude/longitude columns or a place column (city, state, country...)"
    return "unknown chart type"


def map_columns(r: Roles) -> dict | None:
    """Coordinates ({lat, lon}) or a place-name column ({location}) for a map, if present."""
    lat = next((n for n in r.nums if _LAT.search(n)), None)
    lon = next((n for n in r.nums if _LON.search(n)), None)
    if lat and lon:
        return {"lat": lat, "lon": lon}
    place = next((d for d in r.dims if _PLACE.search(d)), None)
    return {"location": place} if place else None


def _fallback(chart: str, r: Roles) -> str:
    """Closest chart that fits, preferring the same family."""
    family = {
        "pie": ["donut", "bar"], "donut": ["pie", "bar"], "treemap": ["bar"], "funnel": ["bar"],
        "waterfall": ["bar"], "gauge": ["kpi"], "kpi": ["line" if r.temporal_x else "bar"],
        "bubble": ["scatter"], "radar": ["bar"], "sankey": ["matrix"], "heatmap": ["matrix"],
        "line": ["bar"], "area": ["line", "bar"], "hbar": ["bar"], "bar": ["hbar"],
        "histogram": ["bar"], "boxplot": ["bar"], "map": ["hbar", "bar"],
    }.get(chart, [])
    if len(r.dims) >= 2:
        family += ["matrix"]
    for candidate in [*family, "bar", "table"]:
        if fit_problem(candidate, r) is None:
            return candidate
    return "table"


def finalize(chart: str, stack: str, r: Roles) -> tuple[dict, list[str]]:
    """Pick the final chart for these roles and build the field part of the spec."""
    notes: list[str] = []
    if r.rows == 1 and r.nums and chart not in ("gauge", "table", "map"):
        chart = "kpi"  # one number is a KPI, whatever was asked
    elif problem := fit_problem(chart, r):
        new = _fallback(chart, r)
        notes.append(f"Shown as {new} because {chart} {problem}.")
        chart = new
    # Readability adjustments that keep the chart's intent.
    if chart in ("bar", "hbar", "pie", "donut") and r.temporal_x and r.rows > 12 and not r.series:
        notes.append("Shown as a line because there are many time points.")
        chart = "line"
    if chart == "bar" and r.rows > 25 and not r.temporal_x:
        chart = "hbar" if r.rows <= 40 else "table"
    spec: dict = {"type": chart}
    if chart == "table":
        return spec, notes
    if chart in ("kpi", "gauge"):
        # For single values the first number is the value (a second one is a target or comparison).
        spec["y"] = r.measures[0] if r.explicit_measure else (r.nums[0] if r.nums else None)
        if chart == "gauge" and len(r.nums) >= 2:
            spec["target"] = next(n for n in r.nums if n != spec["y"])
        return spec, notes
    if chart in ("scatter", "bubble"):
        xs = r.nums
        spec |= {"x": xs[0], "y": xs[1]}
        if chart == "bubble":
            spec["size"] = xs[2]
        if r.dims:
            spec["label"] = r.dims[0]
        return spec, notes
    if chart == "map":
        cols = map_columns(r) or {}
        spec |= cols
        measures = [m for m in r.measures if m not in (cols.get("lat"), cols.get("lon"))]
        if measures:
            spec["y"] = measures[0]
        label = next((d for d in r.dims if d != cols.get("location")), None) or cols.get("location")
        if label:
            spec["label"] = label
        return spec, notes
    if chart in ("histogram", "boxplot"):
        spec["y"] = r.measures[0] if r.measures else r.nums[0]
        if chart == "boxplot" and r.dims:
            spec["x"] = r.dims[0]
        return spec, notes
    spec["x"] = r.x
    if chart in ("heatmap", "matrix", "sankey"):
        spec |= {"series": r.series, "y": r.measures[0]}
        return spec, notes
    if r.series and chart in ("line", "area", "bar", "hbar", "radar"):
        spec |= {"series": r.series, "y": r.measures[0]}
    elif len(r.measures) > 1 and chart in ("line", "area", "bar", "hbar", "radar", "waterfall"):
        spec["measures"] = r.measures[:MAX_SERIES]
        spec["y"] = r.measures[0]
    else:
        spec["y"] = r.measures[0]
    if stack in ("stacked", "percent") and chart in ("bar", "hbar", "area") and (r.series or len(r.measures) > 1):
        spec["stack"] = stack
    return spec, notes


CHART_GUIDE = """Chart types. Pick the one that best answers the request and return exactly the columns it needs.
Name columns after what they hold (city, category, month, revenue), never after their role.
- kpi: one headline number. One row, one column.
- gauge: one percentage or one number against a target. One row: value[, target].
- line / area: a trend over time. Columns: period, number. Several lines: add a category column
  (e.g. month, region, revenue) or several number columns (e.g. month, revenue_2025, revenue_2024).
- bar (vertical) / hbar (horizontal, for long or many labels): compare categories. Clustered: add a
  second category column or several number columns. stack = "stacked" or "percent" (100%) to stack.
- pie / donut: share of a whole, at most 6 parts. Columns: category, positive number.
- treemap: share of a whole with many parts. Columns: category, positive number.
- funnel: process stages in order (e.g. placed -> paid -> delivered). Columns: stage, count.
  Build it with one SELECT per stage joined by UNION ALL.
- waterfall: how a total changed between two periods, step by step. Easiest form: one row per step with
  the value before and after (e.g. category, revenue_2024, revenue_2025); the chart computes the start
  total, each step's change and the end total. Alternative: step, signed change.
- scatter: two numbers per item (e.g. product, units, revenue), one row per item.
- bubble: like scatter plus a size number (e.g. city, orders, revenue, customers).
- heatmap: a number across two categories (e.g. weekday, hour, orders).
- matrix: pivot table with totals (e.g. city, category, revenue).
- radar: 3-12 attributes compared (e.g. category, units[, segment]).
- sankey: flows between two sets (e.g. channel, product, revenue).
- histogram: distribution of a number: one row per item, one number column, no GROUP BY.
- boxplot: distribution per category: category, number, one row per item, no GROUP BY.
- map: values by place (e.g. city, revenue) or latitude, longitude, number. Use real place names.
- table: detail rows, or several columns no chart fits.

Year-over-year comparisons: one row per month number ('01'-'12') with one column per year, e.g.
SUM(CASE WHEN <year of date> = '2025' THEN <measure> END) AS revenue_2025, and the same for 2024."""
