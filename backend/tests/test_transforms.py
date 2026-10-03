import json

import httpx
import pytest

from stardeck.config import Settings
from stardeck.transforms import Compiler, Recipe, TransformError, steps_adapter, with_prepared
from tests.conftest import fake_ai

BASE = "/api/sources/retail-demo/transforms"


def preview(client, base, steps, **kw):
    res = client.post(f"{BASE}/preview", json={"base": base, "steps": steps, **kw})
    assert res.status_code == 200, res.text
    return res.json()


EVERY_STEP = [
    {"op": "filter_rows", "column": "sts", "operator": "in", "value": "1, 9"},
    {"op": "replace_values", "column": "sts", "mapping": {"1": "Completed", "9": "Refunded"}},
    {"op": "calculated_column", "name": "amount_inr", "expression": "amt / 100.0"},
    {"op": "date_part", "column": "order_date", "part": "year_month", "name": "month"},
    {"op": "date_part", "column": "order_date", "part": "weekday", "name": "weekday"},
    {"op": "date_part", "column": "order_date", "part": "quarter", "name": "quarter"},
    {"op": "change_type", "column": "amount_inr", "to": "decimal"},
    {"op": "merge", "table": "customers", "left_on": "customer_id", "right_on": "id", "columns": ["city", "sgmt"]},
    {"op": "fill_blanks", "column": "city", "value": "Unknown"},
    {"op": "text_transform", "column": "city", "mode": "upper"},
    {"op": "rename_column", "column": "sts", "to": "status"},
    {"op": "remove_columns", "columns": ["amt"]},
    {"op": "group_by", "by": ["month", "city", "status"],
     "aggregations": [{"column": "amount_inr", "func": "sum", "name": "revenue"},
                      {"column": None, "func": "count", "name": "orders"}]},
    {"op": "filter_rows", "column": "revenue", "operator": "gt", "value": "1000"},
    {"op": "sort", "column": "revenue", "descending": True},
    {"op": "keep_top", "n": 50},
]


def test_every_step_compiles_and_runs_on_sqlite(client):
    out = preview(client, "orders", EVERY_STEP)
    assert out["error"] is None, out["error"]
    assert out["columns"] == ["month", "city", "status", "revenue", "orders"]
    assert len(out["rows"]) == 50 and out["rows"][0][3] >= out["rows"][-1][3]  # sorted descending
    assert {r[2] for r in out["rows"]} <= {"Completed", "Refunded"} and out["rows"][0][1].isupper()
    assert len(out["labels"]) == len(EVERY_STEP) and out["labels"][0] == "Filtered: sts is one of 1, 9"
    # Clicking an earlier step previews the table as it was at that point.
    mid = preview(client, "orders", EVERY_STEP, upto=3)
    assert mid["columns"][-1] == "amount_inr"


def test_unpivot_append_dedupe():
    cols = {"sales": ["region", "q1", "q2"], "more_sales": ["region", "q1", "q2"]}
    steps = steps_adapter.validate_python([
        {"op": "append", "table": "more_sales"},
        {"op": "remove_duplicates", "columns": []},
        {"op": "unpivot", "columns": ["q1", "q2"], "name_column": "quarter", "value_column": "amount"},
    ])
    sql, history = Compiler("postgres", cols).compile(Recipe(name="x", base="sales", steps=steps))
    assert history[-1] == ["region", "quarter", "amount"] and sql.count("UNION ALL") == 2


def test_step_errors_point_at_the_step(client):
    out = preview(client, "orders", [{"op": "rename_column", "column": "amt", "to": "amount"},
                                      {"op": "filter_rows", "column": "amt", "operator": "gt", "value": "5"}])
    assert out["errorStep"] == 1 and "doesn't exist" in out["error"]
    bad = preview(client, "orders", [{"op": "calculated_column", "name": "x", "expression": "(SELECT 1)"}])
    assert "row by row" in bad["error"]
    bad = preview(client, "orders", [{"op": "calculated_column", "name": "x", "expression": "nope * 2"}])
    assert "doesn't exist" in bad["error"]


