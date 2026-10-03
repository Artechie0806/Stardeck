import pytest

from stardeck.sqlgen import UnsafeSQLError, ensure_read_only


@pytest.mark.parametrize("sql", [
    "SELECT 1",
    "WITH t AS (SELECT 1 AS x) SELECT x FROM t",
    "SELECT a FROM t UNION SELECT b FROM u",
])
def test_allows_selects(sql):
    assert ensure_read_only(sql, "sqlite")


@pytest.mark.parametrize("sql", [
    "DELETE FROM orders",
    "DROP TABLE orders",
    "UPDATE orders SET amt = 0",
    "SELECT 1; DROP TABLE orders",
    "INSERT INTO t SELECT * FROM orders",
    "CREATE TABLE x AS SELECT * FROM orders",
])
def test_rejects_writes(sql):
    with pytest.raises(UnsafeSQLError):
        ensure_read_only(sql, "sqlite")
