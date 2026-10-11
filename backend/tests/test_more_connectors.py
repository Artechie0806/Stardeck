"""SQL Server, Oracle, ClickHouse, Trino, Databricks, Snowflake, BigQuery, Redshift and DuckDB sources.

Only DuckDB runs here (it's a file). The others are checked as far as they can be without a server:
connection settings, stored secrets, and SQL that parses in each dialect.
"""

from datetime import date
from types import SimpleNamespace

import duckdb
import pytest
import sqlglot
from sqlalchemy import text
from sqlalchemy.exc import NoSuchModuleError

from stardeck import connectors
from stardeck.connectors import (
    BigQueryConfig,
    ClickHouseConfig,
    DatabricksConfig,
    DuckDBConfig,
    OracleConfig,
    RedshiftConfig,
    SnowflakeConfig,
    SourceConnectionError,
    SQLServerConfig,
    TrinoConfig,
    build_engine,
    build_url,
    describe,
)
from stardeck.query import DateRange, apply_date_filter
from stardeck.sqlgen import UnsafeSQLError, ensure_read_only
from stardeck.sqlutil import quote
from stardeck.transforms import Compiler, Recipe, steps_adapter

NETWORK = [
    (SQLServerConfig(host="db", database="sales", username="ro", password="pw"), "mssql+pymssql"),
    (OracleConfig(host="db", service_name="ORCL", username="ro", password="pw"), "oracle+oracledb"),
    (ClickHouseConfig(host="db", username="ro", password="pw"), "clickhousedb"),
    (TrinoConfig(host="db", catalog="hive", schema="sales", username="ro"), "trino"),
    (DatabricksConfig(host="x.cloud.databricks.com", http_path="/sql/1.0/warehouses/1", token="pw"), "databricks"),
    (SnowflakeConfig(account="org-acct", username="ro", password="pw", database="SALES"), "snowflake"),
    (BigQueryConfig(project="p", dataset="sales"), "bigquery"),
    (RedshiftConfig(host="db", database="dev", username="ro", password="pw"), "postgresql+psycopg"),
]


@pytest.mark.parametrize(("config", "driver"), NETWORK)
def test_connection_urls_and_descriptions(config, driver):
    url = build_url(config)
    assert url.drivername == driver
    assert "pw" not in describe(config)


def test_bigquery_caps_bytes_billed():
    url = build_url(BigQueryConfig(project="p", dataset="d", max_gb_billed=2))
    assert url.query["maximum_bytes_billed"] == str(2 * 1024 ** 3)


def test_missing_driver_names_the_install(monkeypatch):
    def missing(*_a, **_k):
        raise NoSuchModuleError("Can't load plugin: sqlalchemy.dialects:mssql.pymssql")

    monkeypatch.setattr(connectors, "create_engine", missing)
    with pytest.raises(SourceConnectionError, match="uv sync --extra sqlserver"):
        build_engine(SQLServerConfig(host="db", database="d", username="u"))


def test_trino_password_needs_https():
    with pytest.raises(SourceConnectionError, match="HTTPS"):
        build_engine(TrinoConfig(host="db", catalog="c", schema="s", username="u", password="pw"))


def test_bad_snowflake_account_and_oracle_schema_are_refused():
    with pytest.raises(SourceConnectionError, match="account"):
        build_engine(SnowflakeConfig(account="evil.com/x?", username="u", database="d"))
    with pytest.raises(SourceConnectionError, match="schema"):
        build_engine(OracleConfig(host="db", service_name="s", username="u", schema='x"; DROP'))


@pytest.mark.parametrize(("key", "message"), [
    ('{"type": "service_account", "token_uri": "http://169.254.169.254/token"}', "other than Google"),
    ('{"type": "external_account", "credential_source": {"url": "http://10.0.0.1/"}}', "service account key"),
    ('{"type": "service_account", "universe_domain": "evil.example"}', "other than Google"),
    ("not json", "valid JSON"),
])
def test_bigquery_keys_cant_send_token_requests_elsewhere(key, message):
    with pytest.raises(SourceConnectionError, match=message):
        build_engine(BigQueryConfig(project="p", dataset="d", credentials_json=key))


