"""Build read-only SQLAlchemy engines for user data sources.

Read-only is enforced in layers: the SQL parser (sqlgen.ensure_read_only) rejects anything
but SELECT, and every connection here is opened read-only at the database level too.
Users should still connect with a read-only database account.
"""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL, Engine

from stardeck.netguard import BlockedAddress, Guard


class PostgresConfig(BaseModel):
    kind: Literal["postgres"] = "postgres"
    host: str
    port: int = 5432
    database: str
    username: str
    password: str = ""
    db_schema: str = Field("public", alias="schema")
    sslmode: Literal["disable", "prefer", "require"] = "prefer"

    model_config = {"populate_by_name": True}


class MySQLConfig(BaseModel):
    kind: Literal["mysql"] = "mysql"
    host: str
    port: int = 3306
    database: str
    username: str
    password: str = ""


class SQLiteConfig(BaseModel):
    kind: Literal["sqlite"] = "sqlite"
    path: str


class UploadConfig(BaseModel):
    """CSV/Excel files, loaded into a Stardeck-managed SQLite file."""

    kind: Literal["csv"] = "csv"
    path: str
    files: list[str] = []


SourceConfig = Annotated[
    PostgresConfig | MySQLConfig | SQLiteConfig | UploadConfig, Field(discriminator="kind")
]

DIALECTS = {"postgres": "postgres", "mysql": "mysql", "sqlite": "sqlite", "csv": "sqlite"}


class SourceConnectionError(RuntimeError):
    """A data source could not be reached; message is safe to show to users."""


def describe(config: SourceConfig) -> str:
    """Short human-readable location, never including credentials."""
    match config:
        case PostgresConfig() | MySQLConfig():
            return f"{config.username}@{config.host}:{config.port}/{config.database}"
        case SQLiteConfig():
            return config.path
        case UploadConfig():
            return ", ".join(config.files) or "uploaded files"


def schema_of(config: SourceConfig) -> str | None:
    return config.db_schema if isinstance(config, PostgresConfig) else None


def build_url(config: SourceConfig) -> URL | str:
    match config:
        case PostgresConfig():
            return URL.create(
                "postgresql+psycopg", username=config.username, password=config.password or None,
                host=config.host, port=config.port, database=config.database,
                query={"sslmode": config.sslmode},
            )
        case MySQLConfig():
            return URL.create(
                "mysql+pymysql", username=config.username, password=config.password or None,
                host=config.host, port=config.port, database=config.database,
            )
        case SQLiteConfig() | UploadConfig():
            path = Path(config.path).expanduser().resolve()
            return f"sqlite:///file:{path}?mode=ro&uri=true"


# Small pools: a workspace's queries are already limited per source, and idle engines are dropped.
_POOL = {"pool_size": 2, "max_overflow": 2, "pool_recycle": 300, "pool_pre_ping": True}


def build_engine(config: SourceConfig, timeout_s: int = 30, guard: Guard | None = None) -> Engine:
    """`guard` (hosted): network databases must be on the public internet, checked on every connect."""
    url = build_url(config)
    if guard is not None and isinstance(config, PostgresConfig | MySQLConfig):
        try:
            guard.check_fields(config.host, config.database, config.username)
        except BlockedAddress as exc:
            raise SourceConnectionError(str(exc)) from exc
    match config:
        case PostgresConfig():
            engine = create_engine(url, **_POOL, connect_args={
                "connect_timeout": 10,
                "options": f"-c default_transaction_read_only=on -c statement_timeout={timeout_s * 1000}",
            })
        case MySQLConfig():
            engine = create_engine(url, **_POOL, connect_args={"connect_timeout": 10, "local_infile": False})

            @event.listens_for(engine, "connect")
            def _readonly(dbapi_conn, _record):
                with dbapi_conn.cursor() as cur:
                    cur.execute("SET SESSION TRANSACTION READ ONLY")
                    cur.execute(f"SET SESSION max_execution_time = {timeout_s * 1000}")
        case SQLiteConfig() | UploadConfig():
            if not Path(config.path).expanduser().is_file():
                raise SourceConnectionError(f"File not found: {config.path}")
            engine = create_engine(url, connect_args={"timeout": timeout_s})
    if guard is not None and isinstance(config, PostgresConfig | MySQLConfig):
        host, port, postgres = config.host, config.port, isinstance(config, PostgresConfig)

        @event.listens_for(engine, "do_connect")
        def _pin(_dialect, _record, _cargs, cparams):
            ip = guard.resolve(host, port)  # again on every new connection (pools reconnect)
            if postgres:
                cparams["hostaddr"] = ip  # libpq connects here and keeps `host` for TLS
            else:
                cparams["host"] = ip
    return engine


def check_connection(config: SourceConfig, timeout_s: int = 30, guard: Guard | None = None) -> Engine:
    """Open an engine and prove it works, translating driver errors into readable messages."""
    try:
        engine = build_engine(config, timeout_s, guard)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except SourceConnectionError:
        raise
    except BlockedAddress as exc:
        raise SourceConnectionError(str(exc)) from exc
    except Exception as exc:  # driver errors vary widely; show the first line only
        if isinstance(exc.__cause__, BlockedAddress) or isinstance(getattr(exc, "orig", None), BlockedAddress):
            raise SourceConnectionError(str(exc.__cause__ or exc.orig)) from exc
        root = exc.__cause__ or exc
        message = str(getattr(root, "orig", None) or root).strip().splitlines()[0]
        raise SourceConnectionError(f"Could not connect: {message}") from exc
