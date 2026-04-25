from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import psycopg
from psycopg import sql

ASYNC_PREFIX = "postgresql+asyncpg://"
SQLALCHEMY_SYNC_PREFIX = "postgresql+psycopg://"
PSYCOPG_PREFIX = "postgresql://"
REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_TEST_DATABASE_URL = (
    "postgresql+asyncpg://agenticqueue:agenticqueue@127.0.0.1:"
    f"{os.getenv('AGENTICQUEUE_DB_PORT') or os.getenv('DB_PORT') or '54329'}"
    "/agenticqueue_test"
)


def _drop_query_keys(url: str, keys: set[str]) -> str:
    parts = urlsplit(url)
    query_items = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in keys
    ]
    return urlunsplit(parts._replace(query=urlencode(query_items)))


def _as_psycopg_url(url: str) -> str:
    url = _drop_query_keys(url, {"prepared_statement_cache_size"})
    if url.startswith(ASYNC_PREFIX):
        return PSYCOPG_PREFIX + url[len(ASYNC_PREFIX) :]
    if url.startswith(SQLALCHEMY_SYNC_PREFIX):
        return PSYCOPG_PREFIX + url[len(SQLALCHEMY_SYNC_PREFIX) :]
    return url


def _derive_direct_port(port: int | None) -> int | None:
    configured_direct_port = os.getenv("AGENTICQUEUE_DB_PORT") or os.getenv("DB_PORT")
    if configured_direct_port:
        return int(configured_direct_port)
    if port == 6432:
        return 5432
    if port is not None:
        port_text = str(port)
        if len(port_text) == 5 and port_text.startswith("643"):
            return int(f"543{port_text[3:]}")
    return port


def _test_database_url() -> str:
    return (
        os.getenv("AGENTICQUEUE_DATABASE_URL_TEST")
        or os.getenv("DATABASE_URL_TEST")
        or DEFAULT_TEST_DATABASE_URL
    )


def _database_name(url: str) -> str:
    name = urlsplit(url).path.lstrip("/")
    if not name:
        raise RuntimeError("test database URL must include a database name")
    if name == "agenticqueue":
        raise RuntimeError("refusing to use the dev database as the e2e test database")
    return name


def _maintenance_url(test_url: str) -> str:
    parts = urlsplit(_as_psycopg_url(test_url))
    hostname = "db" if parts.hostname == "pgbouncer" else parts.hostname
    port = _derive_direct_port(parts.port)

    netloc = ""
    if parts.username:
        netloc += parts.username
        if parts.password:
            netloc += f":{parts.password}"
        netloc += "@"
    if hostname:
        netloc += hostname
    if port is not None:
        netloc += f":{port}"
    return urlunsplit(parts._replace(netloc=netloc, path="/postgres", query=""))


def _direct_database_url(test_url: str) -> str:
    parts = urlsplit(test_url)
    hostname = "db" if parts.hostname == "pgbouncer" else parts.hostname
    port = _derive_direct_port(parts.port)

    netloc = ""
    if parts.username:
        netloc += parts.username
        if parts.password:
            netloc += f":{parts.password}"
        netloc += "@"
    if hostname:
        netloc += hostname
    if port is not None:
        netloc += f":{port}"
    return urlunsplit(parts._replace(netloc=netloc))


def _recreate_database(test_url: str) -> None:
    database_name = _database_name(test_url)
    with psycopg.connect(_maintenance_url(test_url), autocommit=True) as connection:
        connection.execute(
            "SELECT pg_terminate_backend(pid) "
            "FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (database_name,),
        )
        connection.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(database_name)
            )
        )
        connection.execute(
            sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database_name))
        )


def _drop_database(test_url: str) -> None:
    database_name = _database_name(test_url)
    with psycopg.connect(_maintenance_url(test_url), autocommit=True) as connection:
        connection.execute(
            "SELECT pg_terminate_backend(pid) "
            "FROM pg_stat_activity "
            "WHERE datname = %s AND pid <> pg_backend_pid()",
            (database_name,),
        )
        connection.execute(
            sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(
                sql.Identifier(database_name)
            )
        )


def setup() -> None:
    test_url = _direct_database_url(_test_database_url())
    _recreate_database(test_url)
    env = {
        **os.environ,
        "AGENTICQUEUE_USE_TEST_DATABASE": "1",
        "AGENTICQUEUE_DATABASE_URL_TEST": test_url,
        "DATABASE_URL_TEST": test_url,
    }
    subprocess.run(
        [
            sys.executable,
            "-m",
            "alembic",
            "-c",
            "apps/api/alembic.ini",
            "upgrade",
            "head",
        ],
        cwd=REPO_ROOT,
        env=env,
        check=True,
    )


def teardown() -> None:
    _drop_database(_direct_database_url(_test_database_url()))


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"setup", "teardown"}:
        print("Usage: e2e_test_db.py setup|teardown", file=sys.stderr)
        return 2
    if sys.argv[1] == "setup":
        setup()
    else:
        teardown()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
