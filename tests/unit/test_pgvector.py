from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.command import upgrade
from alembic.config import Config
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from agenticqueue_api.config import (
    get_embedding_dimension,
    get_sqlalchemy_sync_database_url,
)
from agenticqueue_api.models import (
    ActorModel,
    LearningModel,
    ProjectModel,
    RunModel,
    TaskModel,
    WorkspaceModel,
)
from agenticqueue_api.pgvector import (
    EMBEDDING_TABLES,
    embedding_index_name,
    normalize_embedding,
)
from agenticqueue_api.repo import (
    create_actor,
    create_learning,
    create_project,
    create_run,
    create_task,
    create_workspace,
    get_learning,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_CONFIG_PATH = REPO_ROOT / "apps" / "api" / "alembic.ini"


def alembic_config() -> Config:
    return Config(str(ALEMBIC_CONFIG_PATH))


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(f"{value:.6f}" for value in values) + "]"


def make_embedding(primary: float, secondary: float = 0.0) -> list[float]:
    vector = [0.0] * get_embedding_dimension()
    vector[0] = primary
    vector[1] = secondary
    return vector


def test_normalize_embedding_handles_tuple_and_non_numeric_lists() -> None:
    assert normalize_embedding((1, 2, 3)) == [1.0, 2.0, 3.0]
    assert normalize_embedding(["leave", "as-is"]) == ["leave", "as-is"]


@pytest.fixture(scope="session")
def engine() -> Engine:
    return sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)


@pytest.fixture(scope="session", autouse=True)
def ensure_schema_is_at_head() -> None:
    upgrade(alembic_config(), "head")


@pytest.fixture
def db_session(engine: Engine) -> Iterator[Session]:
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


@pytest.fixture
def seeded_graph(db_session: Session) -> dict[str, uuid.UUID]:
    now = dt.datetime.now(dt.UTC).replace(microsecond=0)
    workspace = create_workspace(
        db_session,
        WorkspaceModel(
            id=uuid.uuid4(),
            slug=f"workspace-{uuid.uuid4().hex[:8]}",
            name="AgenticQueue Test Workspace",
            description="Unit test workspace.",
            created_at=now,
            updated_at=now,
        ),
    )
    actor = create_actor(
        db_session,
        ActorModel(
            id=uuid.uuid4(),
            handle=f"codex-{uuid.uuid4().hex[:8]}",
            actor_type="agent",
            display_name="Codex",
            auth_subject=f"codex-{uuid.uuid4().hex[:8]}",
            is_active=True,
            created_at=now,
            updated_at=now,
        ),
    )
    project = create_project(
        db_session,
        ProjectModel(
            id=uuid.uuid4(),
            workspace_id=workspace.id,
            slug=f"project-{uuid.uuid4().hex[:8]}",
            name="AgenticQueue Test Project",
            description="Unit test project.",
            created_at=now,
            updated_at=now,
        ),
    )
    task = create_task(
        db_session,
        TaskModel(
            id=uuid.uuid4(),
            project_id=project.id,
            task_type="coding-task",
            title="Seed pgvector dependencies",
            state="queued",
            description="Test task for pgvector fixtures.",
            contract={"summary": "Seed pgvector dependencies"},
            definition_of_done=["pgvector tests pass"],
            created_at=now,
            updated_at=now,
        ),
    )
    run = create_run(
        db_session,
        RunModel(
            id=uuid.uuid4(),
            task_id=task.id,
            actor_id=actor.id,
            status="running",
            started_at=now,
            ended_at=None,
            summary="Seeding graph for pgvector tests.",
            details={"seed": True},
            created_at=now,
            updated_at=now,
        ),
    )

    return {
        "actor_id": actor.id,
        "run_id": run.id,
        "task_id": task.id,
    }


def test_pgvector_cosine_queries_return_expected_similarity_order(
    db_session: Session,
    seeded_graph: dict[str, uuid.UUID],
) -> None:
    similarity_query = sa.text("""
        SELECT title
        FROM agenticqueue.learning
        WHERE task_id = :task_id
          AND embedding IS NOT NULL
        ORDER BY embedding <=> CAST(:query_vector AS vector)
        """)
    base_payload = {
        "task_id": seeded_graph["task_id"],
        "owner_actor_id": seeded_graph["actor_id"],
        "learning_type": "pattern",
        "what_happened": "Embeddings were generated for retrieval.",
        "what_learned": "Cosine order should be stable for known vectors.",
        "action_rule": "Keep vectors normalized when possible.",
        "applies_when": "Ranking related learnings.",
        "does_not_apply_when": "There is no vector representation yet.",
        "evidence": ["AQ-42"],
        "scope": "project",
        "confidence": "confirmed",
        "status": "active",
        "review_date": dt.date(2026, 5, 1),
    }

    create_learning(
        db_session,
        LearningModel(
            id=uuid.uuid4(),
            title="nearest",
            embedding=make_embedding(1.0, 0.0),
            created_at=dt.datetime.now(dt.UTC),
            updated_at=dt.datetime.now(dt.UTC),
            **base_payload,
        ),
    )
    create_learning(
        db_session,
        LearningModel(
            id=uuid.uuid4(),
            title="middle",
            embedding=make_embedding(0.8, 0.2),
            created_at=dt.datetime.now(dt.UTC),
            updated_at=dt.datetime.now(dt.UTC),
            **base_payload,
        ),
    )
    create_learning(
        db_session,
        LearningModel(
            id=uuid.uuid4(),
            title="farthest",
            embedding=make_embedding(0.0, 1.0),
            created_at=dt.datetime.now(dt.UTC),
            updated_at=dt.datetime.now(dt.UTC),
            **base_payload,
        ),
    )

    ordered_titles = (
        db_session.execute(
            similarity_query,
            {
                "task_id": seeded_graph["task_id"],
                "query_vector": vector_literal(make_embedding(1.0, 0.0)),
            },
        )
        .scalars()
        .all()
    )

    assert ordered_titles == ["nearest", "middle", "farthest"]


