"""Learning lifecycle helpers for supersede + expire flows."""

from __future__ import annotations

import datetime as dt
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from agenticqueue_api.audit import (
    AUDIT_ACTOR_ID_KEY,
    AUDIT_REDACTION_KEY,
    AUDIT_TRACE_ID_KEY,
)
from agenticqueue_api.models import EdgeRelation, LearningModel, LearningRecord
from agenticqueue_api.models.audit_log import AuditLogRecord
from agenticqueue_api.models.edge import EdgeRecord
from agenticqueue_api.schemas.learning import LearningStatus

EXPIRATION_REVIEW_WINDOW_DAYS = 90


def _normalize_reason(reason: str) -> str:
    normalized = reason.strip()
    if not normalized:
        raise ValueError("reason must not be empty")
    return normalized


class LearningLifecycleService:
    """Apply lifecycle transitions to persisted learnings."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def supersede(
        self,
        *,
        old_learning_id: uuid.UUID,
        new_learning_id: uuid.UUID,
        reason: str,
        created_by: uuid.UUID | None = None,
    ) -> LearningModel:
        normalized_reason = _normalize_reason(reason)
        if old_learning_id == new_learning_id:
            raise ValueError("old_learning_id and new_learning_id must differ")

        old_learning = self._require_learning(old_learning_id)
        new_learning = self._require_learning(new_learning_id)
        if new_learning.status != LearningStatus.ACTIVE.value:
            raise ValueError("replacement learning must be active")
        if old_learning.status == LearningStatus.EXPIRED.value:
            raise ValueError("expired learnings cannot be superseded")

        self._ensure_unique_replacement(
            old_learning_id=old_learning_id,
            new_learning_id=new_learning_id,
        )
        old_learning.status = LearningStatus.SUPERSEDED.value
        self._ensure_supersedes_edge(
            old_learning_id=old_learning_id,
            new_learning_id=new_learning_id,
            reason=normalized_reason,
            created_by=created_by,
        )
        self._session.flush()
        self._session.refresh(old_learning)
        return LearningModel.model_validate(old_learning)

    def expire(
        self,
        learning_id: uuid.UUID,
        *,
        reason: str,
    ) -> LearningModel:
        _normalize_reason(reason)
        learning = self._require_learning(learning_id)
        learning.status = LearningStatus.EXPIRED.value
        self._session.flush()
        self._session.refresh(learning)
        return LearningModel.model_validate(learning)

    def flag_expired_candidates(
        self,
        *,
        as_of: dt.date | None = None,
    ) -> list[LearningModel]:
        reference_day = dt.date.today() if as_of is None else as_of
        cutoff = reference_day - dt.timedelta(days=EXPIRATION_REVIEW_WINDOW_DAYS)
        statement = (
            sa.select(LearningRecord)
            .where(LearningRecord.status == LearningStatus.ACTIVE.value)
            .where(LearningRecord.review_date.is_not(None))
            .where(LearningRecord.review_date < cutoff)
            .order_by(
                LearningRecord.review_date.asc(),
                LearningRecord.created_at.asc(),
                LearningRecord.id.asc(),
            )
        )
        candidates = list(self._session.scalars(statement))
        for learning in candidates:
            self._write_flag_audit_row(learning, cutoff=cutoff)
        if candidates:
            self._session.flush()
        return [LearningModel.model_validate(learning) for learning in candidates]

    def _require_learning(self, learning_id: uuid.UUID) -> LearningRecord:
        learning = self._session.get(LearningRecord, learning_id)
        if learning is None:
            raise KeyError(str(learning_id))
        return learning

    def _ensure_unique_replacement(
        self,
        *,
        old_learning_id: uuid.UUID,
        new_learning_id: uuid.UUID,
    ) -> None:
        existing_replacement = self._session.scalar(
            sa.select(EdgeRecord.src_id).where(
                EdgeRecord.src_entity_type == "learning",
                EdgeRecord.dst_entity_type == "learning",
                EdgeRecord.dst_id == old_learning_id,
                EdgeRecord.relation == EdgeRelation.SUPERSEDES,
            )
        )
        if existing_replacement is not None and existing_replacement != new_learning_id:
            raise ValueError(
                "learning already has a replacement; supersede chains must stay linear"
            )

    def _ensure_supersedes_edge(
        self,
        *,
        old_learning_id: uuid.UUID,
        new_learning_id: uuid.UUID,
        reason: str,
        created_by: uuid.UUID | None,
    ) -> None:
        edge = self._session.scalar(
            sa.select(EdgeRecord).where(
                EdgeRecord.src_entity_type == "learning",
                EdgeRecord.src_id == new_learning_id,
                EdgeRecord.dst_entity_type == "learning",
                EdgeRecord.dst_id == old_learning_id,
                EdgeRecord.relation == EdgeRelation.SUPERSEDES,
            )
        )
        if edge is not None:
            if edge.edge_metadata.get("reason") != reason:
                edge.edge_metadata = {**edge.edge_metadata, "reason": reason}
            return

        self._session.add(
            EdgeRecord(
                src_entity_type="learning",
                src_id=new_learning_id,
                dst_entity_type="learning",
                dst_id=old_learning_id,
                relation=EdgeRelation.SUPERSEDES,
                edge_metadata={"reason": reason},
                created_by=created_by,
            )
        )

    def _write_flag_audit_row(
        self,
        learning: LearningRecord,
        *,
        cutoff: dt.date,
    ) -> None:
        snapshot = LearningModel.model_validate(learning).model_dump(mode="json")
        self._session.execute(
            sa.insert(AuditLogRecord).values(
                actor_id=self._session.info.get(AUDIT_ACTOR_ID_KEY),
                entity_type="learning",
                entity_id=learning.id,
                action="FLAG_EXPIRED_CANDIDATE",
                before=None,
                after={
                    "candidate": snapshot,
                    "cutoff_date": cutoff.isoformat(),
                },
                trace_id=self._session.info.get(AUDIT_TRACE_ID_KEY),
                redaction=self._session.info.get(AUDIT_REDACTION_KEY),
            )
        )


__all__ = [
    "EXPIRATION_REVIEW_WINDOW_DAYS",
    "LearningLifecycleService",
]
