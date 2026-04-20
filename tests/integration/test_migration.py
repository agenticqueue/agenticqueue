from __future__ import annotations

from pathlib import Path

import psycopg
from alembic.command import downgrade, upgrade
from alembic.config import Config
from alembic.script import ScriptDirectory

from agenticqueue_api.config import get_sync_database_url
from agenticqueue_api.pgvector import EMBEDDING_TABLES, embedding_index_name

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG_PATH = REPO_ROOT / "apps" / "api" / "alembic.ini"
ENTITY_TABLES = {
    "api_token",
    "actor",
    "artifact",
    "audit_log",
    "capability",
    "capability_grant",
    "decision",
    "edge",
    "idempotency_key",
    "learning",
    "packet_version",
    "policy",
    "project",
    "run",
    "task",
    "workspace",
}
PRE_CAPABILITY_GRANT_TABLES = ENTITY_TABLES - {"capability_grant"}
EDGE_REVISION = "20260419_02"
PRE_IDEMPOTENCY_TABLES = ENTITY_TABLES - {"idempotency_key"}
PRE_LATEST_REVISION = "20260420_09"


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


def assert_embedding_columns_and_indexes() -> None:
    with psycopg.connect(get_sync_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT table_name FROM information_schema.columns "
                "WHERE table_schema = 'agenticqueue' AND column_name = 'embedding' "
                "ORDER BY table_name"
            )
            assert [row[0] for row in cursor.fetchall()] == sorted(EMBEDDING_TABLES)

            cursor.execute(
                "SELECT indexname FROM pg_indexes "
                "WHERE schemaname = 'agenticqueue' "
                "ORDER BY indexname"
            )
            assert set(
                embedding_index_name(table_name) for table_name in EMBEDDING_TABLES
            ).issubset({row[0] for row in cursor.fetchall()})


def assert_embedding_columns_are_absent() -> None:
    with psycopg.connect(get_sync_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema = 'agenticqueue' AND column_name = 'embedding'"
            )
            assert cursor.fetchone() == (0,)


def assert_capability_columns(expected_columns: set[str]) -> None:
    with psycopg.connect(get_sync_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'agenticqueue' AND table_name = 'capability' "
                "ORDER BY column_name"
            )
            assert {row[0] for row in cursor.fetchall()} == expected_columns


def assert_capability_grant_columns(expected_columns: set[str]) -> None:
    with psycopg.connect(get_sync_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'agenticqueue' AND table_name = 'capability_grant' "
                "ORDER BY column_name"
            )
            assert {row[0] for row in cursor.fetchall()} == expected_columns


def assert_policy_columns(expected_columns: set[str]) -> None:
    with psycopg.connect(get_sync_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = 'agenticqueue' AND table_name = 'policy' "
                "ORDER BY column_name"
            )
            assert {row[0] for row in cursor.fetchall()} == expected_columns


def assert_policy_attachment_columns(
    table_name: str,
    *,
    expected_present: bool,
) -> None:
    with psycopg.connect(get_sync_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema = 'agenticqueue' AND table_name = %s "
                "AND column_name = 'policy_id'",
                (table_name,),
            )
            expected_count = 1 if expected_present else 0
            assert cursor.fetchone() == (expected_count,)


def assert_seeded_capability_keys(expected_keys: set[str]) -> None:
    with psycopg.connect(get_sync_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT key FROM agenticqueue.capability ORDER BY key")
            assert {row[0] for row in cursor.fetchall()} == expected_keys


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
    assert_capability_columns({"created_at", "description", "id", "key", "updated_at"})
    assert_capability_grant_columns(
        {
            "actor_id",
            "capability_id",
            "created_at",
            "expires_at",
            "granted_by_actor_id",
            "id",
            "revoked_at",
            "scope",
            "updated_at",
        }
    )
    assert_policy_columns(
        {
            "autonomy_tier",
            "body",
            "capabilities",
            "created_at",
            "hitl_required",
            "id",
            "name",
            "updated_at",
            "version",
            "workspace_id",
        }
    )
    assert_policy_attachment_columns("workspace", expected_present=True)
    assert_policy_attachment_columns("project", expected_present=True)
    assert_policy_attachment_columns("task", expected_present=True)
    assert_seeded_capability_keys(
        {
            "admin",
            "create_artifact",
            "promote_learning",
            "query_graph",
            "read_learnings",
            "read_repo",
            "run_tests",
            "search_memory",
            "trigger_handoff",
            "update_task",
            "write_branch",
            "write_learning",
        }
    )
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
            "redaction",
            "trace_id",
        }
    )
    assert_embedding_columns_and_indexes()


