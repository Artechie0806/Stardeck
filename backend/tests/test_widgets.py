import json

import httpx
import pytest

from stardeck.config import Settings
from stardeck.definitions import Definitions
from stardeck.deps import get_llm
from stardeck.llm import LLMClient
from stardeck.query import QueryResult
from stardeck.schema import ColumnInfo, TableInfo
from stardeck.widgets import WidgetDraft, finalize_spec, select_tables


def draft(**kw) -> WidgetDraft:
    base = {"reasoning": "r", "sql": "SELECT 1", "title": "t", "summary": "s", "chart_type": "bar"}
    return WidgetDraft.model_validate({**base, **kw})


def test_single_value_becomes_kpi():
    spec, _ = finalize_spec(draft(chart_type="bar"), QueryResult(["revenue"], [[12.5]]))
    assert spec == {"type": "kpi", "y": "revenue", "format": "number", "upIsGood": True}


def test_line_without_time_axis_becomes_bar_and_notes_it():
    spec, notes = finalize_spec(draft(chart_type="line", x="city", y="n"),
                                QueryResult(["city", "n"], [["Pune", 3], ["Delhi", 5]]))
    assert spec["type"] == "bar" and notes


def test_donut_with_many_parts_becomes_bar_and_long_labels_go_horizontal():
    rows = [[f"Category number {i}", i] for i in range(8)]
    spec, _ = finalize_spec(draft(chart_type="donut", x="c", y="n"), QueryResult(["c", "n"], rows))
    assert spec["type"] == "hbar"


def test_many_time_points_prefer_line():
    rows = [[f"2025-{m:02d}", m] for m in range(1, 13)] + [["2026-01", 1]]
    spec, _ = finalize_spec(draft(chart_type="bar", x="month", y="n"), QueryResult(["month", "n"], rows))
    assert spec["type"] == "line"


def test_measure_fallback_skips_codes_and_matches_loosely():
    res = QueryResult(["customer_name", "segment", "total_revenue"], [["A", 1, 10.0], ["B", 2, 5.0]])
    assert finalize_spec(draft(chart_type="hbar", x="customer_name", y="revenue"), res)[0]["y"] == "total_revenue"
    assert finalize_spec(draft(chart_type="hbar", x="customer_name", y=None), res)[0]["y"] == "total_revenue"


def test_value_axis_called_x_is_swapped_back():
    res = QueryResult(["customer_name", "revenue"], [["A", 10.0], ["B", 5.0]])
    spec, notes = finalize_spec(draft(chart_type="hbar", x="revenue", y="customer_name"), res)
    assert (spec["type"], spec["x"], spec["y"]) == ("hbar", "customer_name", "revenue") and not notes


def test_date_filter_prefers_measured_table_over_model_pick():
    from stardeck.widgets import validate_date_column
    tables = [
        TableInfo(name="customers", columns=[ColumnInfo(name="id", type="INT"), ColumnInfo(name="signup_date", type="DATE")]),
        TableInfo(name="orders", columns=[ColumnInfo(name="amt", type="INT"), ColumnInfo(name="order_date", type="DATE")]),
    ]
    sql = "SELECT c.id, SUM(o.amt) FROM customers c JOIN orders o ON c.id = o.customer_id GROUP BY c.id"
    picked = validate_date_column(draft(date_column="customers.signup_date"), tables, sql, "sqlite")
    assert picked == ("orders", "order_date")
    count_sql = "SELECT COUNT(*) FROM customers"
    assert validate_date_column(draft(), tables, count_sql, "sqlite") == ("customers", "signup_date")


def test_second_category_becomes_series():
    res = QueryResult(["region", "product", "units"], [["N", "A", 1], ["S", "B", 2]])
    spec, _ = finalize_spec(draft(chart_type="bar", stack="stacked"), res)
    assert (spec["type"], spec["x"], spec["series"], spec["y"], spec["stack"]) == ("bar", "region", "product", "units", "stacked")