def test_save_use_in_widget_with_date_filter_and_protections(client):
    steps = [{"op": "filter_rows", "column": "sts", "operator": "eq", "value": "1"},
             {"op": "calculated_column", "name": "amount_inr", "expression": "amt / 100.0"}]
    saved = client.put(f"{BASE}/completed_orders", json={"base": "orders", "steps": steps}).json()
    assert saved["rowCount"] > 0 and saved["columns"][-1] == "amount_inr" and saved["usedBy"] == []
    # It shows up in the schema like a table, flagged as prepared.
    src = client.get("/api/sources/retail-demo").json()
    t = next(t for t in src["tables"] if t["name"] == "completed_orders")
    assert t["prepared"] and any(c["name"] == "amount_inr" for c in t["columns"])
    # Widgets can query it, and the dashboard date filter applies to its date column.
    body = {"sql": "SELECT SUM(amount_inr) AS revenue FROM completed_orders", "kpi": True,
            "dateFilter": {"table": "completed_orders", "column": "order_date"},
            "range": {"start": "2025-01-01", "end": "2025-12-31"}}
    r = client.post("/api/sources/retail-demo/query", json=body)
    assert r.status_code == 200, r.text
    direct = client.post("/api/sources/retail-demo/query", json={
        "sql": "SELECT SUM(amt) / 100.0 FROM orders WHERE sts = 1 AND order_date >= '2025-01-01' AND order_date < '2026-01-01'"}).json()
    assert round(r.json()["rows"][0][0], 2) == round(direct["rows"][0][0], 2)
    # A prepared table built on a prepared table.
    by_month = [{"op": "date_part", "column": "order_date", "part": "year_month", "name": "month"},
                {"op": "group_by", "by": ["month"], "aggregations": [{"column": "amount_inr", "func": "sum", "name": "revenue"}]}]
    assert client.put(f"{BASE}/monthly_revenue", json={"base": "completed_orders", "steps": by_month}).status_code == 200
    q = client.post("/api/sources/retail-demo/query", json={"sql": "SELECT COUNT(*) FROM monthly_revenue"}).json()
    assert q["rows"][0][0] > 20
    # Can't delete a table another one builds on, or one a widget uses; can't rename it while used.
    assert client.delete(f"{BASE}/completed_orders").status_code == 409
    widget = {"title": "Revenue", "sql": "SELECT SUM(revenue) AS revenue FROM monthly_revenue", "spec": {"type": "kpi"}}
    assert client.post("/api/dashboards/sales-overview/widgets", json=widget).status_code == 201
    assert client.delete(f"{BASE}/monthly_revenue").status_code == 409
    rename = client.put(f"{BASE}/revenue_by_month", json={"base": "completed_orders", "steps": by_month, "originalName": "monthly_revenue"})
    assert rename.status_code == 400 and "used by 1 widget" in rename.json()["detail"]
    # Names must be valid and must not clash with real tables.
    assert client.put(f"{BASE}/orders", json={"base": "orders", "steps": []}).status_code == 400
    assert client.put(f"{BASE}/Bad Name", json={"base": "orders", "steps": []}).status_code == 400


def test_with_prepared_merges_with_user_ctes_and_detects_loops():
    prepared = {"a": "SELECT 1 AS x", "b": "SELECT x FROM a"}
    out = with_prepared("WITH mine AS (SELECT x FROM b) SELECT * FROM mine", "sqlite", prepared)
    assert out.index("a AS") < out.index("b AS") < out.index("mine AS")
    with pytest.raises(TransformError, match="loop"):
        with_prepared("SELECT * FROM a", "sqlite", {"a": "SELECT * FROM b", "b": "SELECT * FROM a"})