def test_pgvector_null_embeddings_are_excluded_from_similarity_but_not_crud(
    db_session: Session,
    seeded_graph: dict[str, uuid.UUID],
) -> None:
    similarity_query = sa.text("""
        SELECT title
        FROM agenticqueue.learning
        WHERE task_id = :task_id
          AND embedding IS NOT NULL
        ORDER BY embedding <=> CAST(:query_vector AS vector)
        """)
    now = dt.datetime.now(dt.UTC)
    embedded_learning = create_learning(
        db_session,
        LearningModel(
            id=uuid.uuid4(),
            task_id=seeded_graph["task_id"],
            owner_actor_id=seeded_graph["actor_id"],
            title="embedded",
            learning_type="pattern",
            what_happened="A vectorized learning exists.",
            what_learned="Similarity search should find it.",
            action_rule="Use the vector index for retrieval.",
            applies_when="Embedding is present.",
            does_not_apply_when="Embedding is missing.",
            evidence=["AQ-42"],
            scope="project",
            confidence="confirmed",
            status="active",
            review_date=dt.date(2026, 5, 1),
            embedding=make_embedding(1.0, 0.0),
            created_at=now,
            updated_at=now,
        ),
    )
    null_learning = LearningModel(
        id=uuid.uuid4(),
        task_id=seeded_graph["task_id"],
        owner_actor_id=seeded_graph["actor_id"],
        title="not-embedded",
        learning_type="pattern",
        what_happened="The embedding job has not run yet.",
        what_learned="CRUD reads still need the row.",
        action_rule="Treat NULL embeddings as not-yet-indexed.",
        applies_when="Backfill is lazy.",
        does_not_apply_when="Embedding has already been stored.",
        evidence=["AQ-42"],
        scope="project",
        confidence="confirmed",
        status="active",
        review_date=dt.date(2026, 5, 1),
        embedding=None,
        created_at=now,
        updated_at=now,
    )
    create_learning(db_session, null_learning)

    ordered_titles = (
        db_session.execute(
            similarity_query,
            {
                "task_id": seeded_graph["task_id"],
                "query_vector": vector_literal(make_embedding(1.0, 0.0)),
            },
        )
        .scalars()
        .all()
    )

    loaded_null_learning = get_learning(db_session, null_learning.id)

    assert ordered_titles == [embedded_learning.title]
    assert loaded_null_learning == null_learning


def test_pgvector_wrong_dimension_raises_db_error(
    db_session: Session,
    seeded_graph: dict[str, uuid.UUID],
) -> None:
    bad_vector = vector_literal([1.0, 0.0])
    bad_insert = sa.text(f"""
        INSERT INTO agenticqueue.artifact (
            id,
            task_id,
            run_id,
            kind,
            uri,
            details,
            embedding
        )
        VALUES (
            :artifact_id,
            :task_id,
            :run_id,
            'diff',
            'file://artifacts/pgvector.diff',
            '{{}}'::jsonb,
            '{bad_vector}'::vector
        )
        """)

    with pytest.raises(sa.exc.DBAPIError) as exc_info:
        db_session.execute(
            bad_insert,
            {
                "artifact_id": uuid.uuid4(),
                "task_id": seeded_graph["task_id"],
                "run_id": seeded_graph["run_id"],
            },
        )

    assert f"expected {get_embedding_dimension()} dimensions" in str(
        exc_info.value.orig
    )


def test_pgvector_migration_exposes_columns_and_indexes() -> None:
    upgrade(alembic_config(), "head")
    table_query = sa.text("""
        SELECT table_name
        FROM information_schema.columns
        WHERE table_schema = 'agenticqueue'
          AND column_name = 'embedding'
        ORDER BY table_name
        """)
    index_query = sa.text("""
        SELECT indexname
        FROM pg_indexes
        WHERE schemaname = 'agenticqueue'
        ORDER BY indexname
        """)

    engine = sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)
    with engine.connect() as connection:
        tables = connection.execute(table_query).scalars().all()
        indexes = connection.execute(index_query).scalars().all()

    assert tables == sorted(EMBEDDING_TABLES)
    assert set(
        embedding_index_name(table_name) for table_name in EMBEDDING_TABLES
    ).issubset(set(indexes))
