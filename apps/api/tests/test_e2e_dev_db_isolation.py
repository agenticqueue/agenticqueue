from __future__ import annotations

import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

import sqlalchemy as sa

from agenticqueue_api.config import get_database_url, get_sqlalchemy_sync_database_url

REPO_ROOT = Path(__file__).resolve().parents[3]
AUTH_TABLES = ("users", "auth_audit_log", "actor")
TEST_DATABASE_ENV_VARS = (
    "AGENTICQUEUE_USE_TEST_DATABASE",
    "AGENTICQUEUE_DATABASE_URL_TEST",
    "DATABASE_URL_TEST",
)


@contextmanager
def _dev_database_env() -> Iterator[None]:
    previous = {name: os.environ.get(name) for name in TEST_DATABASE_ENV_VARS}
    for name in TEST_DATABASE_ENV_VARS:
        os.environ.pop(name, None)
    try:
        yield
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _auth_row_counts() -> dict[str, int]:
    with _dev_database_env():
        engine = sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)
    try:
        with engine.connect() as connection:
            return {
                table_name: connection.execute(
                    sa.text(f"SELECT count(*) FROM agenticqueue.{table_name}")
                ).scalar_one()
                for table_name in AUTH_TABLES
            }
    finally:
        engine.dispose()


def _pnpm_command() -> list[str]:
    return ["pnpm.cmd" if os.name == "nt" else "pnpm", "--filter", "web", "test:e2e"]


def test_playwright_e2e_uses_test_db_and_leaves_dev_auth_tables_unchanged(
    monkeypatch,
) -> None:
    direct_db_port = (
        os.getenv("AGENTICQUEUE_DB_PORT") or os.getenv("DB_PORT") or "54329"
    )
    test_database_url = (
        "postgresql+asyncpg://agenticqueue:agenticqueue@127.0.0.1:"
        f"{direct_db_port}/agenticqueue_test"
    )
    with monkeypatch.context() as env:
        env.setenv("AGENTICQUEUE_USE_TEST_DATABASE", "1")
        env.setenv("DATABASE_URL_TEST", test_database_url)
        configured_url = get_database_url()

    assert urlsplit(configured_url).path == "/agenticqueue_test"

    before_counts = _auth_row_counts()
    result = subprocess.run(
        _pnpm_command(),
        cwd=REPO_ROOT,
        env={
            **os.environ,
            "AQ_E2E_AUTH_API_PORT": "3137",
            "AQ_E2E_WEB_PORT": "3015",
            "CI": "1",
            "NEXT_TELEMETRY_DISABLED": "1",
        },
        capture_output=True,
        check=False,
        text=True,
        timeout=180,
    )
    after_counts = _auth_row_counts()

    assert result.returncode == 0, result.stdout + result.stderr
    assert after_counts == before_counts
