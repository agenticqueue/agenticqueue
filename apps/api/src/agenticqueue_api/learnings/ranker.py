"""Learning relevance ranking for context-packet injection."""

from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import re
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from agenticqueue_api.learnings.dedupe import LearningDedupeService, cosine_similarity
from agenticqueue_api.learnings.ranker_config import (
    DEFAULT_LEARNING_RANKER_CONFIG,
    LearningRankerConfig,
)
from agenticqueue_api.models import (
    DecisionRecord,
    EdgeRecord,
    EdgeRelation,
    LearningModel,
)
from agenticqueue_api.models.edge import edge_metadata_marks_superseded
from agenticqueue_api.models.learning import LearningRecord
from agenticqueue_api.models.task import TaskRecord
from agenticqueue_api.repo.graph import descendants
from agenticqueue_api.schemas.learning import LearningStatus

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_TASK_DECISION_EDGE_TYPES = frozenset(
    {
        EdgeRelation.INFORMED_BY,
        EdgeRelation.IMPLEMENTS,
        EdgeRelation.DERIVED_FROM,
        EdgeRelation.RELATED_TO,
        EdgeRelation.TRIGGERED,
    }
)
_TOOLING_TOKENS = frozenset(
    {
        "alembic",
        "api",
        "cli",
        "docker",
        "fastapi",
        "git",
        "github",
        "mcp",
        "mypy",
        "pgvector",
        "pytest",
        "python",
        "rest",
        "ruff",
        "sqlalchemy",
        "typer",
        "uv",
    }
)
_STOP_TOKENS = frozenset(
    {
        "a",
        "an",
        "and",
        "at",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "of",
        "on",
        "or",
        "the",
        "to",
        "v1",
        "with",
    }
)
_RECENCY_WINDOW_DAYS = 30.0


@dataclass(frozen=True)
class _TaskContext:
    task: TaskRecord
    embedding: list[float]
    scope_tokens: frozenset[str]
    tooling_tokens: frozenset[str]
    decision_ids: frozenset[uuid.UUID]
    dependency_ids: frozenset[uuid.UUID]
    created_at: dt.datetime


@dataclass(frozen=True)
class _Candidate:
    learning: LearningRecord
    source_task: TaskRecord | None
    embedding: list[float]
    diversity_embedding: list[float]
    scope_tokens: frozenset[str]
    tooling_tokens: frozenset[str]
    decision_ids: frozenset[uuid.UUID]
    dependency_ids: frozenset[uuid.UUID]
    base_score: float


