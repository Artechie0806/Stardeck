import json

import httpx
import pytest

from stardeck.config import Settings
from stardeck.deps import get_llm
from stardeck.llm import LLMClient

RANGE = {"start": "2025-01-01", "end": "2025-12-31", "compareStart": "2024-01-01", "compareEnd": "2024-12-31"}


@pytest.fixture
def scripted(client):
    """Feed the assistant canned model replies: intent first, then any widget drafts."""
    def use(*replies):
        queue = list(replies)

        def handler(request: httpx.Request) -> httpx.Response:
            content = json.dumps(queue.pop(0) if len(queue) > 1 else queue[0])
            return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})

        client.app.dependency_overrides[get_llm] = lambda: LLMClient(Settings(), transport=httpx.MockTransport(handler))
    yield use
    client.app.dependency_overrides.clear()


def say(client, message):
    res = client.post("/api/dashboards/sales-overview/chat", json={"message": message, **RANGE})
    assert res.status_code == 200, res.text
    return res.json()


def test_define_column_validates_against_schema(client, scripted):
    scripted({"intent": "define_column", "table": "order", "column": "STS", "description": "Order status",
              "values": [{"code": "9", "meaning": "Refunded"}]})
    out = say(client, "sts 9 means refunded")
    assert out["proposal"] == {"kind": "definition", "table": "orders", "column": "sts",
                               "definition": {"description": "Order status", "values": {"9": "Refunded"}}}


def test_define_unknown_column(client, scripted):
    scripted({"intent": "define_column", "column": "zzz", "description": "x"})
    assert "couldn't find" in say(client, "zzz is x")["text"]


def test_add_widget_previews_with_range_and_asks_about_unknown_columns(client, scripted, demo_db):
    draft = {"reasoning": "r", "title": "Orders by status", "summary": "Orders in each status.",
             "chart_type": "bar", "x": "sts", "y": "n", "date_column": "orders.order_date",
             "sql": "SELECT CAST(sts AS TEXT) AS sts, COUNT(*) AS n FROM orders GROUP BY sts"}
    scripted({"intent": "add_widget", "request": "orders by status"}, draft)
    out = say(client, "add orders by status")
    p = out["proposal"]
    assert p["kind"] == "add_widget" and p["widget"]["dateFilter"] == {"table": "orders", "column": "order_date"}
    orders_2025 = demo_db.execute("SELECT COUNT(*) FROM orders WHERE order_date LIKE '2025%'").fetchone()[0]
    assert sum(r[1] for r in p["widget"]["result"]["rows"]) == orders_2025  # the date filter applied
    assert "orders.sts" in out["text"]


def test_change_type_rename_remove_and_kpi_guard(client, scripted):
    scripted({"intent": "change_chart_type", "widget_id": "revenue_by_city", "chart_type": "donut"})
    assert say(client, "make revenue by city a donut")["proposal"]["type"] == "donut"
    scripted({"intent": "change_chart_type", "widget_id": "kpi_revenue", "chart_type": "bar"})
    assert "can't be swapped" in say(client, "revenue as bar")["text"]
    scripted({"intent": "change_chart_type", "widget_id": "revenue_by_city", "chart_type": "bar", "stack": "percent"})
    out = say(client, "make revenue by city a 100% stacked column")
    assert out["proposal"]["stack"] == "percent" and "100% stacked column" in out["text"]
    scripted({"intent": "rename_widget", "widget_id": "top_products", "new_title": "Best sellers"})
    assert say(client, "rename top products")["proposal"]["title"] == "Best sellers"
    # Model omitted the id: title matching fills it in.
    scripted({"intent": "remove_widget"})
    assert say(client, "remove the refund rate tile")["proposal"]["widgetId"] == "kpi_refund_rate"


