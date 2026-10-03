import json

import httpx

from stardeck.dashboards import ResultCache, auto_layout
from stardeck.query import QueryResult
from tests.conftest import completed_orders, fake_ai

LAST_YEAR = {"start": "2025-01-01", "end": "2025-12-31", "compareStart": "2024-01-01", "compareEnd": "2024-12-31"}


def test_demo_dashboard_is_seeded_and_runs(client, demo_db):
    dashboards = client.get("/api/dashboards").json()
    assert [d["id"] for d in dashboards] == ["sales-overview"]
    d = client.get("/api/dashboards/sales-overview").json()
    assert d["datePreset"] == "this_year" and len(d["widgets"]) == 9
    results = client.post("/api/dashboards/sales-overview/run", json=LAST_YEAR).json()
    assert set(results) == {w["id"] for w in d["widgets"]}
    assert results["kpi_orders"]["result"]["rows"] == [[completed_orders(demo_db, 2025), completed_orders(demo_db, 2024)]]
    assert all("result" in r for r in results.values())


def test_widget_crud_validates_sql(client):
    body = {"title": "Orders", "sql": "SELECT COUNT(*) AS n FROM orders", "spec": {"type": "kpi"},
            "dateFilter": {"table": "orders", "column": "order_date"}}
    d = client.post("/api/dashboards/sales-overview/widgets", json=body).json()
    new = d["widgets"][-1]
    assert new["layout"]["w"] == 3 and new["id"].startswith("w-")
    bad = client.patch(f"/api/dashboards/sales-overview/widgets/{new['id']}", json={"sql": "DELETE FROM orders"})
    assert bad.status_code == 400
    d = client.patch(f"/api/dashboards/sales-overview/widgets/{new['id']}",
                     json={"chartType": "bar", "title": "Renamed", "clearDateFilter": True}).json()
    w = next(x for x in d["widgets"] if x["id"] == new["id"])
    assert (w["spec"]["type"], w["title"], w["dateFilter"]) == ("bar", "Renamed", None)
    d = client.delete(f"/api/dashboards/sales-overview/widgets/{new['id']}").json()
    assert all(x["id"] != new["id"] for x in d["widgets"])


def test_layout_rename_preset_delete(client):
    d = client.put("/api/dashboards/sales-overview/layout", json={"kpi_revenue": {"x": 6, "y": 0, "w": 6, "h": 4}}).json()
    assert next(w for w in d["widgets"] if w["id"] == "kpi_revenue")["layout"]["x"] == 6
    d = client.patch("/api/dashboards/sales-overview", json={"name": "Sales", "datePreset": "all"}).json()
    assert (d["name"], d["datePreset"]) == ("Sales", "all")
    assert client.patch("/api/dashboards/sales-overview", json={"datePreset": "someday"}).status_code == 400
    assert client.delete("/api/dashboards/sales-overview").status_code == 204
    assert client.get("/api/dashboards").json() == []


def test_deleting_source_removes_its_dashboards(client):
    assert client.delete("/api/sources/retail-demo").status_code == 204
    assert client.get("/api/dashboards").json() == []


def test_auto_layout():
    def w(i, t):
        return {"id": str(i), "spec": {"type": t}}
    out = auto_layout([w(1, "kpi"), w(2, "bar"), w(3, "kpi"), w(4, "line"), w(5, "donut"), w(6, "table"), w(7, "hbar")])
    pos = {x["id"]: x["layout"] for x in out}
    assert pos["1"] == {"x": 0, "y": 0, "w": 6, "h": 4} and pos["3"]["x"] == 6
    assert pos["4"] == {"x": 0, "y": 4, "w": 8, "h": 8} and pos["5"] == {"x": 8, "y": 4, "w": 4, "h": 8}
    assert pos["2"]["y"] == pos["7"]["y"] == 12 and pos["6"]["w"] == 12


def test_cache_ttl_and_lru():
    c = ResultCache(ttl_s=60, max_entries=2)
    r = QueryResult(["a"], [[1]])
    c.put(("s", 1), r)
    c.put(("s", 2), r)
    c.get(("s", 1))
    c.put(("s", 3), r)  # evicts the least recently used: ("s", 2)
    assert c.get(("s", 2)) is None and c.get(("s", 1)) is r
    c.drop_source("s")
    assert c.get(("s", 1)) is None