def test_every_secret_is_stored_encrypted(client):
    svc = client.app.state.stardeck.sources
    configs = {
        "token": DatabricksConfig(host="h.cloud.databricks.com", http_path="/p", token="tok-123"),
        "private_key": SnowflakeConfig(account="a", username="u", private_key="KEY-123", database="d"),
        "credentials_json": BigQueryConfig(project="p", dataset="d", credentials_json='{"k": "JSON-123"}'),
    }
    for field, config in configs.items():
        stored = svc._store_config(config)
        assert field not in stored and f"{field}_enc" in stored
        assert "123" not in str(stored)
        rec = SimpleNamespace(kind=config.kind, config=stored)
        assert getattr(svc.load_config(rec), field) == getattr(config, field)


def make_duckdb(path):
    db = duckdb.connect(str(path))
    db.execute("CREATE TABLE customers (id INTEGER, name VARCHAR, city VARCHAR)")
    db.execute("CREATE TABLE orders (id INTEGER, customer_id INTEGER, order_date DATE, amount DECIMAL(10,2), tags VARCHAR)")
    db.execute("INSERT INTO customers VALUES (1, 'Asha', 'Pune'), (2, 'Ravi', 'Mumbai')")
    db.execute("INSERT INTO orders VALUES (1, 1, '2025-01-15', 120.5, 'red,big'), (2, 1, '2025-02-03', 80, 'blue'), "
               "(3, 2, '2025-02-20', 200, 'red,small,new')")
    db.close()
    return path


def test_duckdb_is_read_only_and_closed_to_other_files(tmp_path):
    engine = build_engine(DuckDBConfig(path=str(make_duckdb(tmp_path / "shop.duckdb"))))
    (tmp_path / "secret.csv").write_text("a\n1\n")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM orders")).scalar() == 3
        with pytest.raises(Exception, match="read-only"):
            conn.execute(text("INSERT INTO orders VALUES (9, 1, '2025-01-01', 1, NULL)"))
    with engine.connect() as conn, pytest.raises(Exception, match="disabled"):
        conn.execute(text(f"SELECT * FROM '{(tmp_path / 'secret.csv').as_posix()}'"))
    engine.dispose()


def test_duckdb_source_end_to_end(client, tmp_path):
    path = make_duckdb(tmp_path / "shop.duckdb")
    res = client.post("/api/sources", json={"name": "Shop", "config": {"kind": "duckdb", "path": str(path)}})
    assert res.status_code == 201, res.text
    src = res.json()
    tables = {t["name"]: [c["name"] for c in t["columns"]] for t in src["tables"]}
    assert tables["orders"] == ["id", "customer_id", "order_date", "amount", "tags"]
    sql = "SELECT c.city, SUM(o.amount) AS revenue FROM orders o JOIN customers c ON c.id = o.customer_id GROUP BY c.city ORDER BY revenue DESC"
    res = client.post(f"/api/sources/{src['id']}/query", json={"sql": sql})
    assert res.json()["rows"] == [["Pune", 200.5], ["Mumbai", 200]]
    steps = [{"op": "split_column", "column": "tags", "delimiter": ",", "parts": 3},
             {"op": "date_part", "column": "order_date", "part": "month_name", "name": "month"},
             {"op": "sort", "column": "amount", "descending": True}, {"op": "keep_top", "n": 1}]
    out = client.post(f"/api/sources/{src['id']}/transforms/preview", json={"base": "orders", "steps": steps}).json()
    assert out["error"] is None, out["error"]
    assert out["rows"] == [[3, 2, "2025-02-20", 200, "red,small,new", "red", "small", "new", "Feb"]]


