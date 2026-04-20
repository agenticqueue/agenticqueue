"""Learning dedupe helpers for draft confirmation."""

from __future__ import annotations

import datetime as dt
import hashlib
import math
import uuid
from collections.abc import Callable
from enum import StrEnum

import sqlalchemy as sa
from pydantic import Field, model_validator
from sqlalchemy.orm import Session

from agenticqueue_api.config import get_embedding_dimension
from agenticqueue_api.models import EdgeRelation, LearningModel, LearningRecord
from agenticqueue_api.models.edge import EdgeRecord
from agenticqueue_api.models.shared import SchemaModel
from agenticqueue_api.pgvector import normalize_embedding
from agenticqueue_api.schemas.learning import (
    LearningConfidence,
    LearningSchemaModel,
    LearningStatus,
)

DEDUPLICATE_THRESHOLD = 0.92


class MergeDecision(StrEnum):
    """Human decision for a dedupe suggestion."""

    ACCEPT = "accept"
    REJECT = "reject"


class ConfirmLearningDraftRequest(SchemaModel):
    """Optional merge decision supplied when a draft hits a duplicate."""

    merge_decision: MergeDecision | None = None
    matched_learning_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def validate_merge_pair(self) -> ConfirmLearningDraftRequest:
        has_decision = self.merge_decision is not None
        has_match = self.matched_learning_id is not None
        if has_decision != has_match:
            raise ValueError(
                "merge_decision and matched_learning_id must be provided together",
            )
        return self


class DedupeSuggestion(SchemaModel):
    """Suggested duplicate surfaced before mutating the learning ledger."""

    draft_id: uuid.UUID
    draft_learning: LearningSchemaModel
    matched_learning: LearningModel
    similarity: float = Field(ge=0.0, le=1.0)
    threshold: float = Field(default=DEDUPLICATE_THRESHOLD, ge=0.0, le=1.0)


def build_dedupe_text(title: str, action_rule: str) -> str:
    """Return the canonical text used for draft dedupe embeddings."""

    return f"{title.strip()}\n{action_rule.strip()}"


def _hash_embed_text(text: str) -> list[float]:
    """Deterministic fallback embedder for draft dedupe."""

    dimension = get_embedding_dimension()
    vector = [0.0] * dimension
    for token in text.lower().split():
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
        index = int.from_bytes(digest[:8], "big") % dimension
        sign = 1.0 if digest[8] % 2 == 0 else -1.0
        vector[index] += sign

    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0:
        return vector
    return [value / norm for value in vector]


def _cosine_similarity(lhs: list[float], rhs: list[float]) -> float:
    if not lhs or not rhs or len(lhs) != len(rhs):
        return 0.0

    lhs_norm = math.sqrt(sum(value * value for value in lhs))
    rhs_norm = math.sqrt(sum(value * value for value in rhs))
    if lhs_norm == 0 or rhs_norm == 0:
        return 0.0

    dot = sum(left * right for left, right in zip(lhs, rhs, strict=True))
    similarity = dot / (lhs_norm * rhs_norm)
    return max(0.0, min(similarity, 1.0))


def _promote_confidence(
    current: str,
    incoming: LearningConfidence,
) -> str:
    tiers = [
        LearningConfidence.TENTATIVE.value,
        LearningConfidence.CONFIRMED.value,
        LearningConfidence.VALIDATED.value,
    ]
    current_index = tiers.index(current) if current in tiers else 0
    incoming_index = tiers.index(incoming.value)
    promoted_index = min(max(current_index, incoming_index) + 1, len(tiers) - 1)
    return tiers[promoted_index]


def _merge_evidence(existing: list[str], incoming: list[str]) -> list[str]:
    merged: list[str] = []
    seen: set[str] = set()
    for item in [*existing, *incoming]:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        merged.append(normalized)
    return merged


def _max_review_date(current: dt.date | None, incoming: str) -> dt.date:
    incoming_date = dt.date.fromisoformat(incoming)
    if current is None or incoming_date > current:
        return incoming_date
    return current


