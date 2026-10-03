"""Dashboards over several sources, dashboard filters, cross-filtering, streaming and date presets."""

import json

import httpx
import pytest

from tests.conftest import fake_ai

TARGETS = b"city,target\nMumbai,9000000\nDelhi,4000000\nPune,600000\n"


@pytest.fixture
def targets(client):
    res = client.post("/api/sources/upload", data={"name": "Targets"},
                      files=[("files", ("targets.csv", TARGETS, "text/csv"))])
    assert res.status_code == 201, res.text
    return res.json()["id"]


def city_filter(values, key="city"):
    return {"key": key, "sourceId": "retail-demo", "table": "customers", "column": "city", "values": values,
            "label": "City"}


def test_widgets_from_two_sources_on_one_dashboard(client, targets):
    w = {"sourceId": targets, "title": "Targets", "sql": "SELECT city, target FROM targets", "spec": {"type": "bar"}}
    d = client.post("/api/dashboards/sales-overview/widgets", json=w).json()
    assert d["sourceIds"] == ["retail-demo", targets]  # the new source joined the dashboard
    new = next(x for x in d["widgets"] if x["title"] == "Targets")
    assert new["sourceId"] == targets
    out = client.post("/api/dashboards/sales-overview/run", json={"filters": [city_filter(["Pune"])]}).json()
    assert len(out[new["id"]]["result"]["rows"]) == 3
    assert out[new["id"]]["unaffected"] == ["city"]  # a filter on another source doesn't reach it
    assert "unaffected" not in out["revenue_by_city"]
    assert {r[0] for r in out["revenue_by_city"]["result"]["rows"]} == {"Pune"}
    # A source still used by a chart can't be taken off the dashboard.
    res = client.patch("/api/dashboards/sales-overview", json={"sourceIds": ["retail-demo"]})
    assert res.status_code == 400 and "Targets" in res.json()["detail"]


def test_filters_that_dont_relate_are_reported(client):
    out = client.post("/api/dashboards/sales-overview/run", json={"filters": [
        {"key": "cat", "sourceId": "retail-demo", "table": "products", "column": "category", "values": ["Home"]}]}).json()
    assert out["kpi_revenue"]["unaffected"] == ["cat"]  # orders don't link to products
    assert "unaffected" not in out["revenue_by_category"]


def test_saved_filters_are_validated(client):
    ok = {"id": "f1", "sourceId": "retail-demo", "table": "customers", "column": "city", "label": "City"}
    d = client.patch("/api/dashboards/sales-overview", json={"filters": [ok]}).json()
    assert d["filters"][0]["column"] == "city"
    bad = {**ok, "column": "nope"}
    assert client.patch("/api/dashboards/sales-overview", json={"filters": [bad]}).status_code == 400


def test_cross_filter_traces_the_clicked_category(client, demo_db):
    url = "/api/dashboards/sales-overview/crossfilter"
    city = client.post(url, json={"widgetId": "revenue_by_city", "column": "city", "value": "Delhi"}).json()
    assert (city["table"], city["column"], city["sourceId"]) == ("customers", "city", "retail-demo")
    month = client.post(url, json={"widgetId": "revenue_trend", "column": "month", "value": "2025-03"}).json()
    assert month["table"] == "orders" and "strftime" in month["expr"].lower()
    seg = client.post(url, json={"widgetId": "revenue_by_segment", "column": "segment", "value": "Retail"}).json()
    assert seg["table"] == "customers" and seg["expr"].startswith("CASE")
    assert client.post(url, json={"widgetId": "revenue_trend", "column": "revenue", "value": 1}).status_code == 422
    # Applying the month cross-filter restricts the revenue KPI to March 2025.
    out = client.post("/api/dashboards/sales-overview/run", json={"filters": [month]}).json()
    want = demo_db.execute("SELECT SUM(amt) / 100.0 FROM orders WHERE sts = 1 AND order_date LIKE '2025-03%'").fetchone()[0]
    assert out["kpi_revenue"]["result"]["rows"][0][0] == pytest.approx(want)
    # The clicked chart isn't filtered by its own click: it keeps every month, and isn't "unaffected".
    trend = out["revenue_trend"]
    assert len(trend["result"]["rows"]) > 1 and "unaffected" not in trend
    # A plain column click (category = Electronics) filters what reads products, and says what it can't reach.
    out = client.post("/api/dashboards/sales-overview/run", json={"filters": [
        client.post(url, json={"widgetId": "revenue_by_category", "column": "category", "value": "Electronics"}).json()]}).json()
    assert {r[1] for r in out["top_products"]["result"]["rows"]} == {"Electronics"}
    assert out["revenue_by_city"]["unaffected"] == ["x:revenue_by_category"]
    assert len(out["revenue_by_category"]["result"]["rows"]) > 1


