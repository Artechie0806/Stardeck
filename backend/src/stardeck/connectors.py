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


def build_engine(config: SourceConfig, timeout_s: int = 30) -> Engine:
    url = build_url(config)
    match config:
        case PostgresConfig():
            engine = create_engine(url, pool_pre_ping=True, connect_args={
                "connect_timeout": 10,
                "options": f"-c default_transaction_read_only=on -c statement_timeout={timeout_s * 1000}",
            })
        case MySQLConfig():
            engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 10})

            @event.listens_for(engine, "connect")
            def _readonly(dbapi_conn, _record):
                with dbapi_conn.cursor() as cur:
                    cur.execute("SET SESSION TRANSACTION READ ONLY")
                    cur.execute(f"SET SESSION max_execution_time = {timeout_s * 1000}")
        case SQLiteConfig() | UploadConfig():
            if not Path(config.path).expanduser().is_file():
                raise SourceConnectionError(f"File not found: {config.path}")
            engine = create_engine(url, connect_args={"timeout": timeout_s})
    return engine


def check_connection(config: SourceConfig, timeout_s: int = 30) -> Engine:
    """Open an engine and prove it works, translating driver errors into readable messages."""
    try:
        engine = build_engine(config, timeout_s)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return engine
    except SourceConnectionError:
        raise
    except Exception as exc:  # driver errors vary widely; show the first line only
        root = exc.__cause__ or exc
        message = str(getattr(root, "orig", None) or root).strip().splitlines()[0]
        raise SourceConnectionError(f"Could not connect: {message}") from exc
