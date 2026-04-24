"""Dedicated decision mutation routes shared by the REST surface."""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, cast

import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request, status
from sqlalchemy.orm import Session

from agenticqueue_api.capabilities import ensure_actor_has_capability
from agenticqueue_api.db import write_timeout
from agenticqueue_api.errors import raise_api_error
from agenticqueue_api.models import (
    ActorModel,
    CapabilityKey,
    DecisionRecord,
    EdgeModel,
    EdgeRelation,
    TaskRecord,
)
from agenticqueue_api.models.shared import SchemaModel
from agenticqueue_api.repo import create_edge


class DecisionSupersedeRequest(SchemaModel):
    """Payload linking a replacement decision to the superseded one."""

    replaced_by: uuid.UUID


class DecisionLinkRequest(SchemaModel):
    """Payload linking one decision to one job/task."""

    job_id: uuid.UUID
    relation: EdgeRelation = EdgeRelation.INFORMED_BY


def _require_actor(request: Request) -> ActorModel:
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise_api_error(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token")
    return cast(ActorModel, actor)


def _require_token_scope(request: Request, required_scope: str) -> None:
    api_token = getattr(request.state, "api_token", None)
    scopes = set([] if api_token is None else api_token.scopes)
    if required_scope in scopes or "admin" in scopes:
        return
    raise_api_error(
        status.HTTP_403_FORBIDDEN,
        "Token missing required scope",
        details={
            "required_scope": required_scope,
            "granted_scopes": [] if api_token is None else api_token.scopes,
        },
    )


def _task_record_or_404(session: Session, task_id: uuid.UUID) -> TaskRecord:
    task = session.get(TaskRecord, task_id)
    if task is None:
        raise_api_error(status.HTTP_404_NOT_FOUND, "Task not found")
    return task


def _decision_record_or_404(
    session: Session,
    decision_id: uuid.UUID,
) -> DecisionRecord:
    decision = session.get(DecisionRecord, decision_id)
    if decision is None:
        raise_api_error(status.HTTP_404_NOT_FOUND, "Decision not found")
    return decision


def _task_record_for_decision_or_404(
    session: Session,
    decision: DecisionRecord,
) -> TaskRecord:
    return _task_record_or_404(session, decision.task_id)


def _require_update_task_capability_for_task(
    session: Session,
    *,
    actor: ActorModel,
    task: TaskRecord,
) -> None:
    ensure_actor_has_capability(
        session,
        actor=actor,
        capability=CapabilityKey.UPDATE_TASK,
        required_scope={"project_id": str(task.project_id)},
        entity_type="task",
        entity_id=task.id,
    )


def build_decisions_router(get_db_session: Any) -> APIRouter:
    """Build the decision mutation router."""

    router = APIRouter()

    @router.post(
        "/v1/decisions/{decision_id}/supersede",
        response_model=EdgeModel,
        status_code=status.HTTP_201_CREATED,
    )
    def supersede_decision_endpoint(
        decision_id: uuid.UUID,
        payload: DecisionSupersedeRequest,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> EdgeModel:
        with write_timeout(session, endpoint="v1.decisions.supersede"):
            actor = _require_actor(request)
            _require_token_scope(request, "decision:write")
            prior_decision = _decision_record_or_404(session, decision_id)
            replacement_decision = _decision_record_or_404(session, payload.replaced_by)
            _require_update_task_capability_for_task(
                session,
                actor=actor,
                task=_task_record_for_decision_or_404(session, prior_decision),
            )
            _require_update_task_capability_for_task(
                session,
                actor=actor,
                task=_task_record_for_decision_or_404(session, replacement_decision),
            )
            try:
                return create_edge(
                    session,
                    EdgeModel.model_validate(
                        {
                            "id": str(uuid.uuid4()),
                            "created_at": dt.datetime.now(dt.UTC).isoformat(),
                            "src_entity_type": "decision",
                            "src_id": str(payload.replaced_by),
                            "dst_entity_type": "decision",
                            "dst_id": str(decision_id),
                            "relation": EdgeRelation.SUPERSEDES.value,
                            "metadata": {},
                            "created_by": str(actor.id),
                        }
                    ),
                )
            except sa.exc.IntegrityError as error:
                raise_api_error(
                    status.HTTP_409_CONFLICT,
                    "Decision supersede link already exists",
                    details={"reason": str(error.orig) if error.orig else None},
                )

    @router.post(
        "/v1/decisions/{decision_id}/link",
        response_model=EdgeModel,
        status_code=status.HTTP_201_CREATED,
    )
    def link_decision_endpoint(
        decision_id: uuid.UUID,
        payload: DecisionLinkRequest,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> EdgeModel:
        with write_timeout(session, endpoint="v1.decisions.link"):
            actor = _require_actor(request)
            _require_token_scope(request, "decision:write")
            decision = _decision_record_or_404(session, decision_id)
            target_task = _task_record_or_404(session, payload.job_id)
            _require_update_task_capability_for_task(
                session,
                actor=actor,
                task=_task_record_for_decision_or_404(session, decision),
            )
            _require_update_task_capability_for_task(
                session,
                actor=actor,
                task=target_task,
            )
            try:
                return create_edge(
                    session,
                    EdgeModel.model_validate(
                        {
                            "id": str(uuid.uuid4()),
                            "created_at": dt.datetime.now(dt.UTC).isoformat(),
                            "src_entity_type": "decision",
                            "src_id": str(decision_id),
                            "dst_entity_type": "task",
                            "dst_id": str(payload.job_id),
                            "relation": payload.relation.value,
                            "metadata": {},
                            "created_by": str(actor.id),
                        }
                    ),
                )
            except sa.exc.IntegrityError as error:
                raise_api_error(
                    status.HTTP_409_CONFLICT,
                    "Decision link already exists",
                    details={"reason": str(error.orig) if error.orig else None},
                )

    return router


__all__ = [
    "DecisionLinkRequest",
    "DecisionSupersedeRequest",
    "build_decisions_router",
]
