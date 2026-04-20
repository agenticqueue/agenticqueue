"""Learning promotion thresholds and manual promotion helpers."""

from __future__ import annotations

from dataclasses import dataclass
import uuid

from pydantic import model_validator
import sqlalchemy as sa
from sqlalchemy.orm import Session

from agenticqueue_api.learnings.draft import DraftLearningRecord, DraftLearningStatus
from agenticqueue_api.models import LearningModel, LearningRecord, TaskRecord
from agenticqueue_api.models.shared import SchemaModel
from agenticqueue_api.schemas.learning import LearningScope, LearningStatus


class PromoteLearningRequest(SchemaModel):
    """Manual promotion target for one learning."""

    target_scope: LearningScope

    @model_validator(mode="after")
    def validate_target_scope(self) -> PromoteLearningRequest:
        if self.target_scope is LearningScope.TASK:
            raise ValueError("target_scope must be project or global")
        return self


@dataclass(frozen=True)
class _LearningOccurrence:
    learning: LearningRecord
    project_id: uuid.UUID | None
    occurrence_total: int
    occurrence_project_ids: frozenset[uuid.UUID]
    signature: tuple[str, str]


def _normalize_signature(text: str) -> str:
    return " ".join(text.strip().lower().split())


def _eligible_target_scope(scope: LearningScope) -> LearningScope:
    next_scope = {
        LearningScope.TASK: LearningScope.PROJECT,
        LearningScope.PROJECT: LearningScope.GLOBAL,
    }.get(scope)
    if next_scope is None:
        raise ValueError("global learnings cannot be promoted further")
    return next_scope


class LearningPromotionService:
    """Compute promotion eligibility and apply manual promotions."""

    def __init__(self, session: Session) -> None:
        self._session = session

    def auto_promote_candidates(self) -> list[LearningModel]:
        occurrences = self._collect_occurrences()
        eligible_ids = self._eligible_learning_ids(occurrences)

        mutated = False
        candidates: list[LearningRecord] = []
        for occurrence in occurrences:
            should_flag = occurrence.learning.id in eligible_ids
            if occurrence.learning.promotion_eligible != should_flag:
                occurrence.learning.promotion_eligible = should_flag
                mutated = True
            if should_flag:
                candidates.append(occurrence.learning)

        global_statement = sa.select(LearningRecord).where(
            LearningRecord.scope == LearningScope.GLOBAL.value,
            LearningRecord.promotion_eligible.is_(True),
        )
        for learning in self._session.scalars(global_statement):
            learning.promotion_eligible = False
            mutated = True

        if mutated:
            self._session.flush()

        return [LearningModel.model_validate(candidate) for candidate in candidates]

    def promote(
        self,
        *,
        learning_id: uuid.UUID,
        target_scope: LearningScope,
    ) -> LearningModel:
        learning = self._session.get(LearningRecord, learning_id)
        if learning is None:
            raise KeyError(str(learning_id))
        if learning.status != LearningStatus.ACTIVE.value:
            raise ValueError("only active learnings can be promoted")

        current_scope = LearningScope(learning.scope)
        allowed_target = _eligible_target_scope(current_scope)
        if target_scope != allowed_target:
            raise ValueError(
                f"{current_scope.value} learnings can only promote to {allowed_target.value}"
            )

        learning.scope = target_scope.value
        learning.promotion_eligible = False
        self._session.flush()
        self.auto_promote_candidates()
        self._session.refresh(learning)
        return LearningModel.model_validate(learning)

    def _collect_occurrences(self) -> list[_LearningOccurrence]:
        learning_rows = self._session.execute(
            sa.select(LearningRecord, TaskRecord.project_id)
            .outerjoin(TaskRecord, TaskRecord.id == LearningRecord.task_id)
            .where(LearningRecord.status == LearningStatus.ACTIVE.value)
            .where(
                LearningRecord.scope.in_(
                    [LearningScope.TASK.value, LearningScope.PROJECT.value]
                )
            )
            .order_by(LearningRecord.created_at.asc(), LearningRecord.id.asc())
        ).all()

        draft_rows = self._session.execute(
            sa.select(DraftLearningRecord.confirmed_learning_id, TaskRecord.project_id)
            .join(TaskRecord, TaskRecord.id == DraftLearningRecord.task_id)
            .where(
                DraftLearningRecord.draft_status == DraftLearningStatus.CONFIRMED.value,
                DraftLearningRecord.confirmed_learning_id.is_not(None),
            )
        ).all()

        draft_project_ids: dict[uuid.UUID, list[uuid.UUID]] = {}
        for learning_id, project_id in draft_rows:
            if learning_id is None:
                continue
            draft_project_ids.setdefault(learning_id, []).append(project_id)

        occurrences: list[_LearningOccurrence] = []
        for learning, project_id in learning_rows:
            merged_projects = draft_project_ids.get(learning.id, [])
            project_ids = {
                candidate
                for candidate in [project_id, *merged_projects]
                if candidate is not None
            }
            occurrences.append(
                _LearningOccurrence(
                    learning=learning,
                    project_id=project_id,
                    occurrence_total=(1 if project_id is not None else 0)
                    + len(merged_projects),
                    occurrence_project_ids=frozenset(project_ids),
                    signature=(
                        _normalize_signature(learning.title),
                        _normalize_signature(learning.action_rule),
                    ),
                )
            )
        return occurrences

    def _eligible_learning_ids(
        self,
        occurrences: list[_LearningOccurrence],
    ) -> set[uuid.UUID]:
        eligible_ids: set[uuid.UUID] = set()

        task_groups: dict[tuple[uuid.UUID, str, str], list[_LearningOccurrence]] = {}
        project_groups: dict[tuple[str, str], list[_LearningOccurrence]] = {}
        for occurrence in occurrences:
            scope = LearningScope(occurrence.learning.scope)
            if scope is LearningScope.TASK and occurrence.project_id is not None:
                task_groups.setdefault(
                    (occurrence.project_id, *occurrence.signature),
                    [],
                ).append(occurrence)
            elif scope is LearningScope.PROJECT:
                project_groups.setdefault(occurrence.signature, []).append(occurrence)

        for group in task_groups.values():
            total_occurrences = sum(item.occurrence_total for item in group)
            if total_occurrences < 2:
                continue
            eligible_ids.add(group[0].learning.id)

        for group in project_groups.values():
            total_occurrences = sum(item.occurrence_total for item in group)
            distinct_projects = {
                project_id
                for item in group
                for project_id in item.occurrence_project_ids
            }
            if total_occurrences < 3 or len(distinct_projects) < 2:
                continue
            eligible_ids.add(group[0].learning.id)

        return eligible_ids


__all__ = [
    "LearningPromotionService",
    "PromoteLearningRequest",
]
