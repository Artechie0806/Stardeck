"""Build read-only SQLAlchemy engines for user data sources.

Read-only is enforced in layers: the SQL parser (sqlgen.ensure_read_only) rejects anything
but SELECT, and wherever the database supports it the connection is opened read-only too
(PostgreSQL, MySQL, SQLite, DuckDB, Oracle, ClickHouse). SQL Server, Redshift, Trino, Snowflake,
BigQuery and Databricks have no read-only session switch, so there the parser and the account's
own permissions are what protect the data. Users should always connect with a read-only account.

Drivers for databases other than PostgreSQL, MySQL and SQLite are optional extras
(`uv sync --extra sqlserver`, or `--extra all`); a missing one is reported when connecting.
"""

import json
import re
from pathlib import Path
from typing import Annotated, ClassVar, Literal

from pydantic import BaseModel, Field
from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import URL, Engine
from sqlalchemy.exc import NoSuchModuleError

from stardeck.netguard import BlockedAddress, Guard


class _Config(BaseModel):
    model_config = {"populate_by_name": True}

    label: ClassVar[str]
    dialect: ClassVar[str]  # sqlglot dialect the source's SQL is written in
    secrets: ClassVar[tuple[str, ...]] = ()  # fields stored encrypted and never sent back to the browser
    extra: ClassVar[str | None] = None  # the optional install that provides the driver
    # Read tables and columns from information_schema: the driver's own catalog queries are
    # written for PostgreSQL and fail on these look-alikes.
    info_schema: ClassVar[bool] = False

    def schema_name(self) -> str | None:
        """The schema (or database) whose tables the source shows; None for the default."""
        return None


class NetworkConfig(_Config):
    """A database server reached at host:port. Hosted Stardeck checks the address on every connect."""

    host: str
    port: int
    secrets: ClassVar[tuple[str, ...]] = ("password",)

    def guard_fields(self) -> tuple[str, ...]:
        """Other fields a driver might read as connection options (checked in hosted mode)."""
        return ()


class FileConfig(_Config):
    """A database file on the Stardeck server (hosted mode only allows its own uploads)."""

    path: str


class PostgresConfig(NetworkConfig):
    kind: Literal["postgres"] = "postgres"
    label: ClassVar[str] = "PostgreSQL"
    dialect: ClassVar[str] = "postgres"
    port: int = 5432
    database: str
    username: str
    password: str = ""
    db_schema: str = Field("public", alias="schema")
    sslmode: Literal["disable", "prefer", "require"] = "prefer"

    def schema_name(self) -> str | None:
        return self.db_schema

    def guard_fields(self) -> tuple[str, ...]:
        return self.database, self.username


class MySQLConfig(NetworkConfig):
    kind: Literal["mysql"] = "mysql"
    label: ClassVar[str] = "MySQL / MariaDB"
    dialect: ClassVar[str] = "mysql"
    port: int = 3306
    database: str
    username: str
    password: str = ""

    def schema_name(self) -> str | None:
        return self.database

    def guard_fields(self) -> tuple[str, ...]:
        return self.database, self.username


class RedshiftConfig(NetworkConfig):
    """Amazon Redshift speaks the PostgreSQL protocol; its SQL is its own dialect."""

    kind: Literal["redshift"] = "redshift"
    label: ClassVar[str] = "Amazon Redshift"
    dialect: ClassVar[str] = "redshift"
    info_schema: ClassVar[bool] = True
    port: int = 5439
    database: str
    username: str
    password: str = ""
    db_schema: str = Field("public", alias="schema")
    sslmode: Literal["disable", "prefer", "require"] = "require"

    def schema_name(self) -> str | None:
        return self.db_schema

    def guard_fields(self) -> tuple[str, ...]:
        return self.database, self.username


class SQLServerConfig(NetworkConfig):
    """Microsoft SQL Server and Azure SQL (Azure needs encryption "require")."""

    kind: Literal["sqlserver"] = "sqlserver"
    label: ClassVar[str] = "SQL Server"
    dialect: ClassVar[str] = "tsql"
    extra: ClassVar[str | None] = "sqlserver"
    port: int = 1433
    database: str
    username: str
    password: str = ""
    db_schema: str = Field("dbo", alias="schema")
    encryption: Literal["off", "request", "require"] = "request"

    def schema_name(self) -> str | None:
        return self.db_schema

    def guard_fields(self) -> tuple[str, ...]:
        return self.database, self.username


