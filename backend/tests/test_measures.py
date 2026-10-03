"""Reusable measures: define once, MEASURE(name) everywhere, same number everywhere."""

import json

import httpx
import pytest

from stardeck.db import SourceRecord
from stardeck.grain import check_fanout
from stardeck.measures import expand_sql
from stardeck.principal import SYSTEM
from stardeck.query import QueryError, run
from tests.conftest import fake_ai


@pytest.fixture
def ctx(client):
    state = client.app.state.stardeck
    with state.db.new_session("default") as db:
        return state.sources.context(db.get(SourceRecord, "retail-demo"), SYSTEM)


@pytest.fixture
def scripted(client):
    stubs = []

    def use(*replies):
        queue = list(replies)

        def handler(request: httpx.Request) -> httpx.Response:
            content = json.dumps(queue.pop(0) if len(queue) > 1 else queue[0])
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

        stub = fake_ai(client.app, handler)
        stub.__enter__()
        stubs.append(stub)
    yield use
    for stub in reversed(stubs):
        stub.__exit__(None, None, None)


def test_demo_has_measures_and_they_match_the_formula(ctx, demo_db):
    assert {"revenue", "completed_orders", "average_order_value", "refund_rate"} <= set(ctx.measures)
    want = demo_db.execute("SELECT SUM(amt) / 100.0 FROM orders WHERE sts = 1").fetchone()[0]
    assert run(ctx, "SELECT MEASURE(revenue) FROM orders").rows[0][0] == pytest.approx(want)


def test_measure_with_alias_join_and_grouping(ctx, demo_db):
    sql = ("SELECT c.city, MEASURE(revenue) AS revenue, MEASURE(completed_orders) AS n FROM orders o "
           "JOIN customers c ON c.id = o.customer_id GROUP BY c.city ORDER BY c.city")
    want = demo_db.execute("SELECT c.city, SUM(CASE WHEN o.sts = 1 THEN o.amt END) / 100.0, "
                           "SUM(CASE WHEN o.sts = 1 THEN 1 ELSE 0 END) FROM orders o JOIN customers c "
                           "ON c.id = o.customer_id GROUP BY c.city ORDER BY c.city").fetchall()
    got = run(ctx, sql).rows
    assert [(r[0], round(r[1], 2), r[2]) for r in got] == [(w[0], round(w[1], 2), w[2]) for w in want]


def test_measure_errors_are_readable(ctx):
    with pytest.raises(QueryError, match="no measure called profit"):
        run(ctx, "SELECT MEASURE(profit) FROM orders")
    with pytest.raises(QueryError, match="orders must be in the FROM"):
        run(ctx, "SELECT MEASURE(revenue) FROM customers")


def test_double_counting_is_caught_through_a_measure(ctx):
    sql = ("SELECT p.category, MEASURE(revenue) AS r FROM orders o JOIN order_items i ON i.order_id = o.id "
           "JOIN products p ON p.id = i.product_id GROUP BY p.category")
    assert check_fanout(sql, ctx.dialect, ctx.tables) is None  # invisible without expanding
    assert check_fanout(expand_sql(sql, ctx.dialect, ctx.measures), ctx.dialect, ctx.tables)


def test_measures_api_validates_saves_and_deletes(client, demo_db):
    url = "/api/sources/retail-demo/measures"
    bad = {"name": "orders_n", "table": "orders", "expression": "amt / 100"}
    assert "must add something up" in client.put(f"{url}/orders_n", json={"measure": bad}).json()["detail"]
    bad = {"name": "orders_n", "table": "orders", "expression": "SUM(nope)"}
    assert "no column nope" in client.put(f"{url}/orders_n", json={"measure": bad}).json()["detail"]
    good = {"name": "orders_n", "label": "Orders", "table": "orders", "expression": "COUNT(*)"}
    assert client.post(f"{url}/check", json={"measure": good}).json()["value"] == \
        demo_db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
    src = client.put(f"{url}/orders_n", json={"measure": good}).json()
    assert any(m["name"] == "orders_n" for m in src["measures"])
    src = client.delete(f"{url}/orders_n").json()
    assert not any(m["name"] == "orders_n" for m in src["measures"])


def test_a_chart_of_a_measure_takes_its_format(client, scripted):
    scripted({"reasoning": "r", "title": "Refund rate", "summary": "Share of orders refunded.", "chart_type": "kpi",
              "sql": "SELECT MEASURE(refund_rate) AS refund_rate FROM orders o", "value_format": "number"})
    out = client.post("/api/sources/retail-demo/widgets/generate", json={"request": "refund rate"}).json()
    assert out["spec"]["format"] == "percent" and out["spec"]["upIsGood"] is False


def test_suggested_measures_are_checked_against_the_data(client, scripted):
    scripted({"measures": [
        {"name": "cancelled_orders", "label": "Cancelled orders", "description": "Orders cancelled.",
         "table": "orders", "expression": "SUM(CASE WHEN sts = 2 THEN 1 ELSE 0 END)"},
        {"name": "broken", "label": "Broken", "description": "x", "table": "orders", "expression": "SUM(nope)"},
        {"name": "revenue", "label": "Dup", "description": "x", "table": "orders", "expression": "SUM(amt)"},
    ]})
    out = client.post("/api/sources/retail-demo/measures/suggest").json()["measures"]
    assert [m["measure"]["name"] for m in out] == ["cancelled_orders"] and out[0]["value"] > 0