def test_too_many_series_become_a_matrix():
    rows = [[f"r{i}", f"c{j}", i * j] for i in range(3) for j in range(12)]
    spec, notes = finalize_spec(draft(chart_type="line"), QueryResult(["r", "c", "v"], rows))
    assert spec["type"] == "matrix" and notes


def test_currency_and_down_is_good():
    spec, _ = finalize_spec(draft(chart_type="kpi", value_format="currency", currency="inr", up_is_good=False),
                            QueryResult(["x"], [[1]]))
    assert spec["currency"] == "INR" and spec["upIsGood"] is False


def test_select_tables_prunes_large_schemas_but_keeps_fk_neighbours():
    def t(name, cols, fks=()):
        return TableInfo(name=name, columns=[ColumnInfo(name=c, type="TEXT", samples=["x" * 30]) for c in cols],
                         foreign_keys=list(fks))
    tables = [t("orders", ["id", "customer_id", "amount"], ["customer_id -> customers.id"]),
              t("customers", ["id", "city"])]
    tables += [t(f"audit_log_{i}", [f"field_{j}" for j in range(40)]) for i in range(20)]
    picked = {x.name for x in select_tables(tables, Definitions(), "total amount of orders by city", budget=3000)}
    assert {"orders", "customers"} <= picked
    assert not any(n.startswith("audit") for n in picked)


def _llm_returning(replies: list[dict]):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(json.loads(request.content))
        content = json.dumps(replies[min(len(calls), len(replies)) - 1])
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

    return lambda: LLMClient(Settings(), transport=httpx.MockTransport(handler)), calls


GOOD = {"reasoning": "completed orders by city", "title": "Revenue by city",
        "summary": "Revenue from completed orders in each city.", "chart_type": "bar", "x": "city", "y": "revenue",
        "value_format": "currency", "currency": "INR", "date_column": "orders.order_date",
        "sql": "SELECT c.city, SUM(o.amt) / 100.0 AS revenue FROM orders o JOIN customers c ON c.id = o.customer_id "
               "WHERE o.sts = 1 GROUP BY c.city ORDER BY revenue DESC"}


@pytest.fixture
def with_llm(client):
    def use(replies):
        factory, calls = _llm_returning(replies)
        client.app.dependency_overrides[get_llm] = factory
        return calls
    yield use
    client.app.dependency_overrides.clear()


def test_generate_widget_retries_after_sql_error(client, with_llm):
    calls = with_llm([{**GOOD, "sql": "SELECT city, SUM(nope) FROM customers GROUP BY city"}, GOOD])
    res = client.post("/api/sources/retail-demo/widgets/generate", json={"request": "revenue by city"})
    assert res.status_code == 200, res.text
    w = res.json()
    assert w["attempts"] == 2 and "no such column" in calls[1]["messages"][-1]["content"]
    assert w["spec"] == {"type": "bar", "x": "city", "y": "revenue", "format": "currency", "currency": "INR"}
    assert w["dateFilter"] == {"table": "orders", "column": "order_date"}
    assert len(w["result"]["rows"]) == 6


def test_generate_widget_rejects_writes_then_gives_up(client, with_llm):
    with_llm([{**GOOD, "sql": "DELETE FROM orders"}])
    res = client.post("/api/sources/retail-demo/widgets/generate", json={"request": "delete everything"})
    assert res.status_code == 422 and "Only SELECT" in res.json()["detail"]


def test_query_endpoint_applies_range_and_kpi_compare(client, demo_db):
    from tests.conftest import completed_orders

    body = {"sql": "SELECT COUNT(*) AS n FROM orders o WHERE o.sts = 1", "kpi": True,
            "dateFilter": {"table": "orders", "column": "order_date"},
            "range": {"start": "2025-01-01", "end": "2025-12-31"},
            "compare": {"start": "2024-01-01", "end": "2024-12-31"}}
    res = client.post("/api/sources/retail-demo/query", json=body).json()
    expected = [[completed_orders(demo_db, 2025), completed_orders(demo_db, 2024)]]
    assert res["columns"] == ["value", "previous"] and res["rows"] == expected


