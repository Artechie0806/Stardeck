"""Load uploaded CSV / Excel files into a SQLite database Stardeck can query.

Each CSV becomes one table; each non-empty Excel sheet becomes one table.
Column types are inferred (INTEGER, REAL, DATE, TEXT) so charts and SQL behave sensibly.
"""

import csv
import io
import re
import sqlite3
from datetime import date, datetime
from pathlib import Path

from openpyxl import load_workbook

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_DATETIME = re.compile(r"\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(:\d{2})?")
_NUMBER_CHARS = re.compile(r"[,\s₹$€£]")


class UploadError(ValueError):
    """Message is safe to show to users."""


def safe_identifier(raw: str, fallback: str, taken: set[str]) -> str:
    name = re.sub(r"[^0-9a-zA-Z_]+", "_", raw.strip()).strip("_").lower() or fallback
    if name[0].isdigit():
        name = f"_{name}"
    base, n = name, 2
    while name in taken:
        name, n = f"{base}_{n}", n + 1
    taken.add(name)
    return name


def _parse_number(v: str) -> int | float | None:
    s = _NUMBER_CHARS.sub("", v)
    try:
        return int(s)
    except ValueError:
        try:
            return float(s)
        except ValueError:
            return None


def _normalize(v) -> str | int | float | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.isoformat(sep=" ") if (v.hour, v.minute, v.second) != (0, 0, 0) else v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    if isinstance(v, bool):
        return int(v)
    if isinstance(v, (int, float)):
        return v
    s = str(v).strip()
    return s or None


def infer_column(values: list) -> tuple[str, list]:
    """Pick the narrowest type that fits every non-empty value, and convert the values."""
    present = [v for v in values if v is not None]
    if not present:
        return "TEXT", values
    if all(isinstance(v, (int, float)) or (isinstance(v, str) and _parse_number(v) is not None)
           for v in present):
        nums = [v if isinstance(v, (int, float)) or v is None else _parse_number(v) for v in values]
        is_int = all(isinstance(n, int) or (isinstance(n, float) and n.is_integer())
                     for n in nums if n is not None)
        return ("INTEGER", [int(n) if n is not None else None for n in nums]) if is_int else ("REAL", nums)
    if all(isinstance(v, str) and _DATE.fullmatch(v) for v in present):
        return "DATE", values
    if all(isinstance(v, str) and (_DATE.fullmatch(v) or _DATETIME.fullmatch(v)) for v in present):
        return "TIMESTAMP", values
    return "TEXT", [str(v) if v is not None else None for v in values]


def _read_csv(data: bytes) -> list[list]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("latin-1")
    try:
        dialect = csv.Sniffer().sniff(text[:20000], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return [row for row in csv.reader(io.StringIO(text), dialect) if any(c.strip() for c in row)]


def _read_excel(data: bytes) -> dict[str, list[list]]:
    wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    sheets = {}
    for ws in wb.worksheets:
        rows = [list(r) for r in ws.iter_rows(values_only=True)
                if any(c is not None and str(c).strip() for c in r)]
        if rows:
            sheets[ws.title] = rows
    wb.close()
    return sheets


def _write_table(db: sqlite3.Connection, name: str, rows: list[list]) -> None:
    header, body = rows[0], rows[1:]
    width = max(len(r) for r in rows)
    taken: set[str] = set()
    cols = [safe_identifier(str(header[i]) if i < len(header) and header[i] is not None else "",
                            f"column_{i + 1}", taken) for i in range(width)]
    columns = list(zip(*[[_normalize(r[i]) if i < len(r) else None for i in range(width)] for r in body]))
    if not columns:
        columns = [[] for _ in cols]
    typed = [infer_column(list(c)) for c in columns]
    ddl = ", ".join(f'"{c}" {t}' for c, (t, _) in zip(cols, typed))
    db.execute(f'CREATE TABLE "{name}" ({ddl})')
    placeholders = ", ".join("?" for _ in cols)
    db.executemany(f'INSERT INTO "{name}" VALUES ({placeholders})', list(zip(*[v for _, v in typed])))


def load_files(files: list[tuple[str, bytes]], out: Path) -> list[str]:
    """Write all files into a fresh SQLite DB at `out`. Returns the created table names."""
    if not files:
        raise UploadError("No files were uploaded.")
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    db = sqlite3.connect(tmp)
    taken: set[str] = set()
    try:
        for filename, data in files:
            stem, ext = Path(filename).stem, Path(filename).suffix.lower()
            if ext == ".csv" or ext == ".tsv" or ext == ".txt":
                tables = {stem: _read_csv(data)}
            elif ext in (".xlsx", ".xlsm"):
                try:
                    sheets = _read_excel(data)
                except Exception as exc:
                    raise UploadError(f"{filename}: not a readable Excel file ({exc})") from exc
                tables = {stem if len(sheets) == 1 else f"{stem}_{s}": r for s, r in sheets.items()}
            else:
                raise UploadError(f"{filename}: unsupported file type. Use .csv or .xlsx.")
            for raw_name, rows in tables.items():
                if len(rows) < 2:
                    raise UploadError(f"{filename}: needs a header row and at least one data row.")
                _write_table(db, safe_identifier(raw_name, "data", taken), rows)
        db.commit()
    except Exception:
        db.close()
        tmp.unlink(missing_ok=True)
        raise
    db.close()
    tmp.replace(out)
    return sorted(taken)