def test_modify_widget_uses_current_sql(client, scripted):
    draft = {"reasoning": "r", "title": "Top 3 cities", "summary": "Top cities.", "chart_type": "hbar",
             "x": "city", "y": "revenue", "date_column": "orders.order_date",
             "sql": "SELECT c.city, SUM(o.amt) / 100.0 AS revenue FROM orders o JOIN customers c ON c.id = o.customer_id "
                    "WHERE o.sts = 1 GROUP BY c.city ORDER BY revenue DESC LIMIT 3"}
    scripted({"intent": "modify_widget", "widget_id": "revenue_by_city", "request": "only top 3"}, draft)
    out = say(client, "only show the top 3 cities in revenue by city")
    assert out["proposal"]["kind"] == "replace_widget" and len(out["proposal"]["widget"]["result"]["rows"]) == 3


def test_other_and_model_down(client, scripted):
    scripted({"intent": "other", "reply": "I build dashboards."})
    assert say(client, "tell me a joke")["text"] == "I build dashboards."
    client.app.dependency_overrides.clear()
    assert client.post("/api/dashboards/sales-overview/chat", json={"message": "hi"}).status_code == 503


def test_power_bi_chart_words_override_the_model():
    from stardeck.chat import _chart_words
    assert _chart_words("make it a 100% stacked bar", "bar") == ("hbar", "percent")
    assert _chart_words("stacked column please", "hbar") == ("bar", "stacked")
    assert _chart_words("show as a bar chart", "bar") == ("hbar", None)
    assert _chart_words("make it a donut", "donut") == ("donut", None)


def test_vague_requests_get_a_question():
    from stardeck.chat import vague_request
    vocab = {"orders", "revenue", "city", "customers"}
    text, options = vague_request("year on year difference between the revenue", vocab)
    assert "one total number" in text and len(options) == 3
    assert vague_request("I need a chart", vocab) == ("What should it show?", [])
    assert vague_request("revenue by month", vocab) is None
    assert vague_request("revenue this year vs last year by month", vocab) is None
    assert vague_request("top 5 customers", vocab) is None


def test_clarify_then_answer_builds_combined_request(client, scripted):
    scripted({"intent": "add_widget", "request": "year on year revenue difference"})
    out = client.post("/api/dashboards/sales-overview/chat",
                      json={"message": "year on year difference between the revenue", **RANGE}).json()
    assert out["options"] and out["pending"] == "year on year difference between the revenue" and "proposal" not in out
    # Suggestions for "what should it show?" come from the glossary and tables.
    scripted({"intent": "add_widget", "request": "a chart"})
    out = client.post("/api/dashboards/sales-overview/chat", json={"message": "i need a chart", **RANGE}).json()
    assert out["text"] == "What should it show?" and "Revenue by month" in out["options"]
    # The answer goes back with the pending request; the model may still say clarify -> we build anyway.
    draft = {"reasoning": "r", "title": "Revenue by month", "summary": "Revenue each month.", "chart_type": "line",
             "x": "month", "y": "revenue", "sql": "SELECT strftime('%Y-%m', order_date) AS month, SUM(amt) / 100.0 "
             "AS revenue FROM orders WHERE sts = 1 GROUP BY month ORDER BY month"}
    scripted({"intent": "clarify", "reply": "Which?"}, draft)
    out = client.post("/api/dashboards/sales-overview/chat",
                      json={"message": "Month by month", "pending": "revenue difference", **RANGE}).json()
    assert out["proposal"]["kind"] == "add_widget"


def test_model_clarify_returns_options(client, scripted):
    scripted({"intent": "clarify", "reply": "Revenue or number of orders?", "options": ["Revenue?", "Orders", " "]})
    out = say(client, "show sales")
    assert out == {"text": "Revenue or number of orders?", "options": ["Revenue", "Orders"], "pending": "show sales"}
    scripted({"intent": "clarify", "reply": "", "options": ["A", "B"]})
    assert say(client, "show sales")["text"] == "Which of these did you mean?"