def test_ai_suggests_a_validated_step(client):
    replies = [
        {"reasoning": "r", "op": "filter_rows", "column": "status", "operator": "eq", "value": "1"},  # wrong column
        {"reasoning": "r", "op": "filter_rows", "column": "sts", "operator": "eq", "value": "1"},
    ]
    calls = []

    def handler(request):
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(replies[len(calls) - 1])}}]})

    stub = fake_ai(client.app, handler)
    stub.__enter__()
    try:
        out = client.post(f"{BASE}/suggest", json={"base": "orders", "steps": [], "request": "only completed orders"}).json()
    finally:
        stub.__exit__(None, None, None)
    assert out["step"] == {"op": "filter_rows", "column": "sts", "operator": "eq", "value": "1", "value2": None}
    assert out["label"] == "Filtered: sts = 1" and out["preview"]["error"] is None
    assert "doesn't exist" in calls[1]["messages"][-1]["content"]
    # Samples and definitions reach the prompt, so the model can match real values.
    assert "Order total" in calls[0]["messages"][0]["content"]


def test_migration_adds_transforms_column(tmp_path):
    import sqlite3

    from stardeck.db import Database
    path = tmp_path / "old.db"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE sources (id VARCHAR(64) PRIMARY KEY, name VARCHAR(200), kind VARCHAR(20), config JSON, "
               "definitions JSON, snapshot JSON, profiled_at DATETIME, profile_error TEXT, created_at DATETIME)")
    db.commit()
    db.close()
    Database(Settings(data_dir=tmp_path, database_url=f"sqlite:///{path}"))
    cols = [r[1] for r in sqlite3.connect(path).execute("PRAGMA table_info(sources)")]
    assert "transforms" in cols


def test_type_aware_sql_for_strict_databases():
    cols = {"orders": ["id", "sts", "city", "zip"]}
    types = {"orders": {"id": "INTEGER", "sts": "INTEGER", "city": "VARCHAR(40)", "zip": "TEXT"}}
    def compile_(steps):
        return Compiler("postgres", cols, types).compile(Recipe(name="r", base="orders", steps=steps_adapter.validate_python(steps)))[0]
    # Codes -> labels on a number column: compared and returned as text, never mixing types.
    sql = compile_([{"op": "replace_values", "column": "sts", "mapping": {"1": "Completed"}}])
    assert "CASE CAST(\"sts\" AS TEXT) WHEN '1' THEN 'Completed' ELSE CAST(\"sts\" AS TEXT) END" in sql
    # A number-looking value on a text column stays quoted; on a number column it's a number.
    assert "\"zip\" = '411001'" in compile_([{"op": "filter_rows", "column": "zip", "operator": "eq", "value": "411001"}])
    assert "\"sts\" = 1" in compile_([{"op": "filter_rows", "column": "sts", "operator": "eq", "value": "1"}])
    with pytest.raises(TransformError, match="holds numbers"):
        compile_([{"op": "filter_rows", "column": "sts", "operator": "eq", "value": "done"}])
    # "Unknown" into a number column turns it into text instead of failing.
    assert "COALESCE(CAST(\"id\" AS TEXT), 'Unknown')" in compile_([{"op": "fill_blanks", "column": "id", "value": "Unknown"}])
    # After relabelling, the column is text, so a later text filter is quoted.
    sql = compile_([{"op": "replace_values", "column": "sts", "mapping": {"1": "Completed"}},
                    {"op": "filter_rows", "column": "sts", "operator": "eq", "value": "Completed"}])
    assert "\"sts\" = 'Completed'" in sql



def test_in_filter_gathers_values_from_any_field():
    from stardeck.transforms import StepDraft, draft_to_step
    d = StepDraft(reasoning="r", op="filter_rows", column="city", operator="in", value="Delhi", value2="Mumbai")
    assert draft_to_step(d).value == "Delhi, Mumbai"
    d = StepDraft(reasoning="r", op="filter_rows", column="city", operator="in", values=["Delhi", "Pune"])
    assert draft_to_step(d).value == "Delhi, Pune"



