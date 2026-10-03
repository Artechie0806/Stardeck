"""Upgrading a database made before workspaces existed."""

import json
import sqlite3

from sqlalchemy import inspect

from stardeck.config import Settings
from stardeck.db import DashboardRecord, Database, SourceRecord, UserRecord, WorkspaceRecord

# The 1.4 schema, before workspaces.
OLD_SCHEMA = """
CREATE TABLE sources (id VARCHAR(64) PRIMARY KEY, name VARCHAR(200), kind VARCHAR(20), config JSON,
  definitions JSON, snapshot JSON, profiled_at DATETIME, profile_error TEXT, transforms JSON, created_at DATETIME);
CREATE TABLE dashboards (id VARCHAR(64) PRIMARY KEY, name VARCHAR(200), description TEXT, source_id VARCHAR(64),
  source_ids JSON, date_preset VARCHAR(20), date_range JSON, widgets JSON, filters JSON, created_at DATETIME,
  updated_at DATETIME);
CREATE TABLE users (id VARCHAR(64) PRIMARY KEY, username VARCHAR(120) UNIQUE, name VARCHAR(200),
  password_hash VARCHAR(300), role VARCHAR(20), rules JSON, disabled BOOLEAN, created_at DATETIME,
  last_login_at DATETIME);
CREATE TABLE app_settings (key VARCHAR(100) PRIMARY KEY, value JSON);
"""


def old_database(path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path)
    db.executescript(OLD_SCHEMA)
    db.execute("INSERT INTO sources (id, name, kind, config, definitions, snapshot, transforms) VALUES "
               "('retail-demo', 'Retail demo', 'sqlite', '{}', '{}', '[]', '[]'),"
               "('src-1234abcd', 'Sales DB', 'sqlite', '{}', '{}', '[]', '[]')")
    db.execute("INSERT INTO dashboards (id, name, description, source_id, widgets) VALUES "
               "('sales-overview', 'Sales overview', '', 'retail-demo', '[]'),"
               "('dash-1234abcd', 'Mine', '', 'src-1234abcd', '[]')")
    db.execute("INSERT INTO users (id, username, name, password_hash, role, disabled) VALUES "
               "('u-1', 'asha', 'Asha', 'x', 'admin', 0)")
    db.execute("INSERT INTO app_settings VALUES ('fiscal_year_start', ?)", (json.dumps(1),))
    db.commit()
    db.close()


def test_old_install_moves_into_the_default_workspace(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")
    old_database(settings.data_dir / "stardeck.db")
    db = Database(settings)
    with db.platform_session() as s:
        ws = s.get(WorkspaceRecord, "default")
        assert ws.plan == "self_hosted" and ws.settings == {"fiscal_year_start": 1}
        for model in (SourceRecord, DashboardRecord, UserRecord):
            assert {r.workspace_id for r in s.query(model)} == {"default"}
        assert {r.id: r.sample for r in s.query(SourceRecord)} == {"retail-demo": True, "src-1234abcd": False}
        assert {r.id: r.sample for r in s.query(DashboardRecord)} == {"sales-overview": True, "dash-1234abcd": False}
    for table in ("sources", "dashboards", "users"):
        assert f"ix_{table}_workspace_id" in {ix["name"] for ix in inspect(db.engine).get_indexes(table)}
    backups = list((settings.data_dir / "backups").glob("stardeck-pre-v1-*.db"))
    assert len(backups) == 1
    with sqlite3.connect(backups[0]) as old:  # the backup is the database as it was
        assert "workspace_id" not in {r[1] for r in old.execute("PRAGMA table_info(sources)")}

    Database(settings)  # a second start changes nothing
    assert len(list((settings.data_dir / "backups").glob("*.db"))) == 1


def test_fresh_install_needs_no_backup(tmp_path):
    settings = Settings(_env_file=None, data_dir=tmp_path / "data")
    db = Database(settings)
    with db.platform_session() as s:
        assert s.get(WorkspaceRecord, "default") is not None
    assert not (settings.data_dir / "backups").exists()
