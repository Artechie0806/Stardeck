from stardeck.grain import check_fanout
from stardeck.schema import ColumnInfo, TableInfo


def t(name, cols, fks=()):
    return TableInfo(name=name, columns=[ColumnInfo(name=c, type="INT") for c in cols], foreign_keys=list(fks))


SCHEMA = [
    t("orders", ["id", "customer_id", "amt", "sts"], ["customer_id -> customers.id"]),
    t("order_items", ["id", "order_id", "product_id", "qty", "unit_price"],
      ["order_id -> orders.id", "product_id -> products.id"]),
    t("products", ["id", "category"]),
    t("customers", ["id", "city"]),
]


def test_flags_sum_of_one_side_after_joining_many_side():
    sql = ("SELECT p.category, SUM(o.amt) FROM orders o JOIN order_items oi ON o.id = oi.order_id "
           "JOIN products p ON p.id = oi.product_id GROUP BY p.category")
    msg = check_fanout(sql, "sqlite", SCHEMA)
    assert msg and "SUM(orders.amt)" in msg and "order_items" in msg


def test_unqualified_column_is_resolved():
    sql = "SELECT category, AVG(amt) FROM orders JOIN order_items ON orders.id = order_id JOIN products ON products.id = product_id GROUP BY category"
    assert check_fanout(sql, "sqlite", SCHEMA)


def test_sees_through_non_aggregating_subquery():
    sql = ("SELECT p.category, SUM(x.amt) FROM (SELECT id, amt FROM orders WHERE sts = 1) x "
           "JOIN order_items oi ON x.id = oi.order_id JOIN products p ON p.id = oi.product_id GROUP BY p.category")
    assert check_fanout(sql, "sqlite", SCHEMA)


def test_correct_queries_pass():
    ok = [
        # many-side measure
        ("SELECT p.category, SUM(oi.qty * oi.unit_price) FROM order_items oi JOIN orders o ON o.id = oi.order_id "
         "JOIN products p ON p.id = oi.product_id WHERE o.sts = 1 GROUP BY p.category"),
        # one-to-one direction: orders -> customers
        "SELECT c.city, SUM(o.amt) FROM orders o JOIN customers c ON c.id = o.customer_id GROUP BY c.city",
        # pre-aggregated subquery
        ("SELECT c.city, SUM(t.total) FROM (SELECT customer_id, SUM(amt) AS total FROM orders GROUP BY customer_id) t "
         "JOIN customers c ON c.id = t.customer_id GROUP BY c.city"),
    ]
    for sql in ok:
        assert check_fanout(sql, "sqlite", SCHEMA) is None, sql


def test_no_foreign_keys_means_no_opinion():
    assert check_fanout("SELECT SUM(a.x) FROM a JOIN b ON a.id = b.a_id", "sqlite", [t("a", ["x"]), t("b", ["a_id"])]) is None