def test_latest_migration_is_reversible() -> None:
    config = alembic_config()
    downgrade(config, "-1")
    assert current_revision() == PRE_LATEST_REVISION
    assert_foundation_state(PRE_LATEST_REVISION)
    assert_entity_tables(ENTITY_TABLES)
    assert_capability_columns(
        {
            "created_at",
            "description",
            "id",
            "key",
            "updated_at",
        }
    )
    assert_capability_grant_columns(
        {
            "actor_id",
            "capability_id",
            "created_at",
            "expires_at",
            "granted_by_actor_id",
            "id",
            "revoked_at",
            "scope",
            "updated_at",
        }
    )
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
            "redaction",
            "trace_id",
        }
    )
    assert_policy_columns(
        {
            "autonomy_tier",
            "body",
            "capabilities",
            "created_at",
            "hitl_required",
            "id",
            "name",
            "updated_at",
            "version",
            "workspace_id",
        }
    )
    assert_policy_attachment_columns("workspace", expected_present=True)
    assert_policy_attachment_columns("project", expected_present=True)
    assert_policy_attachment_columns("task", expected_present=True)
    assert_embedding_columns_and_indexes()
    upgrade(config, "head")
    expected_head = ScriptDirectory.from_config(config).get_current_head()
    assert expected_head is not None
    assert_foundation_state(expected_head)
    assert_entity_tables(ENTITY_TABLES)
    assert_capability_columns({"created_at", "description", "id", "key", "updated_at"})
    assert_capability_grant_columns(
        {
            "actor_id",
            "capability_id",
            "created_at",
            "expires_at",
            "granted_by_actor_id",
            "id",
            "revoked_at",
            "scope",
            "updated_at",
        }
    )
    assert_policy_columns(
        {
            "autonomy_tier",
            "body",
            "capabilities",
            "created_at",
            "hitl_required",
            "id",
            "name",
            "updated_at",
            "version",
            "workspace_id",
        }
    )
    assert_policy_attachment_columns("workspace", expected_present=True)
    assert_policy_attachment_columns("project", expected_present=True)
    assert_policy_attachment_columns("task", expected_present=True)
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
            "redaction",
            "trace_id",
        }
    )
    assert_embedding_columns_and_indexes()


def test_full_migration_stack_is_reversible_to_base() -> None:
    config = alembic_config()
    downgrade(config, "base")
    assert_base_state()
    upgrade(config, "head")
    expected_head = ScriptDirectory.from_config(config).get_current_head()
    assert expected_head is not None
    assert_foundation_state(expected_head)
    assert_entity_tables(ENTITY_TABLES)
    assert_capability_columns({"created_at", "description", "id", "key", "updated_at"})
    assert_capability_grant_columns(
        {
            "actor_id",
            "capability_id",
            "created_at",
            "expires_at",
            "granted_by_actor_id",
            "id",
            "revoked_at",
            "scope",
            "updated_at",
        }
    )
    assert_policy_columns(
        {
            "autonomy_tier",
            "body",
            "capabilities",
            "created_at",
            "hitl_required",
            "id",
            "name",
            "updated_at",
            "version",
            "workspace_id",
        }
    )
    assert_policy_attachment_columns("workspace", expected_present=True)
    assert_policy_attachment_columns("project", expected_present=True)
    assert_policy_attachment_columns("task", expected_present=True)
    assert_embedding_columns_and_indexes()
