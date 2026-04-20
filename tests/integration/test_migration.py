from __future__ import annotations

from pathlib import Path

import psycopg
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory

from agenticqueue_api.config import get_sync_database_url

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG_PATH = REPO_ROOT / "apps" / "api" / "alembic.ini"
ENTITY_TABLES = {
    "api_token",
    "actor",
    "artifact",
    "audit_log",
    "capability",
    "decision",
    "edge",
    "learning",
    "packet_version",
    "policy",
    "project",
    "run",
    "task",
    "workspace",
}
PRE_AUTH_TABLES = ENTITY_TABLES - {"api_token"}
EDGE_REVISION = "20260419_02"
PRE_AUDIT_HOOK_REVISION = "20260420_03"


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


def assert_entity_tables(expected_tables: set[str]) -> None:
    with psycopg.connect(get_sync_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'agenticqueue' ORDER BY table_name"
            )
            assert {row[0] for row in cursor.fetchall()} == expected_tables


def assert_audit_log_columns(expected_columns: set[str]) -> None:
    with psycopg.connect(get_sync_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'agenticqueue' AND table_name = 'audit_log' "
                "ORDER BY column_name"
            )
            assert {row[0] for row in cursor.fetchall()} == expected_columns


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
    assert_entity_tables(ENTITY_TABLES)
    assert_audit_log_columns(
        {
            "action",
            "actor_id",
            "after",
            "before",
            "created_at",
            "entity_id",
            "entity_type",
            "id",
            "trace_id",
        }
    )


def test_latest_migration_is_reversible() -> None:
    config = alembic_config()
    downgrade(config, "-1")
    assert current_revision() == PRE_AUDIT_HOOK_REVISION
    assert_foundation_state(PRE_AUDIT_HOOK_REVISION)
    assert_entity_tables(ENTITY_TABLES)
    assert_audit_log_columns(
        {
            "action",
            "actor_id",
            "created_at",
            "entity_id",
            "entity_type",
            "id",
            "payload",
        }
    )
    upgrade(config, "head")
    expected_head = ScriptDirectory.from_config(config).get_current_head()
    assert expected_head is not None
    assert_foundation_state(expected_head)
    assert_entity_tables(ENTITY_TABLES)
    assert_audit_log_columns(
        {
            "action",
            "actor_id",
            "after",
            "before",
            "created_at",
            "entity_id",
            "entity_type",
            "id",
            "trace_id",
        }
    )


def test_full_migration_stack_is_reversible_to_base() -> None:
    config = alembic_config()
    downgrade(config, "base")
    assert_base_state()
    upgrade(config, "head")
    expected_head = ScriptDirectory.from_config(config).get_current_head()
    assert expected_head is not None
    assert_foundation_state(expected_head)
    assert_entity_tables(ENTITY_TABLES)