def test_filters_from_the_browser_cannot_smuggle_queries(client):
    sneaky = {"key": "x", "sourceId": "retail-demo", "table": "orders",
              "expr": "(SELECT MAX(city) FROM customers)", "value": "x"}
    assert client.post("/api/dashboards/sales-overview/run", json={"filters": [sneaky]}).status_code == 400


def test_results_stream_as_each_widget_finishes(client):
    res = client.post("/api/dashboards/sales-overview/run", json={"stream": True})
    lines = [json.loads(x) for x in res.text.strip().splitlines()]
    assert lines[-1] == {"done": True} and len(lines) == 10 and all("result" in x for x in lines[:-1])


def test_custom_and_financial_year_presets(client):
    url = "/api/dashboards/sales-overview"
    assert client.patch(url, json={"datePreset": "custom"}).status_code == 400  # needs a range
    d = client.patch(url, json={"datePreset": "custom", "dateRange": {"start": "2025-04-01", "end": "2025-06-30"}}).json()
    assert d["datePreset"] == "custom" and d["dateRange"] == {"start": "2025-04-01", "end": "2025-06-30"}
    assert client.patch(url, json={"datePreset": "this_fy"}).json()["datePreset"] == "this_fy"
    assert client.get("/api/config").json()["fiscalYearStart"] == 4
    assert client.put("/api/settings", json={"fiscalYearStart": 1}).json()["fiscalYearStart"] == 1


def test_generate_over_two_sources(client, targets):
    plan = {"reasoning": "r", "name": "Sales vs targets", "description": "d", "widgets": [
        {"title": "Revenue by city", "request": "revenue by city", "kind": "breakdown", "source": "Retail demo"},
        {"title": "Targets by city", "request": "target by city", "kind": "breakdown", "source": "Targets"},
        {"title": "Orders", "request": "number of orders", "kind": "kpi", "source": "Retail demo"}]}
    drafts = {
        "revenue by city": {"reasoning": "r", "title": "t", "summary": "s.", "chart_type": "bar", "x": "city", "y": "r",
                            "sql": "SELECT c.city, MEASURE(revenue) AS r FROM orders o JOIN customers c "
                                   "ON c.id = o.customer_id GROUP BY c.city"},
        "target by city": {"reasoning": "r", "title": "t", "summary": "s.", "chart_type": "bar", "x": "city",
                           "y": "target", "sql": "SELECT city, target FROM targets"},
        "number of orders": {"reasoning": "r", "title": "t", "summary": "s.", "chart_type": "kpi",
                             "sql": "SELECT COUNT(*) AS n FROM orders"},
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if body["response_format"]["json_schema"]["name"] == "DashboardPlan":
            reply = plan
        else:
            ask = body["messages"][-1]["content"]
            reply = next(v for k, v in drafts.items() if k in ask)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}]})

    with fake_ai(client.app, handler):
        res = client.post("/api/dashboards/generate", json={"sourceIds": ["retail-demo", targets], "goal": "sales vs targets"})
    done = json.loads(res.text.strip().splitlines()[-1])
    assert done["type"] == "done", res.text
    d = client.get(f"/api/dashboards/{done['dashboardId']}").json()
    assert d["sourceIds"] == ["retail-demo", targets]
    assert sorted(w["sourceId"] for w in d["widgets"]) == sorted(["retail-demo", "retail-demo", targets])