class LearningRankerService:
    """Rank active learnings for one task."""

    def __init__(
        self,
        session: Session,
        *,
        config: LearningRankerConfig = DEFAULT_LEARNING_RANKER_CONFIG,
    ) -> None:
        self._session = session
        self._config = config
        self._dedupe = LearningDedupeService(session)

    def rank_learnings_for_task(
        self,
        task_id: uuid.UUID,
        *,
        k: int = 5,
    ) -> list[LearningModel]:
        if k < 1:
            raise ValueError("k must be at least 1")

        task = self._session.get(TaskRecord, task_id)
        if task is None:
            raise KeyError(str(task_id))

        target = self._build_task_context(task)
        candidates = self._scored_candidates(target)
        selected = self._select_diverse_candidates(candidates, k=k)
        return [
            LearningModel.model_validate(candidate.learning) for candidate in selected
        ]

    def _build_task_context(self, task: TaskRecord) -> _TaskContext:
        return _TaskContext(
            task=task,
            embedding=self._dedupe.embed_text(_task_text(task)),
            scope_tokens=_task_scope_tokens(task),
            tooling_tokens=_task_tooling_tokens(task),
            decision_ids=self._task_decision_ids(task.id),
            dependency_ids=self._task_dependency_ids(task.id),
            created_at=task.created_at,
        )

    def _scored_candidates(self, target: _TaskContext) -> list[_Candidate]:
        learning_rows = list(
            self._session.scalars(
                sa.select(LearningRecord)
                .where(LearningRecord.status == LearningStatus.ACTIVE.value)
                .order_by(LearningRecord.created_at.asc(), LearningRecord.id.asc())
            )
        )
        if not learning_rows:
            return []

        task_ids = {
            learning.task_id
            for learning in learning_rows
            if learning.task_id is not None
        }
        tasks_by_id = self._load_tasks(task_ids)
        learning_decision_ids = self._learning_decision_ids(
            {learning.id for learning in learning_rows}
        )

        candidates: list[_Candidate] = []
        dependency_cache: dict[uuid.UUID, frozenset[uuid.UUID]] = {}
        for learning in learning_rows:
            source_task = (
                tasks_by_id.get(learning.task_id) if learning.task_id else None
            )
            dependency_ids: frozenset[uuid.UUID] = frozenset()
            if source_task is not None:
                dependency_ids = dependency_cache.setdefault(
                    source_task.id,
                    self._task_dependency_ids(source_task.id),
                )

            candidate = _Candidate(
                learning=learning,
                source_task=source_task,
                embedding=self._dedupe.embed_text(
                    _learning_text(learning, source_task)
                ),
                diversity_embedding=self._dedupe.embed_learning_text(
                    learning.title,
                    learning.action_rule,
                ),
                scope_tokens=_learning_scope_tokens(learning, source_task),
                tooling_tokens=_learning_tooling_tokens(learning, source_task),
                decision_ids=learning_decision_ids.get(learning.id, frozenset()),
                dependency_ids=dependency_ids,
                base_score=0.0,
            )
            candidates.append(
                _Candidate(
                    learning=candidate.learning,
                    source_task=candidate.source_task,
                    embedding=candidate.embedding,
                    diversity_embedding=candidate.diversity_embedding,
                    scope_tokens=candidate.scope_tokens,
                    tooling_tokens=candidate.tooling_tokens,
                    decision_ids=candidate.decision_ids,
                    dependency_ids=candidate.dependency_ids,
                    base_score=self._score_candidate(target, candidate),
                )
            )

        return sorted(
            candidates,
            key=lambda candidate: (
                -candidate.base_score,
                candidate.learning.created_at,
                str(candidate.learning.id),
            ),
        )

    def _score_candidate(self, target: _TaskContext, candidate: _Candidate) -> float:
        source_task = candidate.source_task
        task_type_match = (
            1.0
            if source_task is not None
            and source_task.task_type == target.task.task_type
            else 0.0
        )
        project_match = (
            1.0
            if source_task is not None
            and source_task.project_id == target.task.project_id
            else 0.0
        )
        repo_scope_overlap = _jaccard(target.scope_tokens, candidate.scope_tokens)
        linked_decision_overlap = _jaccard_ids(
            target.decision_ids, candidate.decision_ids
        )
        shared_dependency_overlap = _jaccard_ids(
            target.dependency_ids,
            candidate.dependency_ids,
        )
        tooling_overlap = _jaccard(target.tooling_tokens, candidate.tooling_tokens)
        text_similarity = cosine_similarity(target.embedding, candidate.embedding)
        recency_score = _recency_score(
            candidate.learning.created_at,
            reference=target.created_at,
        )

        return (
            task_type_match * self._config.task_type_match_weight
            + project_match * self._config.project_match_weight
            + repo_scope_overlap * self._config.repo_scope_overlap_weight
            + linked_decision_overlap * self._config.linked_decision_overlap_weight
            + shared_dependency_overlap * self._config.shared_dependency_overlap_weight
            + tooling_overlap * self._config.tooling_overlap_weight
            + text_similarity * self._config.text_similarity_weight
            + recency_score * self._config.recency_weight
            + _scope_bonus(learning_scope=candidate.learning.scope, config=self._config)
        )

    def _select_diverse_candidates(
        self,
        candidates: list[_Candidate],
        *,
        k: int,
    ) -> list[_Candidate]:
        selected: list[_Candidate] = []
        deferred: list[_Candidate] = []
        for candidate in candidates:
            if len(selected) >= k:
                break
            if any(
                cosine_similarity(
                    candidate.diversity_embedding,
                    existing.diversity_embedding,
                )
                >= self._config.diversity_similarity_threshold
                for existing in selected
            ):
                deferred.append(candidate)
                continue
            selected.append(candidate)

        if len(selected) < k:
            for candidate in deferred:
                if len(selected) >= k:
                    break
                selected.append(candidate)
        return selected

    def _load_tasks(self, task_ids: set[uuid.UUID]) -> dict[uuid.UUID, TaskRecord]:
        if not task_ids:
            return {}
        rows = self._session.scalars(
            sa.select(TaskRecord).where(TaskRecord.id.in_(task_ids))
        )
        return {task.id: task for task in rows}

    def _task_decision_ids(self, task_id: uuid.UUID) -> frozenset[uuid.UUID]:
        decision_ids = set(
            self._session.scalars(
                sa.select(DecisionRecord.id).where(DecisionRecord.task_id == task_id)
            )
        )
        if decision_ids:
            return frozenset(decision_ids)

        related_decisions = set(
            self._session.scalars(
                sa.select(EdgeRecord.dst_id)
                .where(EdgeRecord.src_entity_type == "task")
                .where(EdgeRecord.src_id == task_id)
                .where(EdgeRecord.dst_entity_type == "decision")
                .where(EdgeRecord.relation.in_(_TASK_DECISION_EDGE_TYPES))
            )
        )
        reverse_decisions = set(
            self._session.scalars(
                sa.select(EdgeRecord.src_id)
                .where(EdgeRecord.dst_entity_type == "task")
                .where(EdgeRecord.dst_id == task_id)
                .where(EdgeRecord.src_entity_type == "decision")
                .where(EdgeRecord.relation.in_(_TASK_DECISION_EDGE_TYPES))
            )
        )
        return frozenset({*related_decisions, *reverse_decisions})

    def _task_dependency_ids(self, task_id: uuid.UUID) -> frozenset[uuid.UUID]:
        hits = descendants(
            self._session,
            "task",
            task_id,
            edge_types=(EdgeRelation.DEPENDS_ON,),
        )
        return frozenset(hit.entity_id for hit in hits if hit.entity_type == "task")

    def _learning_decision_ids(
        self,
        learning_ids: set[uuid.UUID],
    ) -> dict[uuid.UUID, frozenset[uuid.UUID]]:
        if not learning_ids:
            return {}

        statement = sa.select(EdgeRecord).where(
            EdgeRecord.relation == EdgeRelation.LEARNED_FROM
        )
        rows = list(self._session.scalars(statement))
        decision_ids_by_learning: dict[uuid.UUID, set[uuid.UUID]] = {}
        for edge in rows:
            if edge_metadata_marks_superseded(edge.edge_metadata):
                continue

            learning_id: uuid.UUID | None = None
            decision_id: uuid.UUID | None = None
            if edge.src_entity_type == "learning" and edge.src_id in learning_ids:
                learning_id = edge.src_id
                if edge.dst_entity_type == "decision":
                    decision_id = edge.dst_id
            elif edge.dst_entity_type == "learning" and edge.dst_id in learning_ids:
                learning_id = edge.dst_id
                if edge.src_entity_type == "decision":
                    decision_id = edge.src_id

            if learning_id is None or decision_id is None:
                continue
            decision_ids_by_learning.setdefault(learning_id, set()).add(decision_id)

        return {
            learning_id: frozenset(decision_ids)
            for learning_id, decision_ids in decision_ids_by_learning.items()
        }


