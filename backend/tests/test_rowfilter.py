"""Row filters and row-level security: whatever the SQL, restricted rows stay out."""

import pytest

from stardeck.context import Restriction
from stardeck.db import SourceRecord
from stardeck.principal import Principal
from stardeck.query import QueryError, UnsafeSQLError, run

PUNE_VIEWER = Principal(id="v", username="v", name="V", role="viewer",
                        rules=({"sourceId": "retail-demo", "table": "customers", "column": "city", "values": ["Pune"]},))
PUNE_EDITOR = Principal(id="e", username="e", name="E", role="editor", rules=PUNE_VIEWER.rules)


@pytest.fixture
def ctx_for(client):
    state = client.app.state.stardeck

    def make(who):
        with state.db.new_session("default") as db:
            return state.sources.context(db.get(SourceRecord, "retail-demo"), who)
    return make


def scalar(ctx, sql, **kw):
    return run(ctx, sql, **kw).rows[0][0]


def pune_orders(demo_db):
    return demo_db.execute("SELECT COUNT(*) FROM orders o JOIN customers c ON c.id = o.customer_id "
                           "WHERE c.city = 'Pune'").fetchone()[0]


def test_relationships_are_detected_from_foreign_keys(ctx_for):
    ctx = ctx_for(PUNE_EDITOR)
    keys = {r.key for r in ctx.relationships}
    assert {"orders.customer_id->customers.id", "order_items.order_id->orders.id",
            "order_items.product_id->products.id"} <= keys


def test_filter_on_customers_flows_to_orders_and_order_lines(ctx_for, demo_db):
    ctx = ctx_for(PUNE_EDITOR)  # editors: no security, only the dashboard filter
    city = Restriction(table="customers", column="city", values=("Pune",), key="f1")
    res = run(ctx, "SELECT COUNT(*) FROM orders", filters=[city])
    assert res.rows[0][0] == pune_orders(demo_db) and res.unaffected == []
    lines = demo_db.execute("SELECT COUNT(*) FROM order_items i JOIN orders o ON o.id = i.order_id "
                            "JOIN customers c ON c.id = o.customer_id WHERE c.city = 'Pune'").fetchone()[0]
    assert scalar(ctx, "SELECT COUNT(*) FROM order_items", filters=[city]) == lines  # two hops
    # Products aren't linked to customers (a product list), so the filter doesn't reach them.
    res = run(ctx, "SELECT COUNT(*) FROM products", filters=[city])
    assert res.unaffected == ["f1"] and res.rows[0][0] == demo_db.execute("SELECT COUNT(*) FROM products").fetchone()[0]


def test_cross_filter_on_an_expression(ctx_for, demo_db):
    ctx = ctx_for(PUNE_EDITOR)
    month = Restriction(table="orders", expr="strftime('%Y-%m', o.order_date)", value="2025-03", key="x")
    want = demo_db.execute("SELECT COUNT(*) FROM orders WHERE order_date LIKE '2025-03%'").fetchone()[0]
    assert scalar(ctx, "SELECT COUNT(*) FROM orders o", filters=[month]) == want


def test_null_and_multiple_values(ctx_for, demo_db):
    ctx = ctx_for(PUNE_EDITOR)
    two = Restriction(table="customers", column="city", values=("Pune", "Delhi"), key="k")
    want = demo_db.execute("SELECT COUNT(*) FROM customers WHERE city IN ('Pune', 'Delhi')").fetchone()[0]
    assert scalar(ctx, "SELECT COUNT(*) FROM customers", filters=[two]) == want
    none = Restriction(table="customers", column="city", values=(None,), key="k")
    assert scalar(ctx, "SELECT COUNT(*) FROM customers", filters=[none]) == 0


@pytest.mark.parametrize("sql", [
    "SELECT COUNT(*) FROM orders",
    "SELECT COUNT(*) FROM orders AS x",
    "SELECT COUNT(*) FROM (SELECT * FROM orders) t",
    "SELECT (SELECT COUNT(*) FROM orders)",
    "WITH t AS (SELECT * FROM orders) SELECT COUNT(*) FROM t",
    "SELECT COUNT(*) FROM (SELECT id FROM orders UNION ALL SELECT id FROM orders WHERE 1 = 0) u",
    "SELECT COUNT(*) FROM main.orders",
    "SELECT COUNT(*) FROM orders WHERE customer_id IN (SELECT id FROM customers)",
])
def test_viewer_rules_hold_whatever_the_sql(ctx_for, demo_db, sql):
    assert scalar(ctx_for(PUNE_VIEWER), sql) == pune_orders(demo_db)


def test_editors_see_everything(ctx_for, demo_db):
    assert scalar(ctx_for(PUNE_EDITOR), "SELECT COUNT(*) FROM orders") == \
        demo_db.execute("SELECT COUNT(*) FROM orders").fetchone()[0]


@pytest.mark.parametrize(("sql", "message"), [
    ("WITH customers AS (SELECT * FROM customers) SELECT COUNT(*) FROM customers", "reuse the table name"),
    ("SELECT name FROM sqlite_master", "no table called sqlite_master"),
    ("SELECT COUNT(*) FROM temp.orders", "outside this data source"),
    ("SELECT load_extension('x')", "isn't allowed"),
])
def test_viewer_sql_that_could_step_around_rules_is_refused(ctx_for, sql, message):
    with pytest.raises((UnsafeSQLError, QueryError), match=message):
        run(ctx_for(PUNE_VIEWER), sql)


def test_a_cte_named_like_a_table_still_filters_the_real_table():
    # In Postgres a non-recursive CTE can't see itself, so the inner `customers` is the real table and
    # must be filtered, while the outer reference is the CTE and is left alone. (SQLite rejects this
    # SQL outright, and strict mode refuses it for viewers.)
    import sqlglot

    from stardeck.rowfilter import apply_row_filters
    tree = sqlglot.parse_one("WITH customers AS (SELECT * FROM customers) SELECT COUNT(*) FROM customers",
                             read="postgres")
    city = Restriction(table="customers", column="city", values=("Pune",), key="f")
    out, applied = apply_row_filters(tree, "postgres", [city], [])
    sql = out.sql(dialect="postgres")
    assert applied == {"f"} and sql.count("city IN ('Pune')") == 1
    assert sql.startswith("WITH customers AS (SELECT * FROM (SELECT * FROM customers WHERE city IN ('Pune'))")
    assert sql.endswith("SELECT COUNT(*) FROM customers")


def test_filter_expressions_cannot_read_other_tables(ctx_for):
    sneaky = Restriction(table="orders", expr="(SELECT MAX(city) FROM customers)", value="x", key="x")
    with pytest.raises(UnsafeSQLError):
        run(ctx_for(PUNE_EDITOR), "SELECT COUNT(*) FROM orders", filters=[sneaky])


def test_empty_rule_means_no_rows(ctx_for):
    nobody = Principal(id="n", username="n", name="N", role="viewer",
                       rules=({"sourceId": "retail-demo", "table": "customers", "column": "city", "values": []},))
    assert scalar(ctx_for(nobody), "SELECT COUNT(*) FROM orders") == 0
