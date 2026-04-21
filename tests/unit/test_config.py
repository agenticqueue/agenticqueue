from __future__ import annotations

from agenticqueue_api import config


def test_get_database_url_defaults_to_pgbouncer_with_asyncpg_cache_disabled(
    monkeypatch,
) -> None:
    monkeypatch.delenv("AGENTICQUEUE_DATABASE_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert config.get_database_url() == (
        "postgresql+asyncpg://agenticqueue:agenticqueue@127.0.0.1:64329/"
        "agenticqueue?prepared_statement_cache_size=0"
    )


def test_get_database_url_preserves_existing_cache_override(monkeypatch) -> None:
    monkeypatch.setenv(
        "AGENTICQUEUE_DATABASE_URL",
        "postgresql+asyncpg://aq:aq@127.0.0.1:64329/aq?prepared_statement_cache_size=7",
    )

    assert config.get_database_url().endswith("prepared_statement_cache_size=7")


def test_sync_database_urls_strip_asyncpg_only_parameters(monkeypatch) -> None:
    monkeypatch.setenv(
        "AGENTICQUEUE_DATABASE_URL",
        "postgresql+asyncpg://aq:aq@127.0.0.1:64329/aq?prepared_statement_cache_size=0&sslmode=disable",
    )

    assert (
        config.get_sync_database_url()
        == "postgresql://aq:aq@127.0.0.1:64329/aq?sslmode=disable"
    )
    assert (
        config.get_sqlalchemy_sync_database_url()
        == "postgresql+psycopg://aq:aq@127.0.0.1:64329/aq?sslmode=disable"
    )


def test_direct_sync_database_url_honors_configured_direct_port(monkeypatch) -> None:
    monkeypatch.setenv(
        "AGENTICQUEUE_DATABASE_URL",
        "postgresql+asyncpg://aq:aq@127.0.0.1:64329/aq?prepared_statement_cache_size=0",
    )
    monkeypatch.setenv("AGENTICQUEUE_DB_PORT", "5432")

    assert (
        config.get_direct_sync_database_url() == "postgresql://aq:aq@127.0.0.1:5432/aq"
    )


def test_direct_database_urls_map_nonstandard_pgbouncer_ports(monkeypatch) -> None:
    monkeypatch.delenv("AGENTICQUEUE_DB_PORT", raising=False)
    monkeypatch.delenv("DB_PORT", raising=False)
    monkeypatch.setenv(
        "AGENTICQUEUE_DATABASE_URL",
        "postgresql+asyncpg://aq:aq@127.0.0.1:64331/aq?prepared_statement_cache_size=0",
    )

    assert (
        config.get_direct_database_url()
        == "postgresql+asyncpg://aq:aq@127.0.0.1:54331/aq?prepared_statement_cache_size=0"
    )
    assert (
        config.get_direct_sync_database_url()
        == "postgresql://aq:aq@127.0.0.1:54331/aq"
    )


def test_get_psycopg_connect_args_disables_prepared_statements() -> None:
    assert config.get_psycopg_connect_args() == {"prepare_threshold": None}