def test_prepared_columns_inherit_meanings_and_money_is_detected(client):
    steps = [{"op": "merge", "table": "customers", "left_on": "customer_id", "right_on": "id", "columns": ["sgmt"]},
             {"op": "calculated_column", "name": "amount_inr", "expression": "amt / 100.0"}]
    assert client.put(f"{BASE}/orders_plus", json={"base": "orders", "steps": steps}).status_code == 200
    t = next(t for t in client.get("/api/sources/retail-demo").json()["tables"] if t["name"] == "orders_plus")
    sgmt = next(c for c in t["columns"] if c["name"] == "sgmt")
    assert sgmt["definition"]["values"]["1"] == "Retail"  # inherited from customers.sgmt
    # Inherited meanings aren't stored as copies.
    stored = client.get("/api/sources/retail-demo/definitions.yaml").text
    assert "orders_plus" not in stored
    from stardeck.definitions import Definitions
    from stardeck.schema import ColumnInfo, TableInfo
    from stardeck.widgets import infer_currency
    tables = [TableInfo(name="orders_plus", columns=[ColumnInfo(name="amount_inr", type="REAL")])]
    spec = infer_currency({"type": "bar", "format": "number"}, "SELECT SUM(amount_inr) FROM orders_plus", "sqlite", tables, Definitions())
    assert spec["currency"] == "INR"



def test_subset_prepared_tables_only_used_when_named():
    from stardeck.schema import ColumnInfo, TableInfo
    from stardeck.transforms import drops_rows
    from stardeck.widgets import usable_tables
    assert drops_rows(steps_adapter.validate_python([{"op": "filter_rows", "column": "a", "operator": "eq", "value": "1"}]))
    assert not drops_rows(steps_adapter.validate_python([{"op": "calculated_column", "name": "b", "expression": "a * 2"}]))
    tables = [TableInfo(name="orders", columns=[ColumnInfo(name="a", type="INT")]),
              TableInfo(name="orders_2025", columns=[], prepared=True, subset=True),
              TableInfo(name="orders_inr", columns=[], prepared=True, subset=False)]
    assert [t.name for t in usable_tables(tables, "Revenue by year")] == ["orders", "orders_inr"]
    assert "orders_2025" in [t.name for t in usable_tables(tables, "revenue from orders_2025 by city")]


def test_split_column_each_and_first(client):
    out = preview(client, "orders", [
        {"op": "split_column", "column": "order_date", "delimiter": "-", "mode": "each", "parts": 4,
         "names": ["y", "m"]},  # 4th part doesn't exist -> blank; unnamed parts get default names
        {"op": "split_column", "column": "order_date", "delimiter": "-", "mode": "first", "names": ["year", "rest"]},
        {"op": "select_columns", "columns": ["order_date", "y", "m", "order_date_3", "order_date_4", "year", "rest"]},
    ])
    assert out["error"] is None, out["error"]
    date, y, m, d, extra, year, rest = out["rows"][0]
    assert f"{y}-{m}-{d}" == date and extra is None
    assert year == y and rest == f"{m}-{d}"


def test_conditional_column_bands_and_column_results(client):
    out = preview(client, "orders", [
        {"op": "calculated_column", "name": "inr", "expression": "amt / 100.0"},
        {"op": "conditional_column", "name": "band", "rules": [
            {"column": "inr", "operator": "gte", "value": "10000", "result": "High"},
            {"column": "inr", "operator": "gte", "value": "5000", "result": "Medium"}], "otherwise": "Low"},
        {"op": "conditional_column", "name": "label", "rules": [
            {"column": "sts", "operator": "eq", "value": "1", "result_column": "band"}], "otherwise": "Not completed"},
        {"op": "select_columns", "columns": ["inr", "sts", "band", "label"]},
    ])
    assert out["error"] is None, out["error"]
    for inr, sts, band, label in out["rows"]:
        assert band == ("High" if inr >= 10000 else "Medium" if inr >= 5000 else "Low")
        assert label == (band if sts == 1 else "Not completed")


