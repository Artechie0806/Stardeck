import io
import sqlite3
from datetime import date

import pytest
from openpyxl import Workbook

from stardeck.uploads import UploadError, infer_column, load_files


def test_infer_types():
    assert infer_column(["1", "2", None])[0] == "INTEGER"
    assert infer_column(["1.5", "2"])[0] == "REAL"
    assert infer_column(["₹1,200", "300"]) == ("INTEGER", [1200, 300])
    assert infer_column(["2025-01-02", None])[0] == "DATE"
    assert infer_column(["a", "1"])[0] == "TEXT"


def test_csv_and_excel_become_tables(tmp_path):
    csv_bytes = b"Order ID;Amount (INR);Order Date\n1;1200;2025-01-02\n2;80.5;2025-02-03\n"
    wb = Workbook()
    ws = wb.active
    ws.title = "Customers"
    ws.append(["id", "name", "joined"])
    ws.append([1, "Asha", date(2024, 5, 1)])
    ws.append([2, "Ravi", date(2024, 6, 9)])
    buf = io.BytesIO()
    wb.save(buf)

    out = tmp_path / "u.sqlite"
    tables = load_files([("2025 orders.csv", csv_bytes), ("crm.xlsx", buf.getvalue())], out)
    assert tables == ["_2025_orders", "crm"]
    db = sqlite3.connect(out)
    cols = {r[1]: r[2] for r in db.execute('PRAGMA table_info("_2025_orders")')}
    assert cols == {"order_id": "INTEGER", "amount_inr": "REAL", "order_date": "DATE"}
    assert db.execute('SELECT SUM(amount_inr) FROM "_2025_orders"').fetchone()[0] == 1280.5
    assert db.execute('SELECT name, joined FROM crm ORDER BY id').fetchall() == [("Asha", "2024-05-01"), ("Ravi", "2024-06-09")]


def test_rejects_unsupported_and_empty(tmp_path):
    with pytest.raises(UploadError, match="unsupported"):
        load_files([("x.pdf", b"%PDF")], tmp_path / "a.sqlite")
    with pytest.raises(UploadError, match="header row"):
        load_files([("x.csv", b"only,header\n")], tmp_path / "b.sqlite")
    assert not (tmp_path / "b.sqlite").exists()
