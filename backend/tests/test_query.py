import sqlite3
from datetime import date

import pytest
from sqlalchemy import create_engine

from stardeck.context import SourceContext
from stardeck.definitions import Definitions
from stardeck.query import (
    DateRange,
    QueryError,
    UnsafeSQLError,
    apply_date_filter,
    cap_rows,
    run,
    run_widget_query,
)

Y2025 = DateRange(date(2025, 1, 1), date(2025, 12, 31))


def test_filter_uses_alias_and_keeps_or_precedence():
    sql = "SELECT COUNT(*) FROM orders o WHERE o.sts = 1 OR o.sts = 9"
    out = apply_date_filter(sql, "sqlite", "orders", "order_date", Y2025)
    assert out.endswith("WHERE (o.sts = 1 OR o.sts = 9) AND (o.order_date >= '2025-01-01' AND o.order_date < '2026-01-01')")


def test_filter_applies_inside_cte_and_subquery_only_where_table_is_read():
    sql = ("WITH t AS (SELECT customer_id, SUM(amt) AS s FROM orders GROUP BY customer_id) "
           "SELECT c.city, SUM(t.s) FROM t JOIN customers c ON c.id = t.customer_id GROUP BY c.city")
    out = apply_date_filter(sql, "sqlite", "orders", "order_date", Y2025)
    assert out.count("order_date") == 2  # one >= and one <, inside the CTE only
    assert "WHERE orders.order_date >= '2025-01-01'" in out


def test_filter_on_unrelated_table_is_an_error():
    with pytest.raises(QueryError, match="doesn't read orders"):
        apply_date_filter("SELECT COUNT(*) FROM customers", "sqlite", "orders", "order_date", Y2025)


def test_cap_rows_only_when_no_limit():
    assert cap_rows("SELECT a FROM t", "sqlite", 10).endswith("LIMIT 11")
    assert cap_rows("SELECT a FROM t LIMIT 5", "sqlite", 10).endswith("LIMIT 5")


@pytest.fixture
def engine(tmp_path):
    path = tmp_path / "d.sqlite"
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE orders (id INTEGER, amt NUMERIC, order_date TEXT);
        INSERT INTO orders VALUES (1, 10.5, '2024-06-01'), (2, 20, '2025-02-01'), (3, 5, '2025-12-31 18:00');
    """)
    db.commit()
    return create_engine(f"sqlite:///file:{path}?mode=ro&uri=true")


def ctx(engine, **kw):
    return SourceContext(id="t", name="t", engine=engine, dialect="sqlite", tables=[], defs=Definitions(), **kw)


def test_run_is_json_safe_and_truncates(engine):
    res = run(ctx(engine), "SELECT id, amt FROM orders ORDER BY id", max_rows=2)
    assert res.columns == ["id", "amt"] and res.rows == [[1, 10.5], [2, 20]] and res.truncated


def test_run_rejects_writes_and_reports_errors(engine):
    with pytest.raises(UnsafeSQLError):
        run(ctx(engine), "DELETE FROM orders")
    with pytest.raises(QueryError, match="no such column"):
        run(ctx(engine), "SELECT nope FROM orders")


def test_kpi_with_range_and_previous_period(engine):
    sql = "SELECT SUM(amt) AS revenue FROM orders"
    res = run_widget_query(ctx(engine), sql, kpi=True, date_filter=("orders", "order_date"),
                           rng=Y2025, compare=DateRange(date(2024, 1, 1), date(2024, 12, 31)))
    # The 18:00 timestamp on Dec 31 is included thanks to the exclusive end bound.
    assert res.columns == ["value", "previous"] and res.rows == [[25, 10.5]]


def test_sqlite_timeout(engine):
    slow = "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) SELECT COUNT(*) FROM n"
    with pytest.raises(QueryError, match="longer than 1s"):
        run(ctx(engine, timeout_s=1), slow)