class OracleConfig(NetworkConfig):
    kind: Literal["oracle"] = "oracle"
    label: ClassVar[str] = "Oracle"
    dialect: ClassVar[str] = "oracle"
    extra: ClassVar[str | None] = "oracle"
    port: int = 1521
    service_name: str
    username: str
    password: str = ""
    # Whose tables to show; empty means the login's own schema.
    db_schema: str = Field("", alias="schema")

    def schema_name(self) -> str | None:
        return self.db_schema.lower() or None  # SQLAlchemy names Oracle schemas in lowercase

    def guard_fields(self) -> tuple[str, ...]:
        return self.service_name, self.username


class ClickHouseConfig(NetworkConfig):
    kind: Literal["clickhouse"] = "clickhouse"
    label: ClassVar[str] = "ClickHouse"
    dialect: ClassVar[str] = "clickhouse"
    extra: ClassVar[str | None] = "clickhouse"
    port: int = 8123  # the HTTP interface (8443 with TLS)
    database: str = "default"
    username: str = "default"
    password: str = ""
    secure: bool = False

    def schema_name(self) -> str | None:
        return self.database

    def guard_fields(self) -> tuple[str, ...]:
        return self.database, self.username


class TrinoConfig(NetworkConfig):
    """Trino and Starburst (Presto's successor)."""

    kind: Literal["trino"] = "trino"
    label: ClassVar[str] = "Trino"
    dialect: ClassVar[str] = "trino"
    extra: ClassVar[str | None] = "trino"
    port: int = 8080
    catalog: str
    db_schema: str = Field(alias="schema")
    username: str
    password: str = ""  # sent only over HTTPS
    https: bool = False

    def schema_name(self) -> str | None:
        return self.db_schema

    def guard_fields(self) -> tuple[str, ...]:
        return self.catalog, self.db_schema, self.username


class DatabricksConfig(NetworkConfig):
    kind: Literal["databricks"] = "databricks"
    label: ClassVar[str] = "Databricks"
    dialect: ClassVar[str] = "databricks"
    extra: ClassVar[str | None] = "databricks"
    secrets: ClassVar[tuple[str, ...]] = ("token",)
    port: int = 443
    http_path: str
    token: str = ""
    catalog: str = ""
    db_schema: str = Field("default", alias="schema")

    def schema_name(self) -> str | None:
        return self.db_schema

    def guard_fields(self) -> tuple[str, ...]:
        return self.http_path, self.catalog, self.db_schema


_ACCOUNT = re.compile(r"^[A-Za-z0-9_.-]{1,255}$")


class SnowflakeConfig(_Config):
    kind: Literal["snowflake"] = "snowflake"
    label: ClassVar[str] = "Snowflake"
    dialect: ClassVar[str] = "snowflake"
    extra: ClassVar[str | None] = "snowflake"
    secrets: ClassVar[tuple[str, ...]] = ("password", "private_key")
    account: str  # e.g. "myorg-myaccount"
    username: str
    password: str = ""
    private_key: str = ""  # PEM key-pair login (unencrypted PKCS#8), instead of a password
    warehouse: str = ""
    database: str
    db_schema: str = Field("PUBLIC", alias="schema")
    role: str = ""

    def schema_name(self) -> str | None:
        return self.db_schema.lower()  # SQLAlchemy names Snowflake schemas in lowercase


class BigQueryConfig(_Config):
    kind: Literal["bigquery"] = "bigquery"
    label: ClassVar[str] = "Google BigQuery"
    dialect: ClassVar[str] = "bigquery"
    extra: ClassVar[str | None] = "bigquery"
    secrets: ClassVar[tuple[str, ...]] = ("credentials_json",)
    project: str
    dataset: str
    credentials_json: str = ""  # a service account key; empty uses the server's default Google login
    location: str = ""
    # BigQuery bills by data scanned; a query that would scan more than this fails instead.
    max_gb_billed: float = 10

    def schema_name(self) -> str | None:
        return self.dataset  # `dataset.table` is the usual way to write a table


class SQLiteConfig(FileConfig):
    kind: Literal["sqlite"] = "sqlite"
    label: ClassVar[str] = "SQLite file"
    dialect: ClassVar[str] = "sqlite"


class DuckDBConfig(FileConfig):
    kind: Literal["duckdb"] = "duckdb"
    label: ClassVar[str] = "DuckDB file"
    dialect: ClassVar[str] = "duckdb"
    info_schema: ClassVar[bool] = True
    extra: ClassVar[str | None] = "duckdb"


class UploadConfig(FileConfig):
    """CSV/Excel files, loaded into a Stardeck-managed SQLite file."""

    kind: Literal["csv"] = "csv"
    label: ClassVar[str] = "Uploaded files"
    dialect: ClassVar[str] = "sqlite"
    files: list[str] = []