def test_generate_streams_plan_and_saves(client, monkeypatch):
    plan = {"reasoning": "r", "name": "Shop pulse", "description": "Key numbers.",
            "widgets": [{"title": "Orders", "request": "number of orders", "kind": "kpi"},
                        {"title": "Orders by city", "request": "orders by city", "kind": "breakdown"},
                        {"title": "Broken", "request": "x", "kind": "kpi"}]}
    widget_ok = {"reasoning": "r", "title": "Orders", "summary": "How many orders.", "chart_type": "kpi",
                 "sql": "SELECT COUNT(*) AS n FROM orders", "date_column": "orders.order_date"}
    widget_city = {"reasoning": "r", "title": "By city", "summary": "Orders per city.", "chart_type": "bar",
                   "x": "city", "y": "n", "sql": "SELECT c.city, COUNT(*) AS n FROM orders o JOIN customers c "
                   "ON c.id = o.customer_id GROUP BY c.city"}
    widget_bad = {"reasoning": "r", "title": "Broken", "summary": "s", "chart_type": "kpi", "sql": "SELECT nope FROM x"}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        name = body["response_format"]["json_schema"]["name"]
        user = body["messages"][1]["content"]
        reply = plan if name == "DashboardPlan" else widget_city if "city" in user else \
            widget_bad if "Broken" in user else widget_ok
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(reply)}}]})

    with fake_ai(client.app, handler), \
            client.stream("POST", "/api/dashboards/generate", json={"sourceId": "retail-demo", "goal": ""}) as res:
        events = [json.loads(line) for line in res.iter_lines() if line]
    kinds = [e["type"] for e in events]
    assert kinds[0] == "plan" and kinds[-1] == "done"
    assert kinds.count("widget") == 2 and kinds.count("widget_failed") == 1
    d = client.get(f"/api/dashboards/{events[-1]['dashboardId']}").json()
    assert d["name"] == "Shop pulse" and [w["spec"]["type"] for w in d["widgets"]] == ["kpi", "bar"]
    assert d["widgets"][0]["dateFilter"] == {"table": "orders", "column": "order_date"}


def test_editing_sql_refits_chart_format_title_and_date_filter(client):
    url = "/api/dashboards/sales-overview/widgets/revenue_by_city"
    # Same columns, fewer rows: the user's title, format and date filter stay.
    sql = ("SELECT c.city, SUM(o.amt) / 100.0 AS revenue FROM orders o JOIN customers c ON c.id = o.customer_id "
           "WHERE o.sts = 1 GROUP BY c.city ORDER BY revenue DESC LIMIT 3")
    w = next(x for x in client.patch(url, json={"sql": sql}).json()["widgets"] if x["id"] == "revenue_by_city")
    assert (w["title"], w["spec"]["format"], w["spec"]["currency"]) == ("Revenue by city", "currency", "INR")
    assert w["dateFilter"] == {"table": "orders", "column": "order_date"}
    # New columns (counts by region and outcome): chart fields, format and title are redone.
    sql = ("SELECT c.city AS region, CASE o.sts WHEN 1 THEN 'Completed' ELSE 'Other' END AS outcome, "
           "COUNT(*) AS order_count FROM orders o JOIN customers c ON c.id = o.customer_id GROUP BY 1, 2")
    w = next(x for x in client.patch(url, json={"sql": sql}).json()["widgets"] if x["id"] == "revenue_by_city")
    assert w["spec"]["type"] == "hbar" and (w["spec"]["x"], w["spec"]["series"], w["spec"]["y"]) == ("region", "outcome", "order_count")
    assert w["spec"]["format"] == "number" and "currency" not in w["spec"]
    assert w["title"] == "Order count by region and outcome"  # model offline in tests: title from columns
    # A query that no longer reads orders moves the date filter to a date it does read.
    sql = "SELECT city, COUNT(*) AS customers FROM customers GROUP BY city"
    w = next(x for x in client.patch(url, json={"sql": sql}).json()["widgets"] if x["id"] == "revenue_by_city")
    assert w["dateFilter"] == {"table": "customers", "column": "signup_date"}
    results = client.post("/api/dashboards/sales-overview/run", json={}).json()
    assert "result" in results["revenue_by_city"]
