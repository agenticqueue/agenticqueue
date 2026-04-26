"""Pytest database isolation helpers for local mutating API tests."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

REPO_ROOT = Path(__file__).resolve().parents[1]
ASYNC_PREFIX = "postgresql+asyncpg://"
DEFAULT_TEST_DATABASE_NAME = "agenticqueue_test"
DEFAULT_TEST_DATABASE_URL = (
    "postgresql+asyncpg://agenticqueue:agenticqueue@127.0.0.1:"
    f"{os.getenv('AGENTICQUEUE_DB_PORT') or os.getenv('DB_PORT') or '54329'}"
    f"/{DEFAULT_TEST_DATABASE_NAME}"
)
TRUE_ENV_VALUES = {"1", "true", "yes", "on"}
PREPARED_ENV = "AGENTICQUEUE_PYTEST_TEST_DB_PREPARED"
_PREPARED_IN_PROCESS = False


def _truthy(value: str | None) -> bool:
    return value is not None and value.strip().lower() in TRUE_ENV_VALUES


def _drop_query_keys(url: str, keys: set[str]) -> str:
    parts = urlsplit(url)
    query_items = [
        (key, value)
        for key, value in parse_qsl(parts.query, keep_blank_values=True)
        if key not in keys
    ]
    return urlunsplit(parts._replace(query=urlencode(query_items)))


def _with_database_name(url: str, database_name: str) -> str:
    parts = urlsplit(_drop_query_keys(url, {"prepared_statement_cache_size"}))
    return urlunsplit(parts._replace(path=f"/{database_name}"))


def derive_pytest_database_url() -> str:
    """Return the non-dev database URL local pytest should use."""

    explicit = os.getenv("AGENTICQUEUE_DATABASE_URL_TEST") or os.getenv(
        "DATABASE_URL_TEST"
    )
    if explicit:
        return explicit

    configured = os.getenv("AGENTICQUEUE_DATABASE_URL") or os.getenv("DATABASE_URL")
    if configured:
        return _with_database_name(configured, DEFAULT_TEST_DATABASE_NAME)

    return DEFAULT_TEST_DATABASE_URL


def should_prepare_pytest_database() -> bool:
    """Return whether local pytest should create and use `agenticqueue_test`."""

    if _truthy(os.getenv("CI")):
        return False
    if _truthy(os.getenv("AGENTICQUEUE_ALLOW_DEV_DATABASE_TESTS")):
        return False
    if _truthy(os.getenv(PREPARED_ENV)):
        return False
    return True


def prepare_pytest_database() -> bool:
    """Prepare the local disposable pytest database when safety requires it."""

    global _PREPARED_IN_PROCESS
    if _PREPARED_IN_PROCESS:
        return False
    if not should_prepare_pytest_database():
        return False

    test_database_url = derive_pytest_database_url()
    os.environ["AGENTICQUEUE_USE_TEST_DATABASE"] = "1"
    os.environ["AGENTICQUEUE_DATABASE_URL_TEST"] = test_database_url
    os.environ["DATABASE_URL_TEST"] = test_database_url
    os.environ[PREPARED_ENV] = "1"
    _PREPARED_IN_PROCESS = True
    try:
        subprocess.run(
            [sys.executable, "apps/api/scripts/e2e_test_db.py", "setup"],
            cwd=REPO_ROOT,
            env=os.environ.copy(),
            check=True,
        )
    except Exception:
        _PREPARED_IN_PROCESS = False
        os.environ.pop(PREPARED_ENV, None)
        raise
    return True


def teardown_pytest_database(prepared_here: bool) -> None:
    """Optionally drop the disposable pytest database after local tests."""

    global _PREPARED_IN_PROCESS
    if not prepared_here:
        return
    if not _truthy(os.getenv("AGENTICQUEUE_PYTEST_DROP_TEST_DATABASE")):
        os.environ.pop(PREPARED_ENV, None)
        _PREPARED_IN_PROCESS = False
        return
    subprocess.run(
        [sys.executable, "apps/api/scripts/e2e_test_db.py", "teardown"],
        cwd=REPO_ROOT,
        env=os.environ.copy(),
        check=True,
    )
    os.environ.pop(PREPARED_ENV, None)
    _PREPARED_IN_PROCESS = False