_CONFIGS = (PostgresConfig, MySQLConfig, RedshiftConfig, SQLServerConfig, OracleConfig, ClickHouseConfig,
            TrinoConfig, DatabricksConfig, SnowflakeConfig, BigQueryConfig, SQLiteConfig, DuckDBConfig,
            UploadConfig)

SourceConfig = Annotated[
    PostgresConfig | MySQLConfig | RedshiftConfig | SQLServerConfig | OracleConfig | ClickHouseConfig
    | TrinoConfig | DatabricksConfig | SnowflakeConfig | BigQueryConfig | SQLiteConfig | DuckDBConfig
    | UploadConfig,
    Field(discriminator="kind"),
]

DIALECTS = {c.model_fields["kind"].default: c.dialect for c in _CONFIGS}
SECRETS = {c.model_fields["kind"].default: c.secrets for c in _CONFIGS}
INFO_SCHEMA = {c.model_fields["kind"].default for c in _CONFIGS if c.info_schema}


class SourceConnectionError(RuntimeError):
    """A data source could not be reached; message is safe to show to users."""


def describe(config: SourceConfig) -> str:
    """Short human-readable location, never including credentials."""
    match config:
        case PostgresConfig() | MySQLConfig() | RedshiftConfig() | SQLServerConfig() | ClickHouseConfig():
            return f"{config.username}@{config.host}:{config.port}/{config.database}"
        case OracleConfig():
            return f"{config.username}@{config.host}:{config.port}/{config.service_name}"
        case TrinoConfig():
            return f"{config.username}@{config.host}:{config.port}/{config.catalog}.{config.db_schema}"
        case DatabricksConfig():
            return f"{config.host}{config.http_path}" + (f" ({config.catalog}.{config.db_schema})" if config.catalog else "")
        case SnowflakeConfig():
            return f"{config.username}@{config.account}/{config.database}.{config.db_schema}"
        case BigQueryConfig():
            return f"{config.project}.{config.dataset}"
        case SQLiteConfig() | DuckDBConfig():
            return config.path
        case UploadConfig():
            return ", ".join(config.files) or "uploaded files"


def schema_of(config: SourceConfig) -> str | None:
    """The schema to read tables from, as SQLAlchemy's inspector expects it."""
    if isinstance(config, PostgresConfig | RedshiftConfig | SQLServerConfig | OracleConfig | SnowflakeConfig):
        return config.schema_name()
    return None  # the connection's own database/schema


def build_url(config: SourceConfig) -> URL | str:
    match config:
        case PostgresConfig() | RedshiftConfig():
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
        case SQLServerConfig():
            return URL.create(
                "mssql+pymssql", username=config.username, password=config.password or None,
                host=config.host, port=config.port, database=config.database,
            )
        case OracleConfig():
            return URL.create(
                "oracle+oracledb", username=config.username, password=config.password or None,
                host=config.host, port=config.port, query={"service_name": config.service_name},
            )
        case ClickHouseConfig():
            return URL.create(
                "clickhousedb", username=config.username, password=config.password or None,
                host=config.host, port=config.port, database=config.database,
                query={"secure": "true"} if config.secure else {},
            )
        case TrinoConfig():
            return URL.create("trino", username=config.username, host=config.host, port=config.port,
                              database=f"{config.catalog}/{config.db_schema}")
        case DatabricksConfig():
            query = {"http_path": config.http_path, "schema": config.db_schema}
            if config.catalog:
                query["catalog"] = config.catalog
            return URL.create("databricks", username="token", password=config.token or None,
                              host=config.host, port=config.port, query=query)
        case SnowflakeConfig():
            query = {k: v for k, v in (("warehouse", config.warehouse), ("role", config.role)) if v}
            return URL.create("snowflake", username=config.username, password=config.password or None,
                              host=config.account, database=f"{config.database}/{config.db_schema}", query=query)
        case BigQueryConfig():
            query = {"maximum_bytes_billed": str(int(config.max_gb_billed * 1024 ** 3))}
            if config.location:
                query["location"] = config.location
            return URL.create("bigquery", host=config.project, database=config.dataset, query=query)
        case SQLiteConfig() | UploadConfig():
            path = Path(config.path).expanduser().resolve()
            return f"sqlite:///file:{path}?mode=ro&uri=true"
        case DuckDBConfig():
            return f"duckdb:///{Path(config.path).expanduser().resolve().as_posix()}"


