"""Tiered retrieval service for learnings."""

from __future__ import annotations

import datetime as dt
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session

from agenticqueue_api.models.learning import LearningRecord
from agenticqueue_api.models.task import TaskRecord
from agenticqueue_api.retrieval.config import RetrievalConfig, get_retrieval_config
from agenticqueue_api.retrieval.tiers.fts import fts_candidates
from agenticqueue_api.retrieval.tiers.graph import (
    rank_candidates as graph_rank_candidates,
)
from agenticqueue_api.retrieval.tiers.metadata import (
    apply_filters as apply_metadata_filters,
)
from agenticqueue_api.retrieval.tiers.rerank import rerank_candidates
from agenticqueue_api.retrieval.tiers.surface_area import (
    select_candidates as surface_candidates,
)
from agenticqueue_api.retrieval.tiers.trgm import trgm_candidates
from agenticqueue_api.retrieval.tiers.vector import (
    task_similarity_text,
    vector_candidates,
    vector_text_candidates,
)
from agenticqueue_api.retrieval.types import (
    RetrievalCandidate,
    RetrievalQuery,
    RetrievalResult,
    RetrievalSearchQuery,
)
from agenticqueue_api.schemas.learning import LearningStatus


class RetrievalService:
    """Resolve hot-path and cold-path learnings for one task."""

    def __init__(
        self,
        session: Session,
        *,
        config: RetrievalConfig | None = None,
    ) -> None:
        self._session = session
        self._config = config or get_retrieval_config()

    def retrieve(self, query: RetrievalQuery) -> RetrievalResult:
        task = self._session.get(TaskRecord, query.task_id)
        if task is None:
            raise KeyError(str(query.task_id))

        scope = query.scope.with_defaults(task)
        candidates = self._candidate_pool(project_id=scope.project_id)
        tiers_fired: list[str] = []

        hot_candidates = surface_candidates(candidates, scope=scope)
        tiers_fired.append("surface_area")
        hot_candidates = graph_rank_candidates(
            self._session,
            task_id=task.id,
            candidates=hot_candidates,
        )
        tiers_fired.append("graph")
        hot_candidates = apply_metadata_filters(
            hot_candidates,
            layers=query.layers,
            owners=scope.owners,
            learning_types=scope.learning_types,
            reference=task.created_at,
            max_age_days=scope.max_age_days,
        )
        tiers_fired.append("metadata")
        result_candidates = hot_candidates[: query.k]

        cold_candidates, cold_tiers = self._cold_candidates(
            task=task,
            candidates=candidates,
            result_candidates=result_candidates,
            query=query,
            scope=scope,
        )
        if cold_candidates:
            tiers_fired.extend(cold_tiers)
            reranked = rerank_candidates(
                task=task,
                candidates=self._merge_candidates(result_candidates, cold_candidates),
                config=self._config,
                limit=query.k,
            )
            result_candidates = reranked
            tiers_fired.append("rerank")

        return RetrievalResult(
            items=[candidate.to_model() for candidate in result_candidates[: query.k]],
            tiers_fired=tiers_fired,
        )

    def search(self, query: RetrievalSearchQuery) -> RetrievalResult:
        scope = query.scope.normalized()
        candidates = self._candidate_pool(project_id=None)
        if scope.project_id is not None:
            candidates = [
                candidate
                for candidate in candidates
                if candidate.source_task is not None
                and candidate.source_task.project_id == scope.project_id
            ]

        tiers_fired: list[str] = []
        hot_candidates = surface_candidates(candidates, scope=scope)
        tiers_fired.append("surface_area")
        hot_candidates = apply_metadata_filters(
            hot_candidates,
            layers=query.layers,
            owners=scope.owners,
            learning_types=scope.learning_types,
            reference=dt.datetime.now(dt.UTC),
            max_age_days=scope.max_age_days,
        )
        tiers_fired.append("metadata")

        result_candidates: list[RetrievalCandidate] = []
        fts_matches = fts_candidates(
            self._session,
            query_text=query.query,
            candidates=hot_candidates,
            exclude_ids=set(),
            limit=query.k,
        )
        if fts_matches:
            result_candidates = self._merge_candidates(result_candidates, fts_matches)
            tiers_fired.append("fts")

        cold_limit = max(query.k - len(result_candidates), 0)
        if query.fuzzy_global_search and cold_limit > 0:
            trgm_matches = trgm_candidates(
                self._session,
                query_text=query.query,
                candidates=hot_candidates,
                exclude_ids={candidate.learning.id for candidate in result_candidates},
                limit=cold_limit,
            )
            if trgm_matches:
                result_candidates = self._merge_candidates(
                    result_candidates,
                    trgm_matches,
                )
                tiers_fired.append("trgm")

            vector_matches = vector_text_candidates(
                self._session,
                query_text=query.query,
                candidates=self._vector_pool(
                    hot_candidates,
                    project_id=scope.project_id,
                ),
                exclude_ids={candidate.learning.id for candidate in result_candidates},
                limit=max(query.k - len(result_candidates), 0),
            )
            if vector_matches:
                result_candidates = self._merge_candidates(
                    result_candidates,
                    vector_matches,
                )
                tiers_fired.append("vector")

        return RetrievalResult(
            items=[candidate.to_model() for candidate in result_candidates[: query.k]],
            tiers_fired=tiers_fired,
        )

    def _cold_candidates(
        self,
        *,
        task: TaskRecord,
        candidates: list[RetrievalCandidate],
        result_candidates: list[RetrievalCandidate],
        query: RetrievalQuery,
        scope,
    ) -> tuple[list[RetrievalCandidate], list[str]]:
        if not query.fuzzy_global_search or len(result_candidates) >= query.k:
            return [], []

        cold_limit = min(
            self._config.vector_candidate_limit,
            max(query.k - len(result_candidates), 1),
        )
        cold_pool = apply_metadata_filters(
            self._vector_pool(candidates, project_id=scope.project_id),
            layers=query.layers,
            owners=scope.owners,
            learning_types=scope.learning_types,
            reference=task.created_at,
            max_age_days=scope.max_age_days,
        )
        if not cold_pool:
            return [], []

        exclude_ids = {candidate.learning.id for candidate in result_candidates}
        cold_candidates: list[RetrievalCandidate] = []
        tiers_fired: list[str] = []
        query_text = task_similarity_text(task)

        fts_matches = fts_candidates(
            self._session,
            query_text=query_text,
            candidates=cold_pool,
            exclude_ids=exclude_ids,
            limit=cold_limit,
        )
        if fts_matches:
            cold_candidates = self._merge_candidates(cold_candidates, fts_matches)
            tiers_fired.append("fts")

        trgm_matches = trgm_candidates(
            self._session,
            query_text=task.title or query_text,
            candidates=cold_pool,
            exclude_ids=exclude_ids
            | {candidate.learning.id for candidate in cold_candidates},
            limit=max(cold_limit - len(cold_candidates), 0),
        )
        if trgm_matches:
            cold_candidates = self._merge_candidates(cold_candidates, trgm_matches)
            tiers_fired.append("trgm")

        vector_matches = vector_candidates(
            self._session,
            task=task,
            candidates=cold_pool,
            exclude_ids=exclude_ids
            | {candidate.learning.id for candidate in cold_candidates},
            limit=max(cold_limit - len(cold_candidates), 0),
        )
        if vector_matches:
            cold_candidates = self._merge_candidates(cold_candidates, vector_matches)
            tiers_fired.append("vector")

        return cold_candidates, tiers_fired

    def _candidate_pool(
        self,
        *,
        project_id: uuid.UUID | None,
    ) -> list[RetrievalCandidate]:
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
        tasks_by_id: dict[uuid.UUID, TaskRecord] = {}
        if task_ids:
            task_rows = self._session.scalars(
                sa.select(TaskRecord).where(TaskRecord.id.in_(task_ids))
            )
            tasks_by_id = {task.id: task for task in task_rows}

        candidates: list[RetrievalCandidate] = []
        for learning in learning_rows:
            source_task = (
                tasks_by_id.get(learning.task_id) if learning.task_id else None
            )
            candidates.append(
                RetrievalCandidate(learning=learning, source_task=source_task)
            )
        return candidates

    def _vector_pool(
        self,
        candidates: list[RetrievalCandidate],
        *,
        project_id: uuid.UUID | None,
    ) -> list[RetrievalCandidate]:
        if not self._config.vector_project_scope_only or project_id is None:
            return candidates
        return [
            candidate
            for candidate in candidates
            if candidate.source_task is not None
            and candidate.source_task.project_id == project_id
        ]

    @staticmethod
    def _merge_candidates(
        lhs: list[RetrievalCandidate],
        rhs: list[RetrievalCandidate],
    ) -> list[RetrievalCandidate]:
        merged: dict[uuid.UUID, RetrievalCandidate] = {}
        for candidate in [*lhs, *rhs]:
            existing = merged.get(candidate.learning.id)
            if (
                existing is None
                or candidate.vector_similarity > existing.vector_similarity
            ):
                merged[candidate.learning.id] = candidate
        return list(merged.values())


__all__ = ["RetrievalService"]
