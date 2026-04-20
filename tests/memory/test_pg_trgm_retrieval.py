from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.models.learning import LearningRecord
from agenticqueue_api.models.project import ProjectRecord
from agenticqueue_api.models.task import TaskRecord
from agenticqueue_api.models.workspace import WorkspaceRecord
from agenticqueue_api.retrieval import RetrievalQuery, RetrievalService
from agenticqueue_api.schemas.learning import LearningStatus

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


def _uuid(label: str) -> uuid.UUID:
    return uuid.uuid5(uuid.NAMESPACE_URL, f"https://agenticqueue.ai/tests/{label}")


def _utc(value: str) -> dt.datetime:
    return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))


def _truncate_tables(engine: Engine) -> None:
    with engine.begin() as connection:
        qualified_tables = ", ".join(
            f"agenticqueue.{table_name}" for table_name in TRUNCATE_TABLES
        )
        connection.execute(
            sa.text(f"TRUNCATE TABLE {qualified_tables} RESTART IDENTITY CASCADE")
        )


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


def _seed_project(session: Session) -> uuid.UUID:
    workspace = WorkspaceRecord(
        id=_uuid("retrieval-workspace"),
        slug="retrieval-workspace",
        name="Retrieval Workspace",
        description="Workspace for retrieval tier tests",
        created_at=_utc("2026-04-20T18:00:00Z"),
        updated_at=_utc("2026-04-20T18:00:00Z"),
    )
    session.add(workspace)
    session.flush()

    project = ProjectRecord(
        id=_uuid("retrieval-project"),
        workspace_id=workspace.id,
        slug="retrieval-project",
        name="Retrieval Project",
        description="Project for retrieval tier tests",
        created_at=_utc("2026-04-20T18:00:00Z"),
        updated_at=_utc("2026-04-20T18:00:00Z"),
    )
    session.add(project)
    session.flush()
    return project.id


def _seed_task(
    session: Session,
    *,
    label: str,
    project_id: uuid.UUID,
    title: str,
    description: str,
    spec: str,
    surface_area: list[str],
    file_scope: list[str] | None = None,
    created_at: str,
) -> uuid.UUID:
    task = TaskRecord(
        id=_uuid(label),
        project_id=project_id,
        task_type="coding-task",
        title=title,
        state="queued",
        description=description,
        contract={
            "surface_area": surface_area,
            "file_scope": file_scope or [],
            "spec": spec,
        },
        definition_of_done=["retrieval tested"],
        created_at=_utc(created_at),
        updated_at=_utc(created_at),
    )
    session.add(task)
    session.flush()
    return task.id


def _seed_learning(
    session: Session,
    *,
    label: str,
    task_id: uuid.UUID,
    title: str,
    what_happened: str,
    what_learned: str,
    action_rule: str,
    evidence: list[str],
    created_at: str,
) -> uuid.UUID:
    learning = LearningRecord(
        id=_uuid(label),
        task_id=task_id,
        owner_actor_id=None,
        owner="retrieval-tests",
        title=title,
        learning_type="pattern",
        what_happened=what_happened,
        what_learned=what_learned,
        action_rule=action_rule,
        applies_when="retrieval tier coverage is under test",
        does_not_apply_when="the test targets an unrelated subsystem",
        evidence=evidence,
        scope="project",
        promotion_eligible=False,
        confidence="confirmed",
        status=LearningStatus.ACTIVE.value,
        review_date=None,
        embedding=None,
        created_at=_utc(created_at),
        updated_at=_utc(created_at),
    )
    session.add(learning)
    session.flush()
    return learning.id