# Small pools: a workspace's queries are already limited per source, and idle engines are dropped.
_POOL = {"pool_size": 2, "max_overflow": 2, "pool_recycle": 300, "pool_pre_ping": True}

_ORACLE_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_$#]{0,127}$")


def _snowflake_dialect() -> None:
    """snowflake-sqlalchemy 1.x subclasses a class SQLAlchemy 2.1 renamed (only used for ORM
    queries, which Stardeck never sends). Alias it before the dialect is imported."""
    from sqlalchemy.orm import context

    if not hasattr(context, "ORMSelectCompileState") and hasattr(context, "_ORMSelectCompileState"):
        context.ORMSelectCompileState = context._ORMSelectCompileState


def _private_key(pem: str) -> bytes:
    from cryptography.hazmat.primitives import serialization

    try:
        key = serialization.load_pem_private_key(pem.strip().encode(), password=None)
    except (ValueError, TypeError) as exc:
        raise SourceConnectionError("The private key isn't an unencrypted PEM key.") from exc
    return key.private_bytes(serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
                             serialization.NoEncryption())


def _service_account(text: str) -> dict:
    """A Google service account key. Google's library fetches tokens from URLs inside the key, so
    only the standard ones are accepted (anything else could point at the server's own network)."""
    try:
        info = json.loads(text)
    except json.JSONDecodeError as exc:
        raise SourceConnectionError("The service account key isn't valid JSON.") from exc
    if not isinstance(info, dict) or info.get("type") != "service_account":
        raise SourceConnectionError("Use a service account key (a JSON file with \"type\": \"service_account\").")
    if info.get("token_uri", "https://oauth2.googleapis.com/token") != "https://oauth2.googleapis.com/token"             or info.get("universe_domain", "googleapis.com") != "googleapis.com":
        raise SourceConnectionError("This service account key points at a server other than Google's.")
    return info


def _create(config: SourceConfig, url: URL | str, **kwargs) -> Engine:
    try:
        return create_engine(url, **kwargs)
    except (ImportError, NoSuchModuleError) as exc:
        hint = f" Install it with: uv sync --extra {config.extra}" if config.extra else ""
        raise SourceConnectionError(f"The {config.label} driver isn't installed on this server.{hint}") from exc


