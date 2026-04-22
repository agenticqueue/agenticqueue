"""Dedicated learning surfaces shared by REST, CLI, and MCP."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
import uuid
from typing import Annotated, Any, cast

import sqlalchemy as sa
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import Field, StringConstraints
from sqlalchemy.orm import Session

from agenticqueue_api.audit import set_session_audit_context
from agenticqueue_api.auth import AuthenticatedRequest, authenticate_api_token
from agenticqueue_api.capabilities import ensure_actor_has_capability
from agenticqueue_api.db import write_timeout
from agenticqueue_api.errors import HTTP_422_STATUS, error_payload, raise_api_error
from agenticqueue_api.learnings import (
    LearningDedupeService,
    LearningLifecycleService,
    LearningPromotionService,
    PromoteLearningRequest,
    rank_learnings_for_task,
)
from agenticqueue_api.models import (
    ActorModel,
    ApiTokenModel,
    CapabilityKey,
    LearningModel,
    LearningRecord,
    TaskRecord,
)
from agenticqueue_api.models.shared import SchemaModel
from agenticqueue_api.schemas.learning import (
    LearningSchemaModel,
    LearningScope,
    LearningStatus,
)

ShortQueryText = StringConstraints(strict=True, min_length=1, max_length=255)


@dataclass(frozen=True)
class LearningSurfaceError(Exception):
    """Structured error raised by transport-agnostic learning helpers."""

    status_code: int
    payload: dict[str, Any]


class RelevantLearningsRequest(SchemaModel):
    """Input for `get_relevant_learnings`."""

    task_id: uuid.UUID
    actor_id: uuid.UUID
    scope: LearningScope | None = None
    limit: int = Field(default=5, ge=1, le=10)


class RelevantLearningsResponse(SchemaModel):
    """Top relevant learnings for a task."""

    items: list[LearningModel]


class SubmitTaskLearningRequest(SchemaModel):
    """Input for `submit_task_learning`."""

    task_id: uuid.UUID
    learning_object: LearningSchemaModel


class LearningMutationResponse(SchemaModel):
    """Standard response for a single mutated learning."""

    learning: LearningModel


class SupersedeLearningRequest(SchemaModel):
    """Input for `supersede_learning`."""

    replaced_by: uuid.UUID
    reason: Annotated[str | None, ShortQueryText] = None


class SearchLearningsRequest(SchemaModel):
    """Input for `search_learnings`."""

    query: Annotated[str, ShortQueryText]
    project: uuid.UUID | None = None
    task_type: str | None = None
    repo_scope: str | None = None
    limit: int = Field(default=10, ge=1, le=25)


class SearchLearningsResponse(SchemaModel):
    """Matching learnings for a text query."""

    items: list[LearningModel]


def _surface_error(
    status_code: int,
    message: str,
    *,
    details: Any = None,
) -> LearningSurfaceError:
    return LearningSurfaceError(
        status_code=status_code,
        payload=error_payload(
            status_code=status_code,
            message=message,
            details=details,
        ),
    )


def _raise_surface_error(
    status_code: int,
    message: str,
    *,
    details: Any = None,
) -> None:
    raise _surface_error(status_code, message, details=details)


def _http_error(error: LearningSurfaceError) -> HTTPException:
    return HTTPException(status_code=error.status_code, detail=error.payload)


def _require_request_auth(request: Request) -> AuthenticatedRequest:
    actor = getattr(request.state, "actor", None)
    api_token = getattr(request.state, "api_token", None)
    if not isinstance(actor, ActorModel) or not isinstance(api_token, ApiTokenModel):
        raise_api_error(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token")
    return AuthenticatedRequest(actor=actor, api_token=api_token)


def authenticate_surface_token(
    session: Session,
    *,
    token: str | None,
    trace_id: str,
) -> AuthenticatedRequest:
    """Authenticate a non-HTTP surface and attach audit context."""

    if token is None or not token.strip():
        raise _surface_error(
            status.HTTP_401_UNAUTHORIZED,
            "Missing Authorization header",
        )

    authenticated = authenticate_api_token(session, token.strip())
    if authenticated is None:
        raise _surface_error(
            status.HTTP_401_UNAUTHORIZED,
            "Invalid bearer token",
        )

    set_session_audit_context(
        session,
        actor_id=authenticated.actor.id,
        trace_id=trace_id,
    )
    return authenticated


def _require_token_scope(
    authenticated: AuthenticatedRequest,
    required_scope: str,
) -> None:
    scopes = set(authenticated.api_token.scopes)
    if required_scope in scopes or "admin" in scopes:
        return

    raise _surface_error(
        status.HTTP_403_FORBIDDEN,
        "Token missing required scope",
        details={
            "required_scope": required_scope,
            "granted_scopes": authenticated.api_token.scopes,
        },
    )


def _require_actor_match(
    authenticated: AuthenticatedRequest,
    actor_id: uuid.UUID,
) -> None:
    if authenticated.actor.actor_type == "admin":
        return
    if authenticated.actor.id == actor_id:
        return
    raise _surface_error(
        status.HTTP_403_FORBIDDEN,
        "Authenticated actor does not match actor_id",
        details={
            "actor_id": str(actor_id),
            "authenticated_actor_id": str(authenticated.actor.id),
        },
    )


def _get_task_or_error(session: Session, task_id: uuid.UUID) -> TaskRecord:
    task = session.get(TaskRecord, task_id)
    if task is None:
        raise _surface_error(status.HTTP_404_NOT_FOUND, "Task not found")
    return task


def _get_learning_or_error(
    session: Session,
    learning_id: uuid.UUID,
) -> LearningRecord:
    learning = session.get(LearningRecord, learning_id)
    if learning is None:
        raise _surface_error(status.HTTP_404_NOT_FOUND, "Learning not found")
    return learning


def _project_scope_for_task(task: TaskRecord) -> dict[str, str]:
    return {"project_id": str(task.project_id)}


def _project_scope_for_learning(
    session: Session,
    learning: LearningRecord,
) -> dict[str, str]:
    if learning.task_id is None:
        return {}
    task = session.get(TaskRecord, learning.task_id)
    if task is None:
        return {}
    return _project_scope_for_task(task)


def _task_repo_scopes(task: TaskRecord | None) -> set[str]:
    if task is None:
        return set()
    contract = task.contract or {}
    values: list[str] = []
    for key in ("file_scope", "surface_area"):
        raw_value = contract.get(key)
        if isinstance(raw_value, list):
            values.extend(str(item).strip() for item in raw_value if str(item).strip())
    return set(values)


def invoke_get_relevant_learnings(
    session: Session,
    *,
    authenticated: AuthenticatedRequest,
    payload: RelevantLearningsRequest,
) -> RelevantLearningsResponse:
    """Return the ranked active learnings for one task."""

    _require_token_scope(authenticated, "learning:read")
    _require_actor_match(authenticated, payload.actor_id)
    task = _get_task_or_error(session, payload.task_id)
    ensure_actor_has_capability(
        session,
        actor=authenticated.actor,
        capability=CapabilityKey.READ_LEARNINGS,
        required_scope=_project_scope_for_task(task),
        entity_type="learning",
        entity_id=task.id,
    )
    try:
        learnings = rank_learnings_for_task(session, task.id, k=payload.limit)
    except KeyError as error:
        raise _surface_error(status.HTTP_404_NOT_FOUND, "Task not found") from error
    if payload.scope is not None:
        learnings = [
            learning for learning in learnings if learning.scope == payload.scope.value
        ]
    return RelevantLearningsResponse(items=learnings)


def invoke_submit_task_learning(
    session: Session,
    *,
    authenticated: AuthenticatedRequest,
    payload: SubmitTaskLearningRequest,
) -> LearningMutationResponse:
    """Persist one manual task learning."""

    _require_token_scope(authenticated, "learning:write")
    task = _get_task_or_error(session, payload.task_id)
    ensure_actor_has_capability(
        session,
        actor=authenticated.actor,
        capability=CapabilityKey.WRITE_LEARNING,
        required_scope=_project_scope_for_task(task),
        entity_type="learning",
        entity_id=task.id,
    )

    learning_object = payload.learning_object
    embedder = LearningDedupeService(session)
    learning_record = LearningRecord(
        task_id=task.id,
        owner_actor_id=authenticated.actor.id,
        owner=learning_object.owner,
        title=learning_object.title,
        learning_type=learning_object.type.value,
        what_happened=learning_object.what_happened,
        what_learned=learning_object.what_learned,
        action_rule=learning_object.action_rule,
        applies_when=learning_object.applies_when,
        does_not_apply_when=learning_object.does_not_apply_when,
        evidence=learning_object.evidence,
        scope=learning_object.scope.value,
        confidence=learning_object.confidence.value,
        status=learning_object.status.value,
        review_date=dt.date.fromisoformat(learning_object.review_date),
        embedding=embedder.embed_learning_text(
            learning_object.title,
            learning_object.action_rule,
        ),
    )
    session.add(learning_record)
    session.flush()
    session.refresh(learning_record)
    return LearningMutationResponse(
        learning=LearningModel.model_validate(learning_record)
    )


def invoke_promote_learning(
    session: Session,
    *,
    authenticated: AuthenticatedRequest,
    learning_id: uuid.UUID,
    payload: PromoteLearningRequest,
) -> LearningMutationResponse:
    """Promote a learning to project or global scope."""

    _require_token_scope(authenticated, "learning:write")
    learning = _get_learning_or_error(session, learning_id)
    ensure_actor_has_capability(
        session,
        actor=authenticated.actor,
        capability=CapabilityKey.PROMOTE_LEARNING,
        required_scope=_project_scope_for_learning(session, learning),
        entity_type="learning",
        entity_id=learning_id,
    )
    service = LearningPromotionService(session)
    try:
        promoted = service.promote(
            learning_id=learning_id,
            target_scope=payload.target_scope,
        )
    except KeyError as error:
        raise _surface_error(status.HTTP_404_NOT_FOUND, "Learning not found") from error
    except ValueError as error:
        raise _surface_error(status.HTTP_409_CONFLICT, str(error)) from error
    return LearningMutationResponse(learning=promoted)


def invoke_supersede_learning(
    session: Session,
    *,
    authenticated: AuthenticatedRequest,
    learning_id: uuid.UUID,
    payload: SupersedeLearningRequest,
) -> LearningMutationResponse:
    """Supersede one learning with another active learning."""

    _require_token_scope(authenticated, "learning:write")
    learning = _get_learning_or_error(session, learning_id)
    ensure_actor_has_capability(
        session,
        actor=authenticated.actor,
        capability=CapabilityKey.WRITE_LEARNING,
        required_scope=_project_scope_for_learning(session, learning),
        entity_type="learning",
        entity_id=learning_id,
    )
    service = LearningLifecycleService(session)
    try:
        updated = service.supersede(
            old_learning_id=learning_id,
            new_learning_id=payload.replaced_by,
            reason=payload.reason
            or f"Superseded by {payload.replaced_by} via learnings surface",
            created_by=authenticated.actor.id,
        )
    except KeyError as error:
        raise _surface_error(status.HTTP_404_NOT_FOUND, "Learning not found") from error
    except ValueError as error:
        raise _surface_error(status.HTTP_409_CONFLICT, str(error)) from error
    return LearningMutationResponse(learning=updated)


def invoke_search_learnings(
    session: Session,
    *,
    authenticated: AuthenticatedRequest,
    payload: SearchLearningsRequest,
) -> SearchLearningsResponse:
    """Search active learnings by text plus optional task filters."""

    _require_token_scope(authenticated, "learning:read")
    ensure_actor_has_capability(
        session,
        actor=authenticated.actor,
        capability=CapabilityKey.SEARCH_MEMORY,
        required_scope=(
            {} if payload.project is None else {"project_id": str(payload.project)}
        ),
        entity_type="learning",
        entity_id=None,
    )

    statement = (
        sa.select(LearningRecord, TaskRecord)
        .outerjoin(TaskRecord, TaskRecord.id == LearningRecord.task_id)
        .where(LearningRecord.status == LearningStatus.ACTIVE.value)
        .order_by(LearningRecord.created_at.desc(), LearningRecord.id.asc())
    )
    rows = session.execute(statement).all()
    query_text = payload.query.strip().lower()
    repo_scope = None if payload.repo_scope is None else payload.repo_scope.strip()

    items: list[LearningModel] = []
    for learning_record, task_record in rows:
        task = cast(TaskRecord | None, task_record)
        if payload.project is not None and (
            task is None or task.project_id != payload.project
        ):
            continue
        if payload.task_type is not None and (
            task is None or task.task_type != payload.task_type
        ):
            continue
        if repo_scope is not None and repo_scope not in _task_repo_scopes(task):
            continue

        haystack = "\n".join(
            [
                learning_record.title,
                learning_record.action_rule,
                learning_record.what_happened,
                learning_record.what_learned,
                *learning_record.evidence,
                *(sorted(_task_repo_scopes(task))),
            ]
        ).lower()
        if query_text not in haystack:
            continue
        items.append(LearningModel.model_validate(learning_record))
        if len(items) >= payload.limit:
            break

    return SearchLearningsResponse(items=items)


def build_learnings_router(get_db_session: Any) -> APIRouter:
    """Build the dedicated learnings REST surface."""

    router = APIRouter()

    @router.get(
        "/learnings/relevant",
        include_in_schema=False,
        response_model=RelevantLearningsResponse,
    )
    @router.get(
        "/v1/learnings/relevant",
        response_model=RelevantLearningsResponse,
    )
    def get_relevant_learnings_endpoint(
        task_id: uuid.UUID,
        actor_id: uuid.UUID,
        request: Request,
        scope: LearningScope | None = None,
        limit: int = Query(default=5, ge=1, le=10),
        session: Session = Depends(get_db_session),
    ) -> RelevantLearningsResponse:
        authenticated = _require_request_auth(request)
        payload = RelevantLearningsRequest(
            task_id=task_id,
            actor_id=actor_id,
            scope=scope,
            limit=limit,
        )
        try:
            return invoke_get_relevant_learnings(
                session,
                authenticated=authenticated,
                payload=payload,
            )
        except LearningSurfaceError as error:
            raise _http_error(error) from error

    @router.get(
        "/learnings/search",
        include_in_schema=False,
        response_model=SearchLearningsResponse,
    )
    @router.get(
        "/v1/learnings/search",
        response_model=SearchLearningsResponse,
    )
    def search_learnings_endpoint(
        request: Request,
        q: str | None = Query(default=None, alias="q"),
        project: uuid.UUID | None = None,
        task_type: str | None = None,
        repo_scope: str | None = None,
        limit: int = Query(default=10, ge=1, le=25),
        session: Session = Depends(get_db_session),
    ) -> SearchLearningsResponse:
        authenticated = _require_request_auth(request)
        raw_query = q if q is not None else request.query_params.get("query")
        if raw_query is None:
            raise_api_error(
                HTTP_422_STATUS,
                "Field required",
                details=[{"loc": ["query", "q"], "msg": "Field required"}],
            )
        payload = SearchLearningsRequest(
            query=raw_query,
            project=project,
            task_type=task_type,
            repo_scope=repo_scope,
            limit=limit,
        )
        try:
            return invoke_search_learnings(
                session,
                authenticated=authenticated,
                payload=payload,
            )
        except LearningSurfaceError as error:
            raise _http_error(error) from error

    @router.post(
        "/learnings/submit",
        include_in_schema=False,
        response_model=LearningMutationResponse,
        status_code=status.HTTP_201_CREATED,
    )
    @router.post(
        "/v1/learnings/submit",
        response_model=LearningMutationResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def submit_task_learning_endpoint(
        payload: SubmitTaskLearningRequest,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> LearningMutationResponse:
        authenticated = _require_request_auth(request)
        try:
            with write_timeout(session, endpoint="v1.learnings.submit"):
                return invoke_submit_task_learning(
                    session,
                    authenticated=authenticated,
                    payload=payload,
                )
        except LearningSurfaceError as error:
            raise _http_error(error) from error

    @router.post(
        "/learnings/{learning_id}/promote",
        include_in_schema=False,
        response_model=LearningMutationResponse,
    )
    @router.post(
        "/v1/learnings/{learning_id}/promote",
        response_model=LearningMutationResponse,
    )
    def promote_learning_endpoint(
        learning_id: uuid.UUID,
        payload: PromoteLearningRequest,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> LearningMutationResponse:
        authenticated = _require_request_auth(request)
        try:
            with write_timeout(session, endpoint="v1.learnings.promote"):
                return invoke_promote_learning(
                    session,
                    authenticated=authenticated,
                    learning_id=learning_id,
                    payload=payload,
                )
        except LearningSurfaceError as error:
            raise _http_error(error) from error

    @router.post(
        "/learnings/{learning_id}/supersede",
        include_in_schema=False,
        response_model=LearningMutationResponse,
    )
    @router.post(
        "/v1/learnings/{learning_id}/supersede",
        response_model=LearningMutationResponse,
    )
    def supersede_learning_endpoint(
        learning_id: uuid.UUID,
        payload: SupersedeLearningRequest,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> LearningMutationResponse:
        authenticated = _require_request_auth(request)
        try:
            with write_timeout(session, endpoint="v1.learnings.supersede"):
                return invoke_supersede_learning(
                    session,
                    authenticated=authenticated,
                    learning_id=learning_id,
                    payload=payload,
                )
        except LearningSurfaceError as error:
            raise _http_error(error) from error

    return router


__all__ = [
    "LearningMutationResponse",
    "LearningSurfaceError",
    "RelevantLearningsRequest",
    "RelevantLearningsResponse",
    "SearchLearningsRequest",
    "SearchLearningsResponse",
    "SubmitTaskLearningRequest",
    "SupersedeLearningRequest",
    "authenticate_surface_token",
    "build_learnings_router",
    "invoke_get_relevant_learnings",
    "invoke_promote_learning",
    "invoke_search_learnings",
    "invoke_submit_task_learning",
    "invoke_supersede_learning",
]
