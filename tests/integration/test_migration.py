from __future__ import annotations

from pathlib import Path

import psycopg
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory

from agenticqueue_api.config import get_sync_database_url

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG_PATH = REPO_ROOT / "apps" / "api" / "alembic.ini"


def alembic_config() -> Config:
    return Config(str(ALEMBIC_CONFIG_PATH))


def current_revision() -> str:
    with psycopg.connect(get_sync_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version_num FROM alembic_version")
            row = cursor.fetchone()
    assert row is not None
    return row[0]


def assert_foundation_state(expected_revision: str) -> None:
    with psycopg.connect(get_sync_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version_num FROM alembic_version")
            assert cursor.fetchone() == (expected_revision,)

            cursor.execute(
                "SELECT extname FROM pg_extension WHERE extname IN ('pgcrypto', 'vector') "
                "ORDER BY extname"
            )
            assert cursor.fetchall() == [("pgcrypto",), ("vector",)]

            cursor.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name = 'agenticqueue'"
            )
            assert cursor.fetchone() == ("agenticqueue",)


def assert_base_state() -> None:
    with psycopg.connect(get_sync_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass('public.alembic_version')")
            assert cursor.fetchone() == ("alembic_version",)

            cursor.execute("SELECT count(*) FROM alembic_version")
            assert cursor.fetchone() == (0,)

            cursor.execute(
                "SELECT extname FROM pg_extension WHERE extname IN ('pgcrypto', 'vector')"
            )
            assert cursor.fetchall() == []

            cursor.execute(
                "SELECT schema_name FROM information_schema.schemata "
                "WHERE schema_name = 'agenticqueue'"
            )
            assert cursor.fetchone() is None


def test_migration_reaches_head_with_extensions() -> None:
    config = alembic_config()
    expected_head = ScriptDirectory.from_config(config).get_current_head()
    assert expected_head is not None
    assert current_revision() == expected_head
    assert_foundation_state(expected_head)


def test_migration_is_reversible() -> None:
    config = alembic_config()
    downgrade(config, "base")
    assert_base_state()
    upgrade(config, "head")
    expected_head = ScriptDirectory.from_config(config).get_current_head()
    assert expected_head is not None
    assert_foundation_state(expected_head)