class LearningDedupeService:
    """Deduplicate learning drafts against persisted active learnings."""

    def __init__(
        self,
        session: Session,
        *,
        embed_text: Callable[[str], list[float]] | None = None,
        threshold: float = DEDUPLICATE_THRESHOLD,
    ) -> None:
        self._session = session
        self._embed_text = embed_text or _hash_embed_text
        self._threshold = threshold

    @property
    def threshold(self) -> float:
        return self._threshold

    def embed_learning_text(self, title: str, action_rule: str) -> list[float]:
        return self._embedding_for_text(build_dedupe_text(title, action_rule))

    def suggest(
        self,
        *,
        draft_id: uuid.UUID,
        draft_learning: LearningSchemaModel,
    ) -> DedupeSuggestion | None:
        draft_embedding = self.embed_learning_text(
            draft_learning.title,
            draft_learning.action_rule,
        )

        statement = (
            sa.select(LearningRecord)
            .where(LearningRecord.scope == draft_learning.scope.value)
            .where(LearningRecord.status == LearningStatus.ACTIVE.value)
            .order_by(LearningRecord.created_at.asc(), LearningRecord.id.asc())
        )

        best_match: LearningRecord | None = None
        best_similarity = 0.0
        for record in self._session.scalars(statement):
            record_embedding = self._embedding_for_record(record)
            similarity = _cosine_similarity(draft_embedding, record_embedding)
            if similarity < self._threshold:
                continue
            if best_match is None or similarity > best_similarity:
                best_match = record
                best_similarity = similarity

        if best_match is None:
            return None

        return DedupeSuggestion(
            draft_id=draft_id,
            draft_learning=draft_learning,
            matched_learning=LearningModel.model_validate(best_match),
            similarity=best_similarity,
            threshold=self._threshold,
        )

    def merge_into_existing(
        self,
        *,
        existing_learning_id: uuid.UUID,
        draft_learning: LearningSchemaModel,
    ) -> LearningModel:
        existing = self._session.get(LearningRecord, existing_learning_id)
        if existing is None:
            raise KeyError(str(existing_learning_id))

        existing.evidence = _merge_evidence(existing.evidence, draft_learning.evidence)
        existing.confidence = _promote_confidence(
            existing.confidence,
            draft_learning.confidence,
        )
        existing.review_date = _max_review_date(
            existing.review_date,
            draft_learning.review_date,
        )
        if existing.embedding is None:
            existing.embedding = self.embed_learning_text(
                existing.title,
                existing.action_rule,
            )

        self._session.flush()
        self._session.refresh(existing)
        return LearningModel.model_validate(existing)

    def create_related_edge(
        self,
        *,
        learning_id: uuid.UUID,
        related_learning_id: uuid.UUID,
        created_by: uuid.UUID | None,
    ) -> None:
        existing_edge = self._session.scalar(
            sa.select(EdgeRecord.id).where(
                EdgeRecord.relation == EdgeRelation.RELATED_TO,
                sa.or_(
                    sa.and_(
                        EdgeRecord.src_entity_type == "learning",
                        EdgeRecord.src_id == learning_id,
                        EdgeRecord.dst_entity_type == "learning",
                        EdgeRecord.dst_id == related_learning_id,
                    ),
                    sa.and_(
                        EdgeRecord.src_entity_type == "learning",
                        EdgeRecord.src_id == related_learning_id,
                        EdgeRecord.dst_entity_type == "learning",
                        EdgeRecord.dst_id == learning_id,
                    ),
                ),
            )
        )
        if existing_edge is not None:
            return

        edge = EdgeRecord(
            src_entity_type="learning",
            src_id=learning_id,
            dst_entity_type="learning",
            dst_id=related_learning_id,
            relation=EdgeRelation.RELATED_TO,
            edge_metadata={},
            created_by=created_by,
        )
        self._session.add(edge)
        self._session.flush()

    def _embedding_for_record(self, record: LearningRecord) -> list[float]:
        embedding = normalize_embedding(record.embedding)
        if embedding is not None:
            return embedding

        embedding = self.embed_learning_text(record.title, record.action_rule)
        record.embedding = embedding
        self._session.flush()
        return embedding

    def _embedding_for_text(self, text: str) -> list[float]:
        embedding = normalize_embedding(self._embed_text(text))
        if embedding is None:
            raise ValueError("learning embedder must return a numeric vector")
        return embedding


__all__ = [
    "ConfirmLearningDraftRequest",
    "DEDUPLICATE_THRESHOLD",
    "DedupeSuggestion",
    "LearningDedupeService",
    "MergeDecision",
    "build_dedupe_text",
]
