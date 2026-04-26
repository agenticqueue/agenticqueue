from __future__ import annotations

from urllib.parse import urlsplit

from test_support import db_isolation
from test_support.db_isolation import (
    derive_pytest_database_url,
    prepare_pytest_database,
    should_prepare_pytest_database,
)


def test_local_pytest_defaults_to_agenticqueue_test(monkeypatch) -> None:
    for name in (
        "AGENTICQUEUE_USE_TEST_DATABASE",
        "AGENTICQUEUE_DATABASE_URL",
        "DATABASE_URL",
        "AGENTICQUEUE_DATABASE_URL_TEST",
        "DATABASE_URL_TEST",
        "CI",
        "AGENTICQUEUE_ALLOW_DEV_DATABASE_TESTS",
        "AGENTICQUEUE_PYTEST_TEST_DB_PREPARED",
    ):
        monkeypatch.delenv(name, raising=False)

    assert should_prepare_pytest_database()
    assert urlsplit(derive_pytest_database_url()).path == "/agenticqueue_test"


def test_local_pytest_derives_test_database_from_configured_url(monkeypatch) -> None:
    monkeypatch.setenv(
        "AGENTICQUEUE_DATABASE_URL",
        "postgresql+asyncpg://agenticqueue:agenticqueue@127.0.0.1:64329/agenticqueue",
    )
    monkeypatch.delenv("AGENTICQUEUE_DATABASE_URL_TEST", raising=False)
    monkeypatch.delenv("DATABASE_URL_TEST", raising=False)
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("AGENTICQUEUE_ALLOW_DEV_DATABASE_TESTS", raising=False)

    derived_url = derive_pytest_database_url()

    assert urlsplit(derived_url).path == "/agenticqueue_test"
    assert "64329" in derived_url


def test_ci_keeps_the_ci_database(monkeypatch) -> None:
    monkeypatch.setenv("CI", "1")
    monkeypatch.setenv(
        "AGENTICQUEUE_DATABASE_URL",
        "postgresql+asyncpg://agenticqueue:agenticqueue@127.0.0.1:64329/agenticqueue",
    )

    assert should_prepare_pytest_database() is False


def test_explicit_dev_database_opt_out(monkeypatch) -> None:
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.setenv("AGENTICQUEUE_ALLOW_DEV_DATABASE_TESTS", "1")

    assert should_prepare_pytest_database() is False


def test_prepare_pytest_database_is_process_idempotent(monkeypatch) -> None:
    calls = []
    for name in (
        "AGENTICQUEUE_DATABASE_URL",
        "DATABASE_URL",
        "AGENTICQUEUE_DATABASE_URL_TEST",
        "DATABASE_URL_TEST",
        "CI",
        "AGENTICQUEUE_ALLOW_DEV_DATABASE_TESTS",
        "AGENTICQUEUE_PYTEST_TEST_DB_PREPARED",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(db_isolation, "_PREPARED_IN_PROCESS", False)
    monkeypatch.setattr(
        db_isolation.subprocess,
        "run",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert prepare_pytest_database() is True
    monkeypatch.delenv("AGENTICQUEUE_PYTEST_TEST_DB_PREPARED", raising=False)

    assert prepare_pytest_database() is False
    assert len(calls) == 1