def test_query_endpoint_errors_are_400(client):
    res = client.post("/api/sources/retail-demo/query", json={"sql": "DROP TABLE orders"})
    assert res.status_code == 400
    res = client.post("/api/sources/retail-demo/query", json={"sql": "SELECT x FROM nowhere"})
    assert res.status_code == 400 and "no such table" in res.json()["detail"]


def test_clean_summary():
    from stardeck.widgets import clean_summary
    assert clean_summary("This query calculates total revenue per month. It joins x.") == "Shows total revenue per month."
    assert clean_summary("revenue each month") == "Revenue each month"


def test_infer_currency_from_definition_units():
    from stardeck.definitions import ColumnDef, TableDef
    from stardeck.widgets import infer_currency
    defs = Definitions(tables={"orders": TableDef(columns={"amt": ColumnDef(unit="paise")})})
    tables = [TableInfo(name="orders", columns=[ColumnInfo(name="amt", type="INT"), ColumnInfo(name="id", type="INT")])]
    spec = {"type": "kpi", "format": "number"}
    assert infer_currency(spec, "SELECT SUM(amt) / 100.0 FROM orders", "sqlite", tables, defs)["currency"] == "INR"
    assert infer_currency(spec, "SELECT COUNT(id) FROM orders", "sqlite", tables, defs) == spec
    assert infer_currency(spec, "SELECT COUNT(amt) FROM orders", "sqlite", tables, defs) == spec
    pct = {"type": "kpi", "format": "percent"}
    assert infer_currency(pct, "SELECT SUM(amt) FROM orders", "sqlite", tables, defs) == pct


def test_period_comparisons_skip_the_date_filter():
    from stardeck.widgets import compares_periods
    lag = ("SELECT period, revenue - LAG(revenue, 12) OVER (ORDER BY period) AS yoy "
           "FROM (SELECT strftime('%Y-%m', order_date) AS period, SUM(amt) AS revenue FROM orders GROUP BY 1)")
    assert compares_periods(lag, "sqlite")
    plain = "SELECT strftime('%Y-%m', order_date) AS m, SUM(amt) FROM orders GROUP BY 1"
    assert not compares_periods(plain, "sqlite", "Revenue by month")
    for request in ("Year on Year revenue difference", "YoY orders", "revenue vs last year",
                    "monthly revenue growth", "month-over-month signups"):
        assert compares_periods(plain, "sqlite", request), request
    assert not compares_periods(plain, "sqlite", "difference between cities")


def test_generated_yoy_widget_has_no_date_filter(client):
    import json

    import httpx

    from stardeck.config import Settings
    from stardeck.deps import get_llm
    from stardeck.llm import LLMClient
    draft = {"reasoning": "r", "title": "YoY revenue", "summary": "Change vs the same month last year.",
             "chart_type": "line", "x": "period", "y": "yoy", "date_column": "orders.order_date",
             "sql": "SELECT period, revenue - LAG(revenue, 12) OVER (ORDER BY period) AS yoy FROM "
                    "(SELECT strftime('%Y-%m', order_date) AS period, SUM(amt) / 100.0 AS revenue FROM orders "
                    "WHERE sts = 1 GROUP BY period) t ORDER BY period"}

    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(draft)}}]})

    client.app.dependency_overrides[get_llm] = lambda: LLMClient(Settings(), transport=httpx.MockTransport(handler))
    try:
        w = client.post("/api/sources/retail-demo/widgets/generate", json={"request": "monthly revenue"}).json()
    finally:
        client.app.dependency_overrides.clear()
    assert w["dateFilter"] is None and any("all dates" in n for n in w["notes"])