def rank_learnings_for_task(
    session: Session,
    task_id: uuid.UUID,
    *,
    k: int = 5,
    config: LearningRankerConfig = DEFAULT_LEARNING_RANKER_CONFIG,
) -> list[LearningModel]:
    """Return the top-k active learnings for one task."""

    return LearningRankerService(session, config=config).rank_learnings_for_task(
        task_id,
        k=k,
    )


def _task_text(task: TaskRecord) -> str:
    parts = [
        task.task_type,
        task.title,
        task.description or "",
    ]
    contract = task.contract or {}
    parts.extend(_string_list(contract.get("file_scope")))
    parts.extend(_string_list(contract.get("surface_area")))
    parts.append(_string_value(contract.get("spec")))
    return "\n".join(part for part in parts if part)


def _learning_text(learning: LearningRecord, source_task: TaskRecord | None) -> str:
    parts = [
        learning.title,
        learning.action_rule,
        learning.what_happened,
        learning.what_learned,
        learning.applies_when,
        learning.does_not_apply_when,
        *learning.evidence,
    ]
    if source_task is not None:
        parts.extend(_string_list((source_task.contract or {}).get("file_scope")))
        parts.extend(_string_list((source_task.contract or {}).get("surface_area")))
        parts.append(_string_value((source_task.contract or {}).get("spec")))
    return "\n".join(part for part in parts if part)


