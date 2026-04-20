from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator

import psycopg
import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from agenticqueue_api.config import (
    get_sqlalchemy_sync_database_url,
    get_sync_database_url,
)
from agenticqueue_api.models.artifact import ArtifactRecord
from agenticqueue_api.models.decision import DecisionRecord
from agenticqueue_api.models.learning import LearningRecord
from agenticqueue_api.models.project import ProjectRecord
from agenticqueue_api.models.task import TaskRecord
from agenticqueue_api.models.workspace import WorkspaceRecord
from agenticqueue_api.search import (
    SEARCH_DOCUMENT_COLUMN_NAME,
    search_document_index_name,
    search_text_trgm_index_name,
    search_trigram_column_name,
)

TRUNCATE_TABLES = (
    "artifact",
    "decision",
    "run",
    "learning",
    "task",
    "project",
    "workspace",
    "audit_log",
)


def _deterministic_uuid(label: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"https://agenticqueue.ai/tests/{label}")


def _utc(iso_value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(iso_value.replace("Z", "+00:00"))


def _truncate_tables(engine: Engine) -> None:
    with engine.begin() as connection:
        qualified_tables = ", ".join(
            f"agenticqueue.{table_name}" for table_name in TRUNCATE_TABLES
        )
        connection.execute(
            sa.text(f"TRUNCATE TABLE {qualified_tables} RESTART IDENTITY CASCADE")
        )


def _seed_task(session: Session) -> uuid.UUID:
    workspace = WorkspaceRecord(
        id=_deterministic_uuid("pg-trgm-workspace"),
        slug="pg-trgm-workspace",
        name="PG Trgm Workspace",
        description="Workspace for pg_trgm tests",
        created_at=_utc("2026-04-20T18:00:00Z"),
        updated_at=_utc("2026-04-20T18:00:00Z"),
    )
    session.add(workspace)
    session.flush()

    project = ProjectRecord(
        id=_deterministic_uuid("pg-trgm-project"),
        workspace_id=workspace.id,
        slug="pg-trgm-project",
        name="PG Trgm Project",
        description="Project for pg_trgm tests",
        created_at=_utc("2026-04-20T18:00:00Z"),
        updated_at=_utc("2026-04-20T18:00:00Z"),
    )
    session.add(project)
    session.flush()

    task = TaskRecord(
        id=_deterministic_uuid("pg-trgm-task"),
        project_id=project.id,
        task_type="coding-task",
        title="Seed retrieval search task",
        state="queued",
        description="Seed task for pg_trgm search coverage",
        contract={
            "surface_area": ["tests/memory", "retrieval/fts"],
            "spec": "Exercise FTS and trigram substrate.",
        },
        definition_of_done=["Search substrate covered."],
        created_at=_utc("2026-04-20T18:00:00Z"),
        updated_at=_utc("2026-04-20T18:00:00Z"),
    )
    session.add(task)
    session.flush()
    return task.id


def _seed_search_rows(session: Session) -> dict[str, uuid.UUID]:
    task_id = _seed_task(session)
    learning_id = _deterministic_uuid("pg-trgm-learning")
    artifact_id = _deterministic_uuid("pg-trgm-artifact")
    decision_id = _deterministic_uuid("pg-trgm-decision")

    session.add_all(
        [
            LearningRecord(
                id=learning_id,
                task_id=task_id,
                owner_actor_id=None,
                owner="agenticqueue-tests",
                title="Retry validator payload normalization",
                learning_type="pattern",
                what_happened="The validator rejected a retrieval payload during retry.",
                what_learned="Normalize the payload before re-running the validator.",
                action_rule="Normalize payload keys before retrying retrieval validation.",
                applies_when="Validator retries fail on payload shape.",
                does_not_apply_when="The contract schema changed upstream.",
                evidence=["artifact://validator-retry"],
                scope="project",
                promotion_eligible=False,
                confidence="confirmed",
                status="active",
                review_date=None,
                embedding=None,
                created_at=_utc("2026-04-20T18:01:00Z"),
                updated_at=_utc("2026-04-20T18:01:00Z"),
            ),
            ArtifactRecord(
                id=artifact_id,
                task_id=task_id,
                run_id=None,
                kind="patch",
                uri="file:///artifacts/retrieval-similarity.patch",
                details={
                    "summary": "FTS trigram retrieval patch",
                    "topic": "retrieval search patch",
                },
                embedding=None,
                created_at=_utc("2026-04-20T18:02:00Z"),
                updated_at=_utc("2026-04-20T18:02:00Z"),
            ),
            DecisionRecord(
                id=decision_id,
                task_id=task_id,
                run_id=None,
                actor_id=None,
                summary="Choose trigram fallback for retrieval search",
                rationale="FTS handles lexemes while trigram catches typos and near matches.",
                decided_at=_utc("2026-04-20T18:03:00Z"),
                embedding=None,
                created_at=_utc("2026-04-20T18:03:00Z"),
            ),
        ]
    )
    session.commit()
    return {
        "learning": learning_id,
        "artifact": artifact_id,
        "decision": decision_id,
    }


def _best_match_id(
    session: Session,
    *,
    table_name: str,
    query: str,
) -> uuid.UUID:
    statement = sa.text(
        f"""
        SELECT id
        FROM agenticqueue.{table_name}
        WHERE {search_trigram_column_name(table_name)} % :query
        ORDER BY similarity({search_trigram_column_name(table_name)}, :query) DESC, id
        LIMIT 1
        """
    )
    value = session.execute(statement, {"query": query}).scalar_one()
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _fts_match_id(
    session: Session,
    *,
    table_name: str,
    query: str,
) -> uuid.UUID:
    statement = sa.text(
        f"""
        SELECT id
        FROM agenticqueue.{table_name}
        WHERE {SEARCH_DOCUMENT_COLUMN_NAME} @@ to_tsquery('english', :query)
        ORDER BY id
        LIMIT 1
        """
    )
    value = session.execute(statement, {"query": query}).scalar_one()
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


@pytest.fixture(scope="session")
def engine() -> Engine:
    return sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    _truncate_tables(engine)
    connection = engine.connect()
    transaction = connection.begin()
    db_session = Session(bind=connection, expire_on_commit=False)
    try:
        yield db_session
    finally:
        db_session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


def test_search_indexes_exist_for_retrieval_entities() -> None:
    with psycopg.connect(get_sync_database_url()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT tablename, indexname, indexdef
                FROM pg_indexes
                WHERE schemaname = 'agenticqueue'
                  AND tablename IN ('artifact', 'decision', 'learning')
                """
            )
            indexes = {
                (row[0], row[1]): row[2]
                for row in cursor.fetchall()
            }

    for table_name in ("artifact", "decision", "learning"):
        document_index = indexes[(table_name, search_document_index_name(table_name))]
        trigram_index = indexes[(table_name, search_text_trgm_index_name(table_name))]

        assert "USING gin" in document_index
        assert "USING gin" in trigram_index
        assert "gin_trgm_ops" in trigram_index


def test_learning_search_supports_fts_and_trigram(session: Session) -> None:
    ids = _seed_search_rows(session)

    assert _fts_match_id(
        session,
        table_name="learning",
        query="validator & retry & payload",
    ) == ids["learning"]
    assert _best_match_id(
        session,
        table_name="learning",
        query="Retry validator payload normalization",
    ) == ids["learning"]


def test_artifact_search_supports_fts_and_trigram(session: Session) -> None:
    ids = _seed_search_rows(session)

    assert _fts_match_id(
        session,
        table_name="artifact",
        query="retrieval & patch",
    ) == ids["artifact"]
    assert _best_match_id(
        session,
        table_name="artifact",
        query="retrieval-similarity patch",
    ) == ids["artifact"]


def test_decision_search_supports_fts_and_trigram(session: Session) -> None:
    ids = _seed_search_rows(session)

    assert _fts_match_id(
        session,
        table_name="decision",
        query="trigram & fallback & retrieval",
    ) == ids["decision"]
    assert _best_match_id(
        session,
        table_name="decision",
        query="trigram fallbak retrieval search",
    ) == ids["decision"]
