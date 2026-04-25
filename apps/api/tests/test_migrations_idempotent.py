from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path
import subprocess
import sys
import time

import psycopg
import pytest
import sqlalchemy as sa
from alembic.command import stamp, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory

from agenticqueue_api.config import get_direct_sync_database_url

REPO_ROOT = Path(__file__).resolve().parents[3]
ALEMBIC_CONFIG_PATH = REPO_ROOT / "apps" / "api" / "alembic.ini"
ALEMBIC_DIR = REPO_ROOT / "apps" / "api" / "alembic"
POSTGRES_IMAGE = "pgvector/pgvector:pg16"
POSTGRES_PASSWORD = "agenticqueue"

sys.path.insert(0, str(ALEMBIC_DIR))


def alembic_config() -> Config:
    return Config(str(ALEMBIC_CONFIG_PATH))


@pytest.fixture()
def temp_database(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    base_url = sa.engine.make_url(get_direct_sync_database_url())
    database_name = f"aq_migration_idem_{uuid.uuid4().hex[:12]}"
    container_name: str | None = None

    try:
        admin_url = base_url.set(database="postgres")
        _assert_admin_connects(_render_url(admin_url))
    except psycopg.OperationalError as error:
        admin_url, container_name = _start_postgres_container(error)

    test_url = admin_url.set(database=database_name)
    sync_test_url = _render_url(test_url)
    async_test_url = sync_test_url.replace("postgresql://", "postgresql+asyncpg://")

    with psycopg.connect(_render_url(admin_url), autocommit=True) as connection:
        connection.execute(f'CREATE DATABASE "{database_name}"')

    monkeypatch.setenv("AGENTICQUEUE_DATABASE_URL", async_test_url)
    try:
        yield sync_test_url
    finally:
        try:
            with psycopg.connect(_render_url(admin_url), autocommit=True) as connection:
                connection.execute(
                    "SELECT pg_terminate_backend(pid) "
                    "FROM pg_stat_activity WHERE datname = %s",
                    (database_name,),
                )
                connection.execute(f'DROP DATABASE IF EXISTS "{database_name}"')
        finally:
            if container_name:
                subprocess.run(
                    ["docker", "rm", "-f", container_name],
                    check=False,
                    capture_output=True,
                    text=True,
                )


def _assert_admin_connects(admin_url: str) -> None:
    with psycopg.connect(admin_url, autocommit=True) as connection:
        connection.execute("SELECT 1")


def _start_postgres_container(
    previous_error: psycopg.OperationalError,
) -> tuple[sa.URL, str]:
    container_name = f"aq-migration-idem-{uuid.uuid4().hex[:12]}"
    port = _start_container_on_available_port(container_name)
    admin_url = sa.engine.make_url(
        f"postgresql://agenticqueue:{POSTGRES_PASSWORD}@127.0.0.1:{port}/postgres"
    )
    deadline = time.monotonic() + 30
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            _assert_admin_connects(_render_url(admin_url))
            return admin_url, container_name
        except psycopg.OperationalError as error:
            last_error = error
            time.sleep(0.5)

    logs_result = subprocess.run(
        ["docker", "logs", container_name],
        check=False,
        capture_output=True,
        text=True,
    )
    subprocess.run(
        ["docker", "rm", "-f", container_name],
        check=False,
        capture_output=True,
        text=True,
    )
    pytest.fail(
        f"Docker fallback Postgres did not become ready: {last_error}\n"
        f"Postgres error: {previous_error}\n"
        f"Docker logs:\n{logs_result.stderr}\n{logs_result.stdout}"
    )


def _start_container_on_available_port(container_name: str) -> int:
    last_stderr = ""
    for port in range(55432, 55532):
        run_result = subprocess.run(
            [
                "docker",
                "run",
                "--name",
                container_name,
                "-e",
                "POSTGRES_DB=postgres",
                "-e",
                "POSTGRES_USER=agenticqueue",
                "-e",
                f"POSTGRES_PASSWORD={POSTGRES_PASSWORD}",
                "-p",
                f"127.0.0.1:{port}:5432",
                "-d",
                "--rm",
                POSTGRES_IMAGE,
            ],
            capture_output=True,
            text=True,
        )
        if run_result.returncode == 0:
            return port
        last_stderr = run_result.stderr
        if "port is already allocated" not in run_result.stderr:
            break

    pytest.fail(f"Could not start {POSTGRES_IMAGE}: {last_stderr}")

    raise AssertionError("unreachable")


def _render_url(url: sa.URL) -> str:
    return url.render_as_string(hide_password=False)


def test_migrations_can_rerun_after_head(temp_database: str) -> None:
    config = alembic_config()

    upgrade(config, "head")
    upgrade(config, "head")

    with psycopg.connect(temp_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version_num FROM alembic_version")
            assert cursor.fetchone() == (
                ScriptDirectory.from_config(config).get_current_head(),
            )


def test_partial_state_recovery(temp_database: str) -> None:
    config = alembic_config()
    script = ScriptDirectory.from_config(config)
    head_revision = script.get_current_head()
    base_revision = "20260423_28"
    assert script.get_revision(base_revision) is not None

    upgrade(config, base_revision)

    with psycopg.connect(temp_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("CREATE EXTENSION IF NOT EXISTS citext")
            cursor.execute(
                "ALTER TABLE agenticqueue.users ADD COLUMN IF NOT EXISTS email CITEXT"
            )
        connection.commit()

    stamp(config, base_revision)
    upgrade(config, "head")

    with psycopg.connect(temp_database) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version_num FROM alembic_version")
            assert cursor.fetchone() == (head_revision,)
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'agenticqueue' AND table_name = 'users'"
            )
            columns = {row[0] for row in cursor.fetchall()}
            assert "email" in columns
            assert "username" not in columns
