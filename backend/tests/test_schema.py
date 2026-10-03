from sqlalchemy import create_engine, text

from stardeck.definitions import ColumnDef, Definitions, TableDef
from stardeck.schema import introspect, render_schema


def make_engine():
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE t (id INTEGER PRIMARY KEY, col1 INTEGER, secret TEXT, sts INTEGER)"))
        conn.execute(text("INSERT INTO t VALUES (1, 30, 'x', 1), (2, 40, 'y', 9)"))
    return engine


def test_render_without_definitions_shows_samples():
    out = render_schema(introspect(make_engine()))
    assert "TABLE t (2 rows)" in out
    assert "col1 INTEGER -- e.g. 30, 40" in out


def test_render_merges_definitions_and_hides_columns():
    defs = Definitions(tables={"t": TableDef(description="Things", columns={
        "col1": ColumnDef(description="Customer age", unit="years"),
        "secret": ColumnDef(hidden=True),
        "sts": ColumnDef(description="Status", values={"1": "Completed", "9": "Refunded"}),
    })})
    out = render_schema(introspect(make_engine()), defs)
    assert "-- Things" in out
    assert "col1 INTEGER -- Customer age; unit: years" in out
    assert "secret" not in out
    assert "values: 1=Completed, 9=Refunded" in out


def test_load_yaml_coerces_value_keys(tmp_path):
    p = tmp_path / "d.yaml"
    p.write_text("tables:\n  t:\n    columns:\n      sts: {values: {1: Completed}}\n")
    assert Definitions.load(p).column("t", "sts").values == {"1": "Completed"}


def test_flag_column_heuristic():
    from stardeck.schema import flag_column
    assert flag_column("col1", False) == "Generic name gives no hint of meaning"
    assert flag_column("sts", False) == "Short or abbreviated name"
    assert flag_column("qty_oh", False) == "Abbreviated name"
    assert flag_column("wh_cd", False) == "Abbreviated name"
    for fine in ("customer_id", "order_date", "revenue", "city", "unit_price"):
        assert flag_column(fine, False) is None
    assert flag_column("sts", True) is None