def test_retrieval_service_uses_fts_tier_for_lexical_matches(session: Session) -> None:
    project_id = _seed_project(session)
    source_task_id = _seed_task(
        session,
        label="fts-source-task",
        project_id=project_id,
        title="Source task for validator retries",
        description="Original validator retry source.",
        spec="Normalize payload before retrying the validator.",
        surface_area=["source/validator"],
        created_at="2026-04-20T18:01:00Z",
    )
    _seed_learning(
        session,
        label="fts-learning",
        task_id=source_task_id,
        title="Normalize validator payload before retry",
        what_happened="The validator rejected a malformed payload during retry.",
        what_learned="Normalize the payload fields before retrying validation.",
        action_rule="Normalize payload keys before validator retry.",
        evidence=["artifact://validator-retry"],
        created_at="2026-04-20T18:02:00Z",
    )
    query_task_id = _seed_task(
        session,
        label="fts-query-task",
        project_id=project_id,
        title="Validator payload retry normalization",
        description="Need fuzzy retrieval after the surface path misses.",
        spec="Normalize payload before retrying the validator.",
        surface_area=["query/no-surface-hit"],
        created_at="2026-04-20T18:10:00Z",
    )
    session.commit()

    result = RetrievalService(session).retrieve(
        RetrievalQuery(task_id=query_task_id, k=1, fuzzy_global_search=True)
    )

    assert [learning.title for learning in result.items] == [
        "Normalize validator payload before retry"
    ]
    assert result.tiers_fired[:3] == ["surface_area", "graph", "metadata"]
    assert "fts" in result.tiers_fired
    assert "rerank" in result.tiers_fired


def test_retrieval_service_uses_trgm_tier_for_near_matches(session: Session) -> None:
    project_id = _seed_project(session)
    source_task_id = _seed_task(
        session,
        label="trgm-source-task",
        project_id=project_id,
        title="Source task for release hardening",
        description="Original hardening source.",
        spec="Hardening release readiness.",
        surface_area=["source/release"],
        created_at="2026-04-20T18:01:00Z",
    )
    _seed_learning(
        session,
        label="trgm-learning",
        task_id=source_task_id,
        title="Retrieval hardening checklist",
        what_happened="A release was blocked on a missing hardening checklist.",
        what_learned="Keep the hardening checklist close to the retrieval changes.",
        action_rule="Write the hardening checklist before release review.",
        evidence=["artifact://hardening-checklist"],
        created_at="2026-04-20T18:02:00Z",
    )
    query_task_id = _seed_task(
        session,
        label="trgm-query-task",
        project_id=project_id,
        title="Retrievel hardenng cheklist",
        description="Need fzzy retrievel after a tpyo-hevy quary.",
        spec="Use trigrm fallback for retrievel hardenng cheklist.",
        surface_area=["query/no-surface-hit"],
        created_at="2026-04-20T18:10:00Z",
    )
    session.commit()

    result = RetrievalService(session).retrieve(
        RetrievalQuery(task_id=query_task_id, k=1, fuzzy_global_search=True)
    )

    assert [learning.title for learning in result.items] == [
        "Retrieval hardening checklist"
    ]
    assert result.tiers_fired[:3] == ["surface_area", "graph", "metadata"]
    assert "trgm" in result.tiers_fired
    assert "rerank" in result.tiers_fired


def test_retrieval_service_keeps_vector_fallback_when_lexical_tiers_miss(
    session: Session,
) -> None:
    project_id = _seed_project(session)
    source_task_id = _seed_task(
        session,
        label="vector-source-task",
        project_id=project_id,
        title="Legacy source task",
        description="Legacy source task for vector fallback.",
        spec="hash similarity branch",
        surface_area=["source/no-surface-hit"],
        file_scope=["apps/api/vector_fallback.py"],
        created_at="2026-04-20T18:01:00Z",
    )
    _seed_learning(
        session,
        label="vector-learning",
        task_id=source_task_id,
        title="Archived note",
        what_happened="A migration note was filed.",
        what_learned="Keep a regression around the recovery path.",
        action_rule="Preserve the recovery-path regression fixture.",
        evidence=["artifact://apps/api/vector_fallback.py"],
        created_at="2026-04-20T18:02:00Z",
    )
    query_task_id = _seed_task(
        session,
        label="vector-query-task",
        project_id=project_id,
        title="Cold fallback",
        description="Need the legacy fallback without a lexical hit.",
        spec="hash similarity branch",
        surface_area=["query/no-surface-hit"],
        file_scope=["apps/api/vector_fallback.py"],
        created_at="2026-04-20T18:10:00Z",
    )
    session.commit()

    result = RetrievalService(session).retrieve(
        RetrievalQuery(task_id=query_task_id, k=1, fuzzy_global_search=True)
    )

    assert [learning.title for learning in result.items] == ["Archived note"]
    assert result.tiers_fired == [
        "surface_area",
        "graph",
        "metadata",
        "vector",
        "rerank",
    ]