def _task_scope_tokens(task: TaskRecord) -> frozenset[str]:
    contract = task.contract or {}
    return _scope_tokens(
        [
            *_string_list(contract.get("file_scope")),
            *_string_list(contract.get("surface_area")),
            task.title,
            _string_value(contract.get("spec")),
        ]
    )


def _learning_scope_tokens(
    learning: LearningRecord,
    source_task: TaskRecord | None,
) -> frozenset[str]:
    parts = [learning.title, learning.action_rule, *learning.evidence]
    if source_task is not None:
        contract = source_task.contract or {}
        parts.extend(_string_list(contract.get("file_scope")))
        parts.extend(_string_list(contract.get("surface_area")))
    return _scope_tokens(parts)


def _task_tooling_tokens(task: TaskRecord) -> frozenset[str]:
    return _tooling_tokens([_task_text(task)])


def _learning_tooling_tokens(
    learning: LearningRecord,
    source_task: TaskRecord | None,
) -> frozenset[str]:
    return _tooling_tokens([_learning_text(learning, source_task)])


def _scope_tokens(parts: list[str]) -> frozenset[str]:
    tokens = {
        token
        for token in _tokens(parts)
        if token not in _STOP_TOKENS and token not in _TOOLING_TOKENS
    }
    return frozenset(tokens)


def _tooling_tokens(parts: list[str]) -> frozenset[str]:
    tokens = {token for token in _tokens(parts) if token in _TOOLING_TOKENS}
    return frozenset(tokens)


def _tokens(parts: list[str]) -> set[str]:
    tokens: set[str] = set()
    for part in parts:
        for token in _TOKEN_RE.findall(part.lower()):
            if len(token) < 2:
                continue
            tokens.add(token)
    return tokens


def _jaccard(lhs: frozenset[str], rhs: frozenset[str]) -> float:
    if not lhs or not rhs:
        return 0.0
    union = lhs | rhs
    if not union:
        return 0.0
    return len(lhs & rhs) / len(union)


def _jaccard_ids(lhs: frozenset[uuid.UUID], rhs: frozenset[uuid.UUID]) -> float:
    if not lhs or not rhs:
        return 0.0
    union = lhs | rhs
    if not union:
        return 0.0
    return len(lhs & rhs) / len(union)


def _scope_bonus(
    *,
    learning_scope: str,
    config: LearningRankerConfig,
) -> float:
    if learning_scope == "task":
        return config.task_scope_bonus
    if learning_scope == "project":
        return config.project_scope_bonus
    if learning_scope == "global":
        return config.global_scope_bonus
    return 0.0


def _recency_score(
    created_at: dt.datetime,
    *,
    reference: dt.datetime,
) -> float:
    age = max((reference - created_at).total_seconds(), 0.0)
    age_days = age / 86400.0
    return max(0.0, 1.0 - min(age_days, _RECENCY_WINDOW_DAYS) / _RECENCY_WINDOW_DAYS)


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in (_string_value(item) for item in value) if item]


def _string_value(value: object) -> str:
    if isinstance(value, str):
        return value.strip()
    return ""


__all__ = [
    "LearningRankerService",
    "rank_learnings_for_task",
]
