# Adding a connector

A connector turns user-entered settings into a **read-only** SQLAlchemy engine. Everything else
(profiling, the widget engine, dashboards) works through SQLAlchemy and sqlglot, so a new
database mostly needs these steps.

1. **Config model** in `backend/src/stardeck/connectors.py`: a Pydantic model with a unique
   `kind` literal, added to the `SourceConfig` union. Name the password field `password`; it's
   encrypted at rest automatically and never returned by the API.
2. **URL** in `build_url()`: use `sqlalchemy.engine.URL.create(...)` so credentials are escaped.
3. **Read-only engine** in `build_engine()`: open sessions read-only *at the database level* and
   set a statement timeout. Postgres uses `default_transaction_read_only`, MySQL a `connect` event
   running `SET SESSION TRANSACTION READ ONLY`. If the database can't enforce this, say so in the
   docs and rely on `sqlgen.ensure_read_only`.
4. **Dialect** in `DIALECTS`: the [sqlglot dialect](https://sqlglot.com/sqlglot/dialects.html) name.
   The engine prompt, safety check, date filters and grain checker all use it.
5. **Description** in `describe()`: a short location string without credentials.
6. **Driver** dependency in `backend/pyproject.toml`.
7. **Frontend**: add the kind to `KINDS` and the form fields in `frontend/src/pages/SourcesPage.tsx`,
   and to `SourceConfig` in `frontend/src/api/types.ts`.
8. **Tests** in `backend/tests/test_connectors.py`: URL escaping, `describe()` never leaking the
   password, and if you can run the database locally, a write being refused.

Snowflake, BigQuery, ClickHouse and SQL Server are good candidates; their SQLAlchemy dialects and
sqlglot support already exist.