def test_new_chart_types_fit_or_fall_back():
    cats = QueryResult(["stage", "n"], [["Visits", 100], ["Carts", 40], ["Orders", 10]])
    assert finalize_spec(draft(chart_type="funnel"), cats)[0]["type"] == "funnel"
    assert finalize_spec(draft(chart_type="waterfall"), cats)[0]["type"] == "waterfall"
    assert finalize_spec(draft(chart_type="treemap"), cats)[0]["type"] == "treemap"
    two_cats = QueryResult(["category", "segment", "n"], [["A", "x", 1], ["A", "y", 2]])
    assert finalize_spec(draft(chart_type="treemap"), two_cats)[0]["type"] != "treemap"
    signed = QueryResult(["step", "n"], [["Start", 100], ["Refunds", -20], ["New", 30]])
    assert finalize_spec(draft(chart_type="funnel"), signed)[0]["type"] != "funnel"  # negatives don't fit
    pts = QueryResult(["city", "orders", "revenue", "customers"], [["A", 1, 2, 3], ["B", 4, 5, 6]])
    spec, _ = finalize_spec(draft(chart_type="bubble"), pts)
    assert (spec["type"], spec["x"], spec["y"], spec["size"], spec["label"]) == ("bubble", "orders", "revenue", "customers", "city")
    gauge, _ = finalize_spec(draft(chart_type="gauge", value_format="percent"), QueryResult(["rate", "target"], [[42.0, 100]]))
    assert gauge == {"type": "gauge", "y": "rate", "target": "target", "format": "percent", "upIsGood": True}
    wide = QueryResult(["month", "this_year", "last_year"], [["01", 5, 3], ["02", 6, 4], ["03", 7, 2]])
    spec, _ = finalize_spec(draft(chart_type="line"), wide)
    assert spec["measures"] == ["this_year", "last_year"]
    mixed = QueryResult(["month", "orders", "revenue"], [["01", 5, 5000], ["02", 6, 7000]])
    assert "measures" not in finalize_spec(draft(chart_type="line"), mixed)[0]  # scales too different
    raw = QueryResult(["amount"], [[v] for v in range(20)])
    assert finalize_spec(draft(chart_type="histogram"), raw)[0] == {"type": "histogram", "y": "amount", "format": "number"}


def test_year_columns_on_year_month_axis_are_flagged():
    from stardeck.widgets import check_year_columns
    bad = QueryResult(["period", "y2025", "y2024"], [["2024-01", None, 5.0], ["2025-01", 7.0, None]])
    assert check_year_columns(bad, "revenue this year vs last year", "sqlite")
    good = QueryResult(["month", "y2025", "y2024"], [["01", 7.0, 5.0], ["02", 8.0, 6.0]])
    assert check_year_columns(good, "revenue this year vs last year", "sqlite") is None
    assert check_year_columns(bad, "revenue by month", "sqlite") is None


def test_waterfall_bridge_keeps_before_and_after():
    res = QueryResult(["category", "revenue_2024", "revenue_2025"], [["A", 10.0, 15.0], ["B", 8.0, 6.0]])
    spec, _ = finalize_spec(draft(chart_type="waterfall"), res)
    assert spec["type"] == "waterfall" and spec["measures"] == ["revenue_2024", "revenue_2025"]


def test_chart_requests_must_not_collapse_to_one_number():
    from stardeck.widgets import check_wants_chart
    one = QueryResult(["diff"], [[5.0]])
    assert check_wants_chart(one, "YOY difference chart")
    assert check_wants_chart(one, "total revenue") is None
    assert check_wants_chart(QueryResult(["m", "v"], [["01", 1], ["02", 2]]), "revenue chart") is None


def test_currency_without_code_uses_units_or_plain_number():
    from stardeck.definitions import ColumnDef, TableDef
    from stardeck.widgets import infer_currency
    tables = [TableInfo(name="orders", columns=[ColumnInfo(name="amt", type="INT"), ColumnInfo(name="qty", type="INT")])]
    defs = Definitions(tables={"orders": TableDef(columns={"amt": ColumnDef(unit="paise")})})
    spec = {"type": "kpi", "format": "currency"}
    assert infer_currency(spec, "SELECT SUM(amt) FROM orders", "sqlite", tables, defs, None)["currency"] == "INR"
    assert infer_currency(spec, "SELECT SUM(qty) FROM orders", "sqlite", tables, defs, None)["format"] == "number"
    assert infer_currency({**spec, "currency": "EUR"}, "SELECT SUM(qty) FROM orders", "sqlite", tables, defs, "EUR")["currency"] == "EUR"