def build_engine(config: SourceConfig, timeout_s: int = 30, guard: Guard | None = None) -> Engine:
    """`guard` (hosted): network databases must be on the public internet, checked on every connect."""
    url = build_url(config)
    if guard is not None and isinstance(config, NetworkConfig):
        try:
            guard.check_fields(config.host, *config.guard_fields())
        except BlockedAddress as exc:
            raise SourceConnectionError(str(exc)) from exc
    if isinstance(config, SnowflakeConfig) and not _ACCOUNT.match(config.account):
        raise SourceConnectionError("Use the account identifier, e.g. myorg-myaccount.")
    match config:
        case PostgresConfig():
            engine = _create(config, url, **_POOL, connect_args={
                "connect_timeout": 10,
                "options": f"-c default_transaction_read_only=on -c statement_timeout={timeout_s * 1000}",
            })
        case RedshiftConfig():
            # Redshift rejects startup options, so the timeout is set once connected.
            engine = _create(config, url, **_POOL, connect_args={"connect_timeout": 10})

            @event.listens_for(engine, "connect")
            def _redshift_timeout(dbapi_conn, _record):
                with dbapi_conn.cursor() as cur:
                    cur.execute(f"SET statement_timeout TO {timeout_s * 1000}")
                dbapi_conn.commit()
        case MySQLConfig():
            engine = _create(config, url, **_POOL, connect_args={"connect_timeout": 10, "local_infile": False})

            @event.listens_for(engine, "connect")
            def _readonly(dbapi_conn, _record):
                with dbapi_conn.cursor() as cur:
                    cur.execute("SET SESSION TRANSACTION READ ONLY")
                    cur.execute(f"SET SESSION max_execution_time = {timeout_s * 1000}")
        case SQLServerConfig():
            # read_only asks for ApplicationIntent=ReadOnly (routes to a readable replica if there is one).
            engine = _create(config, url, **_POOL, connect_args={
                "login_timeout": 10, "timeout": timeout_s, "encryption": config.encryption, "read_only": True,
            })
        case OracleConfig():
            if config.db_schema and not _ORACLE_NAME.match(config.db_schema):
                raise SourceConnectionError("The schema should be a plain Oracle name, like SALES.")
            engine = _create(config, url, **_POOL, connect_args={"tcp_connect_timeout": 10})
            schema = config.db_schema

            @event.listens_for(engine, "connect")
            def _session(dbapi_conn, _record):
                dbapi_conn.call_timeout = timeout_s * 1000
                if schema:
                    with dbapi_conn.cursor() as cur:
                        cur.execute(f'ALTER SESSION SET CURRENT_SCHEMA = "{schema.upper()}"')

            @event.listens_for(engine, "checkout")
            def _readonly(dbapi_conn, _record, _proxy):
                # Lasts one transaction; connections are rolled back when returned to the pool.
                with dbapi_conn.cursor() as cur:
                    cur.execute("SET TRANSACTION READ ONLY")
        case ClickHouseConfig():
            # Arguments the client doesn't know are server settings. readonly=2: no writes or DDL,
            # but the driver may still adjust per-query settings.
            engine = _create(config, url, **_POOL, connect_args={
                "connect_timeout": 10, "readonly": 2, "max_execution_time": timeout_s,
                # SELECT a AS b, b AS c: c is the column b, as in every other database, not the alias.
                "prefer_column_name_to_alias": 1,
            })
        case TrinoConfig():
            if config.password and not config.https:
                raise SourceConnectionError("Trino only accepts a password over HTTPS. Turn on HTTPS.")
            args = {"http_scheme": "https" if config.https else "http",
                    "session_properties": {"query_max_run_time": f"{timeout_s}s"}, "request_timeout": timeout_s + 10}
            if config.password:
                from trino.auth import BasicAuthentication

                args["auth"] = BasicAuthentication(config.username, config.password)
            engine = _create(config, url, **_POOL, connect_args=args)
        case DatabricksConfig():
            engine = _create(config, url, **_POOL, connect_args={
                "session_configuration": {"STATEMENT_TIMEOUT": str(timeout_s)},
            })
        case SnowflakeConfig():
            _snowflake_dialect()
            args: dict = {"login_timeout": 15, "application": "Stardeck",
                          "session_parameters": {"STATEMENT_TIMEOUT_IN_SECONDS": timeout_s, "QUERY_TAG": "stardeck"}}
            if config.private_key:
                args["private_key"] = _private_key(config.private_key)
            engine = _create(config, url, **_POOL, connect_args=args)
        case BigQueryConfig():
            extra = {}
            if config.credentials_json.strip():
                extra["credentials_info"] = _service_account(config.credentials_json)
            engine = _create(config, url, **_POOL, **extra)
        case SQLiteConfig() | UploadConfig():
            if not Path(config.path).expanduser().is_file():
                raise SourceConnectionError(f"File not found: {config.path}")
            engine = _create(config, url, connect_args={"timeout": timeout_s})
        case DuckDBConfig():
            if not Path(config.path).expanduser().is_file():
                raise SourceConnectionError(f"File not found: {config.path}")
            # No reading other files or URLs from a query (read_csv('/etc/...'), ATTACH, httpfs).
            engine = _create(config, url, connect_args={
                "read_only": True, "config": {"enable_external_access": False},
            })
    if guard is not None and isinstance(config, NetworkConfig):
        _pin(engine, config, guard)
    return engine


def _pin(engine: Engine, config: NetworkConfig, guard: Guard) -> None:
    """Resolve and check the host on every new connection (pools reconnect), and connect to the
    checked address where the driver allows it, so DNS can't switch to a private one in between."""
    host, port = config.host, config.port

    @event.listens_for(engine, "do_connect")
    def _check(_dialect, _record, _cargs, cparams):
        ip = guard.resolve(host, port)
        match config:
            case PostgresConfig() | RedshiftConfig():
                cparams["hostaddr"] = ip  # libpq connects here and keeps `host` for TLS
            case MySQLConfig():
                cparams["host"] = ip
            case SQLServerConfig():
                cparams["host"] = f"[{ip}]:{port}" if ":" in ip else f"{ip}:{port}"
            case OracleConfig():
                cparams["dsn"] = re.sub(r"\(HOST=[^)]*\)", f"(HOST={ip})", cparams["dsn"], count=1)
            case ClickHouseConfig():
                cparams["host"] = ip
                cparams["server_host_name"] = host  # TLS still checks the real name
            # Trino and Databricks connect over HTTPS by name; the check above still runs first.


def check_connection(config: SourceConfig, timeout_s: int = 30, guard: Guard | None = None) -> Engine:
    """Open an engine and prove it works, translating driver errors into readable messages."""
    try:
        engine = build_engine(config, timeout_s, guard)
        with engine.connect() as conn:
            conn.execute(text("SELECT 1 FROM DUAL" if isinstance(config, OracleConfig) else "SELECT 1"))
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
