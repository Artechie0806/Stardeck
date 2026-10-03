import sqlite3

import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from stardeck.connectors import (
    MySQLConfig,
    PostgresConfig,
    SourceConnectionError,
    SQLiteConfig,
    build_engine,
    build_url,
    check_connection,
    describe,
)


def test_postgres_url_escapes_credentials():
    cfg = PostgresConfig(host="db", database="sales", username="ro@corp", password="p@ss/word")
    url = build_url(cfg)
    assert url.drivername == "postgresql+psycopg"
    assert url.password == "p@ss/word"
    assert "p@ss" not in describe(cfg)
    assert describe(cfg) == "ro@corp@db:5432/sales"


def test_postgres_schema_alias():
    cfg = PostgresConfig.model_validate({"host": "h", "database": "d", "username": "u", "schema": "analytics"})
    assert cfg.db_schema == "analytics"
    assert cfg.model_dump(by_alias=True)["schema"] == "analytics"


def test_mysql_url():
    assert build_url(MySQLConfig(host="h", database="d", username="u")).drivername == "mysql+pymysql"


def test_sqlite_is_read_only_at_connection_level(tmp_path):
    path = tmp_path / "x.sqlite"
    db = sqlite3.connect(path)
    db.execute("CREATE TABLE t (a INTEGER)")
    db.commit()
    db.close()
    engine = build_engine(SQLiteConfig(path=str(path)))
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM t")).scalar() == 0
        with pytest.raises(OperationalError, match="readonly"):
            conn.execute(text("INSERT INTO t VALUES (1)"))


def test_missing_sqlite_file_is_readable_error(tmp_path):
    with pytest.raises(SourceConnectionError, match="File not found"):
        check_connection(SQLiteConfig(path=str(tmp_path / "nope.sqlite")))


def test_unreachable_postgres_gives_one_line_message():
    cfg = PostgresConfig(host="127.0.0.1", port=9, database="d", username="u")
    with pytest.raises(SourceConnectionError) as info:
        check_connection(cfg)
    assert str(info.value).startswith("Could not connect:")
    assert "\n" not in str(info.value)
