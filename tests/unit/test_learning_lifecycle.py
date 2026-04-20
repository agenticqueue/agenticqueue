from __future__ import annotations

import datetime as dt
import json
import uuid
from collections.abc import Iterator

import pytest
import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session

from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.learnings import (
    EXPIRATION_REVIEW_WINDOW_DAYS,
    LearningLifecycleService,
)
from agenticqueue_api.models import (
    AuditLogRecord,
    EdgeModel,
    EdgeRelation,
    LearningModel,
)
from agenticqueue_api.models.edge import EdgeRecord
from agenticqueue_api.repo import create_edge, create_learning, learnings_for
from agenticqueue_api.schemas.learning import LearningStatus

TASK_ID = uuid.UUID("00000000-0000-0000-0000-000000000801")


def make_learning_payload(
    suffix: int,
    *,
    review_date: str | None,
    title: str | None = None,
    status: str = LearningStatus.ACTIVE.value,
) -> LearningModel:
    payload = {
        "id": f"00000000-0000-0000-0000-{suffix:012d}",
        "task_id": None,
        "owner_actor_id": None,
        "owner": "agenticqueue-auto-draft",
        "title": title or f"Learning {suffix}",
        "learning_type": "pattern",
        "what_happened": "The task produced a reusable result.",
        "what_learned": "Capture the pattern for later runs.",
        "action_rule": "Reuse this pattern when the same shape appears again.",
        "applies_when": "A task repeats the same integration path.",
        "does_not_apply_when": "The dependency graph materially changes.",
        "evidence": [f"artifact://{suffix}"],
        "scope": "task",
        "confidence": "confirmed",
        "status": status,
        "review_date": review_date,
        "embedding": None,
        "created_at": "2026-04-20T00:00:00+00:00",
        "updated_at": "2026-04-20T00:00:00+00:00",
    }
    return LearningModel.model_validate_json(json.dumps(payload))


def make_learned_from_edge(
    suffix: int,
    *,
    learning_id: uuid.UUID,
) -> EdgeModel:
    payload: dict[str, object] = {
        "id": f"00000000-0000-0000-0000-{suffix:012d}",
        "src_entity_type": "learning",
        "src_id": str(learning_id),
        "dst_entity_type": "task",
        "dst_id": str(TASK_ID),
        "relation": EdgeRelation.LEARNED_FROM.value,
        "metadata": {},
        "created_by": None,
        "created_at": "2026-04-20T00:00:00+00:00",
    }
    return EdgeModel.model_validate_json(json.dumps(payload))


def latest_audit_row(
    session: Session,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    action: str,
) -> AuditLogRecord:
    statement = (
        sa.select(AuditLogRecord)
        .where(
            AuditLogRecord.entity_type == entity_type,
            AuditLogRecord.entity_id == entity_id,
            AuditLogRecord.action == action,
        )
        .order_by(AuditLogRecord.created_at.desc(), AuditLogRecord.id.desc())
    )
    row = session.scalars(statement).first()
    assert row is not None
    return row


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


def test_supersede_marks_old_learning_and_records_edge(db_session: Session) -> None:
    old_learning = create_learning(
        db_session,
        make_learning_payload(
            811,
            title="Legacy validator retry learning",
            review_date="2026-04-01",
        ),
    )
    new_learning = create_learning(
        db_session,
        make_learning_payload(
            812,
            title="Canonical validator retry learning",
            review_date="2026-04-15",
        ),
    )
    create_edge(
        db_session,
        make_learned_from_edge(811, learning_id=old_learning.id),
    )
    create_edge(
        db_session,
        make_learned_from_edge(812, learning_id=new_learning.id),
    )

    service = LearningLifecycleService(db_session)
    superseded = service.supersede(
        old_learning_id=old_learning.id,
        new_learning_id=new_learning.id,
        reason="Promoted into the canonical learning ledger entry.",
    )

    assert superseded.status == LearningStatus.SUPERSEDED.value
    assert [learning.id for learning in learnings_for(db_session, TASK_ID)] == [
        new_learning.id
    ]
    assert [
        learning.id
        for learning in learnings_for(db_session, TASK_ID, include_inactive=True)
    ] == [old_learning.id, new_learning.id]

    edge = db_session.scalar(
        sa.select(EdgeRecord).where(
            EdgeRecord.src_entity_type == "learning",
            EdgeRecord.src_id == new_learning.id,
            EdgeRecord.dst_entity_type == "learning",
            EdgeRecord.dst_id == old_learning.id,
            EdgeRecord.relation == EdgeRelation.SUPERSEDES,
        )
    )
    assert edge is not None
    assert edge.edge_metadata["reason"] == (
        "Promoted into the canonical learning ledger entry."
    )

    learning_audit = latest_audit_row(
        db_session,
        entity_type="learning",
        entity_id=old_learning.id,
        action="UPDATE",
    )
    assert learning_audit.after is not None
    assert learning_audit.after["status"] == LearningStatus.SUPERSEDED.value

    edge_audit = latest_audit_row(
        db_session,
        entity_type="edge",
        entity_id=edge.id,
        action="CREATE",
    )
    assert edge_audit.after is not None
    assert edge_audit.after["relation"] == EdgeRelation.SUPERSEDES.value


def test_expire_excludes_learning_from_default_retrieval(db_session: Session) -> None:
    learning = create_learning(
        db_session,
        make_learning_payload(
            821,
            title="Outdated queue retry heuristic",
            review_date="2026-01-10",
        ),
    )
    create_edge(
        db_session,
        make_learned_from_edge(821, learning_id=learning.id),
    )

    service = LearningLifecycleService(db_session)
    expired = service.expire(
        learning.id,
        reason="Superseded by the newer queue retry path.",
    )

    assert expired.status == LearningStatus.EXPIRED.value
    assert learnings_for(db_session, TASK_ID) == []
    assert [
        item.id for item in learnings_for(db_session, TASK_ID, include_inactive=True)
    ] == [learning.id]

    learning_audit = latest_audit_row(
        db_session,
        entity_type="learning",
        entity_id=learning.id,
        action="UPDATE",
    )
    assert learning_audit.after is not None
    assert learning_audit.after["status"] == LearningStatus.EXPIRED.value


def test_flag_expired_candidates_returns_only_stale_active_rows(
    db_session: Session,
) -> None:
    stale_active = create_learning(
        db_session,
        make_learning_payload(
            831,
            title="Old but still active learning",
            review_date="2026-01-01",
        ),
    )
    create_learning(
        db_session,
        make_learning_payload(
            832,
            title="Fresh active learning",
            review_date="2026-03-01",
        ),
    )
    create_learning(
        db_session,
        make_learning_payload(
            833,
            title="Already superseded learning",
            status=LearningStatus.SUPERSEDED.value,
            review_date="2026-01-01",
        ),
    )

    service = LearningLifecycleService(db_session)
    flagged = service.flag_expired_candidates(as_of=dt.date(2026, 4, 20))

    assert EXPIRATION_REVIEW_WINDOW_DAYS == 90
    assert [learning.id for learning in flagged] == [stale_active.id]

    audit_row = latest_audit_row(
        db_session,
        entity_type="learning",
        entity_id=stale_active.id,
        action="FLAG_EXPIRED_CANDIDATE",
    )
    assert audit_row.after is not None
    assert audit_row.after["cutoff_date"] == "2026-01-20"
    assert audit_row.after["candidate"]["status"] == LearningStatus.ACTIVE.value