# Every step type, compiled for every dialect: the SQL must parse and pass the read-only check.
ALL_STEPS = [
    {"op": "filter_rows", "column": "tags", "operator": "contains", "value": "red"},
    {"op": "filter_rows", "column": "amount", "operator": "between", "value": 1, "value2": 500},
    {"op": "replace_values", "column": "customer_id", "mapping": {"1": "one"}},
    {"op": "fill_blanks", "column": "tags", "value": "none"},
    {"op": "text_transform", "column": "tags", "mode": "upper"},
    {"op": "calculated_column", "name": "double_amt", "expression": "amount * 2"},
    *({"op": "date_part", "column": "order_date", "part": p, "name": f"d_{p}"}
      for p in ("year", "quarter", "month", "month_name", "weekday", "day", "year_month")),
    {"op": "change_type", "column": "amount", "to": "integer"},
    {"op": "change_type", "column": "double_amt", "to": "decimal"},
    {"op": "change_type", "column": "order_date", "to": "date"},
    {"op": "split_column", "column": "tags", "delimiter": ",", "parts": 3},
    {"op": "split_column", "column": "tags", "delimiter": " - ", "mode": "first", "names": ["head", "tail"]},
    {"op": "conditional_column", "name": "size", "rules": [{"column": "amount", "operator": "gte", "value": 100,
                                                           "result": "big"}], "otherwise": "small"},
    {"op": "merge", "table": "customers", "left_on": "id", "right_on": "id", "columns": ["city"]},
    {"op": "sort", "column": "amount", "descending": True},
    {"op": "keep_top", "n": 10},
    {"op": "remove_duplicates", "columns": ["city"]},
    {"op": "group_by", "by": ["city"], "aggregations": [{"column": "amount", "func": "sum", "name": "total"},
                                                       {"column": None, "func": "count", "name": "n"}]},
]
COLUMNS = {"orders": ["id", "customer_id", "order_date", "amount", "tags"], "customers": ["id", "name", "city"]}
TYPES = {"orders": {"id": "INTEGER", "customer_id": "INTEGER", "order_date": "DATE", "amount": "DECIMAL",
                    "tags": "VARCHAR"}, "customers": {"id": "INTEGER", "name": "VARCHAR", "city": "VARCHAR"}}


@pytest.mark.parametrize("dialect", sorted(set(connectors.DIALECTS.values())))
def test_every_step_compiles_for_every_dialect(dialect):
    recipe = Recipe(name="prep", base="orders", steps=steps_adapter.validate_python(ALL_STEPS))
    sql, history = Compiler(dialect, COLUMNS, TYPES).compile(recipe)
    sqlglot.parse_one(sql, read=dialect)
    ensure_read_only(sql, dialect)
    assert history[-1] == ["city", "total", "n"]
    if dialect == "tsql":
        assert "OFFSET 0 ROWS" in sql  # ORDER BY inside a CTE
    assert "ORDER BY" in sql.split("LIMIT 10")[0].rsplit("AS (", 1)[-1]  # top N keeps its sort


@pytest.mark.parametrize(("dialect", "sql"), [
    ("duckdb", "SELECT * FROM read_csv('/etc/passwd')"),
    ("duckdb", "SELECT getenv('HOME')"),
    ("clickhouse", "SELECT * FROM url('http://169.254.169.254/', CSV)"),
    ("clickhouse", "SELECT * FROM file('users.xml')"),
    ("tsql", "SELECT * FROM OPENROWSET('SQLNCLI', 'Server=x', 'SELECT 1')"),
    ("oracle", "SELECT UTL_HTTP.REQUEST('http://example.com') FROM dual"),
    ("snowflake", "SELECT SYSTEM$WAIT(60)"),
])
def test_dialect_specific_escape_hatches_are_blocked(dialect, sql):
    with pytest.raises(UnsafeSQLError):
        ensure_read_only(sql, dialect)


def test_uppercase_dialects_quote_stored_names():
    assert quote("order_date", "oracle") == '"ORDER_DATE"'
    assert quote("size", "oracle") == '"size"'  # reserved: SQLAlchemy reports it as stored
    assert quote("MixedCase", "snowflake") == '"MixedCase"'
    assert quote("order_date", "tsql") == "[order_date]"


@pytest.mark.parametrize("dialect", ["oracle", "trino"])
def test_date_filter_uses_real_dates_where_strings_dont_compare(dialect):
    rng = DateRange(date(2025, 2, 1), date(2025, 2, 28))
    sql = apply_date_filter("SELECT COUNT(*) FROM orders", dialect, "orders", "order_date", rng)
    assert "'2025-02-01'" in sql and ("TO_DATE" in sql or "CAST" in sql)
