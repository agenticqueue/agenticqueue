from __future__ import annotations

import json
import uuid
from typing import Iterator

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.models import EdgeModel, EdgeRelation, LearningModel
from agenticqueue_api.repo import create_edge, create_learning, learnings_for

TASK_ID = uuid.UUID("00000000-0000-0000-0000-000000000501")
DECISION_ID = uuid.UUID("00000000-0000-0000-0000-000000000502")
ARTIFACT_ID = uuid.UUID("00000000-0000-0000-0000-000000000503")
RUN_ID = uuid.UUID("00000000-0000-0000-0000-000000000504")


@pytest.fixture(scope="session")
def engine() -> Engine:
    return sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)


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


def make_learning_payload(
    suffix: int,
    *,
    scope: str,
) -> LearningModel:
    payload = {
        "id": f"00000000-0000-0000-0000-{suffix:012d}",
        "task_id": None,
        "owner_actor_id": None,
        "title": f"Learning {suffix}",
        "learning_type": "pattern",
        "what_happened": "The task produced a reusable result.",
        "what_learned": "Capture the pattern for later runs.",
        "action_rule": "Reuse this pattern when the same shape appears again.",
        "applies_when": "A task repeats the same integration path.",
        "does_not_apply_when": "The dependency graph materially changes.",
        "evidence": [f"artifact://{suffix}"],
        "scope": scope,
        "confidence": "confirmed",
        "status": "active",
        "review_date": None,
        "embedding": None,
        "created_at": "2026-04-20T00:00:00+00:00",
        "updated_at": "2026-04-20T00:00:00+00:00",
    }
    return LearningModel.model_validate_json(json.dumps(payload))


def make_edge_payload(
    suffix: int,
    *,
    src_entity_type: str,
    src_id: uuid.UUID,
    dst_entity_type: str,
    dst_id: uuid.UUID,
) -> EdgeModel:
    payload = {
        "id": f"00000000-0000-0000-0000-{suffix:012d}",
        "src_entity_type": src_entity_type,
        "src_id": str(src_id),
        "dst_entity_type": dst_entity_type,
        "dst_id": str(dst_id),
        "relation": EdgeRelation.LEARNED_FROM.value,
        "metadata": {},
        "created_by": None,
        "created_at": "2026-04-20T00:00:00+00:00",
    }
    return EdgeModel.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize(
    ("src_entity_type", "dst_entity_type"),
    [
        ("learning", "task"),
        ("run", "learning"),
        ("learning", "artifact"),
        ("decision", "learning"),
        ("learning", "incident"),
        ("tool", "learning"),
        ("learning", "actor"),
    ],
)
def test_learned_from_edges_accept_supported_entity_pairs(
    src_entity_type: str,
    dst_entity_type: str,
) -> None:
    payload = make_edge_payload(
        601,
        src_entity_type=src_entity_type,
        src_id=RUN_ID if src_entity_type != "learning" else uuid.uuid4(),
        dst_entity_type=dst_entity_type,
        dst_id=RUN_ID if dst_entity_type != "learning" else uuid.uuid4(),
    )

    assert payload.relation is EdgeRelation.LEARNED_FROM


@pytest.mark.parametrize(
    ("src_entity_type", "dst_entity_type"),
    [
        ("task", "artifact"),
        ("learning", "learning"),
        ("learning", "project"),
        ("actor", "task"),
    ],
)
def test_learned_from_edges_reject_invalid_entity_pairs(
    src_entity_type: str,
    dst_entity_type: str,
) -> None:
    with pytest.raises(ValidationError, match="learned_from"):
        make_edge_payload(
            602,
            src_entity_type=src_entity_type,
            src_id=TASK_ID,
            dst_entity_type=dst_entity_type,
            dst_id=ARTIFACT_ID,
        )


def test_learnings_for_returns_linked_learnings_for_each_entity(
    db_session: Session,
) -> None:
    task_learning = create_learning(
        db_session, make_learning_payload(611, scope="task")
    )
    decision_learning = create_learning(
        db_session,
        make_learning_payload(612, scope="task"),
    )
    artifact_learning = create_learning(
        db_session,
        make_learning_payload(613, scope="task"),
    )

    create_edge(
        db_session,
        make_edge_payload(
            611,
            src_entity_type="learning",
            src_id=task_learning.id,
            dst_entity_type="task",
            dst_id=TASK_ID,
        ),
    )
    create_edge(
        db_session,
        make_edge_payload(
            612,
            src_entity_type="decision",
            src_id=DECISION_ID,
            dst_entity_type="learning",
            dst_id=decision_learning.id,
        ),
    )
    create_edge(
        db_session,
        make_edge_payload(
            613,
            src_entity_type="learning",
            src_id=artifact_learning.id,
            dst_entity_type="artifact",
            dst_id=ARTIFACT_ID,
        ),
    )

    assert [learning.id for learning in learnings_for(db_session, TASK_ID)] == [
        task_learning.id
    ]
    assert [learning.id for learning in learnings_for(db_session, DECISION_ID)] == [
        decision_learning.id
    ]
    assert [learning.id for learning in learnings_for(db_session, ARTIFACT_ID)] == [
        artifact_learning.id
    ]


def test_learnings_for_widens_scope_filters(db_session: Session) -> None:
    task_learning = create_learning(
        db_session, make_learning_payload(621, scope="task")
    )
    project_learning = create_learning(
        db_session,
        make_learning_payload(622, scope="project"),
    )
    global_learning = create_learning(
        db_session,
        make_learning_payload(623, scope="global"),
    )

    for edge_suffix, learning in enumerate(
        [task_learning, project_learning, global_learning],
        start=621,
    ):
        create_edge(
            db_session,
            make_edge_payload(
                edge_suffix,
                src_entity_type="learning",
                src_id=learning.id,
                dst_entity_type="task",
                dst_id=TASK_ID,
            ),
        )

    assert [
        learning.id for learning in learnings_for(db_session, TASK_ID, scope="task")
    ] == [
        task_learning.id,
    ]
    assert [
        learning.id for learning in learnings_for(db_session, TASK_ID, scope="project")
    ] == [task_learning.id, project_learning.id]
    assert [
        learning.id for learning in learnings_for(db_session, TASK_ID, scope="global")
    ] == [task_learning.id, project_learning.id, global_learning.id]
    assert [
        learning.id for learning in learnings_for(db_session, TASK_ID, scope="all")
    ] == [
        task_learning.id,
        project_learning.id,
        global_learning.id,
    ]


def test_learnings_for_rejects_unknown_scope(db_session: Session) -> None:
    with pytest.raises(ValueError, match="scope"):
        learnings_for(db_session, TASK_ID, scope="workspace")