def test_pivot_auto_values_subset_and_limit(client, demo_db):
    steps = [
        {"op": "merge", "table": "customers", "left_on": "customer_id", "right_on": "id", "columns": ["city", "sgmt"]},
        {"op": "replace_values", "column": "sgmt", "mapping": {"1": "Retail", "2": "Wholesale", "3": "Corporate"}},
        {"op": "select_columns", "columns": ["city", "sgmt", "amt"]},
        {"op": "pivot", "column": "sgmt", "value_column": "amt", "func": "sum"},
    ]
    out = preview(client, "orders", steps)
    assert out["error"] is None, out["error"]
    assert out["columns"] == ["city", "corporate", "retail", "wholesale"] and len(out["rows"]) == 6
    total = demo_db.execute("SELECT SUM(amt) FROM orders").fetchone()[0]
    assert sum(sum(v or 0 for v in r[1:]) for r in out["rows"]) == total
    assert out["labels"][-1] == "Pivoted sgmt into 3 columns (sum of amt)"  # values filled in
    # Only chosen values become columns; count needs no value column.
    out = preview(client, "orders", [*steps[:3], {"op": "pivot", "column": "sgmt", "value_column": "amt", "func": "count",
                                                  "values": ["Retail"]}])
    assert out["columns"] == ["city", "retail"] and out["error"] is None
    assert sum(r[1] for r in out["rows"]) == demo_db.execute(
        "SELECT COUNT(*) FROM orders o JOIN customers c ON c.id = o.customer_id WHERE c.sgmt = 1").fetchone()[0]
    # Too many distinct values: ask the user to choose.
    out = preview(client, "orders", [{"op": "pivot", "column": "customer_id", "value_column": "amt"}])
    assert "more than 30" in out["error"] and out["errorStep"] == 0
    vals = client.post(f"{BASE}/distinct", json={"base": "orders", "steps": steps[:2], "upto": 2, "column": "sgmt"}).json()
    assert vals == {"values": ["Corporate", "Retail", "Wholesale"], "more": False}


def test_saved_pivot_keeps_its_columns(client):
    steps = [{"op": "merge", "table": "customers", "left_on": "customer_id", "right_on": "id", "columns": ["city"]},
             {"op": "pivot", "column": "city", "value_column": "amt", "func": "sum"}]
    saved = client.put(f"{BASE}/amt_by_city", json={"base": "orders", "steps": steps}).json()
    assert saved["steps"][1]["values"] == ["Bengaluru", "Chennai", "Delhi", "Hyderabad", "Mumbai", "Pune"]


def test_ai_drafts_for_new_steps():
    from stardeck.transforms import StepDraft, draft_to_step
    split = draft_to_step(StepDraft(reasoning="r", op="split_column", column="name", delimiter=" ", split_mode="first",
                                    mode="upper"))  # `mode` (text case) must not leak into the split
    assert (split.mode, split.delimiter) == ("first", " ")
    cond = draft_to_step(StepDraft(reasoning="r", op="conditional_column", name="band", otherwise="Low",
                                   rules=[{"column": "amt", "operator": "gt", "value": "5", "result": "High"}]))
    assert cond.rules[0].result == "High" and cond.otherwise == "Low"
    piv = draft_to_step(StepDraft(reasoning="r", op="pivot", column="city", value_column="amt", func="sum",
                                  values=["Pune"]), "one column per city with the average amount")
    assert (piv.func, piv.values) == ("avg", [])  # user's word wins; invented values dropped



def test_pivot_columns_named_from_code_meanings(client):
    out = preview(client, "customers", [{"op": "pivot", "column": "sgmt", "value_column": "col1", "func": "avg"}])
    assert out["error"] is None, out["error"]
    assert out["columns"][-3:] == ["retail", "wholesale", "corporate"]


def test_prepared_table_does_not_reflag_passed_through_columns(client):
    save = client.put("/api/sources/retail-demo/transforms/orders_lite",
                      json={"base": "orders", "steps": [{"op": "keep_top", "n": 10}]})
    assert save.status_code == 200, save.text
    tables = {t["name"]: t for t in client.get("/api/sources/retail-demo").json()["tables"]}
    flag = lambda t, c: next(x["flag"] for x in tables[t]["columns"] if x["name"] == c)
    assert flag("orders", "sts") is not None      # the original is still flagged...
    assert flag("orders_lite", "sts") is None     # ...but not counted again on the prepared table
