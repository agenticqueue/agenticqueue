"""Dedicated task-type registry routes shared by the REST surface."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any, cast

from fastapi import APIRouter, Body, Depends, Query, Request, Response, status
from pydantic import ConfigDict, Field
from sqlalchemy.orm import Session

from agenticqueue_api.capabilities import require_capability
from agenticqueue_api.errors import raise_api_error
from agenticqueue_api.models import ActorModel, CapabilityKey
from agenticqueue_api.models.shared import SchemaModel
from agenticqueue_api.pagination import (
    DEFAULT_LIST_LIMIT,
    LIMIT_HEADER,
    MAX_LIST_LIMIT,
    NEXT_CURSOR_HEADER,
    coerce_cursor_value,
    decode_cursor,
    encode_cursor,
)
from agenticqueue_api.task_type_registry import TaskTypeDefinition, TaskTypeRegistry


class TaskTypeView(SchemaModel):
    """Task type registry entry exposed over the API."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    schema_document: dict[str, Any] = Field(alias="schema")
    policy: dict[str, Any]
    schema_path: str
    policy_path: str


class RegisterTaskTypeRequest(SchemaModel):
    """Payload for registering one task type."""

    model_config = ConfigDict(populate_by_name=True)

    name: str
    schema_document: dict[str, Any] = Field(alias="schema")
    policy: dict[str, Any] = Field(default_factory=dict)


class UpdateTaskTypeRequest(SchemaModel):
    """Payload for replacing one task type definition."""

    model_config = ConfigDict(populate_by_name=True)

    schema_document: dict[str, Any] = Field(alias="schema")
    policy: dict[str, Any] = Field(default_factory=dict)


def _require_actor(request: Request) -> ActorModel:
    actor = getattr(request.state, "actor", None)
    if not isinstance(actor, ActorModel):
        raise_api_error(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token")
    return actor


def _require_admin_actor(request: Request) -> ActorModel:
    actor = _require_actor(request)
    if actor.actor_type != "admin":
        raise_api_error(status.HTTP_403_FORBIDDEN, "Admin actor required")
    return actor


def _get_task_type_registry(request: Request) -> TaskTypeRegistry:
    return cast(TaskTypeRegistry, request.app.state.task_type_registry)


def _task_type_view(definition: TaskTypeDefinition) -> TaskTypeView:
    return TaskTypeView(
        name=definition.name,
        schema=definition.schema,
        policy=definition.policy,
        schema_path=str(definition.schema_path),
        policy_path=str(definition.policy_path),
    )


def _paginate_definitions(
    definitions: Sequence[TaskTypeDefinition],
    *,
    response: Response,
    limit: int,
    cursor: str | None,
) -> list[TaskTypeDefinition]:
    cursor_values = None
    if cursor is not None:
        raw_values = decode_cursor(cursor, expected_size=1)
        cursor_values = [coerce_cursor_value(raw_values[0], str)]

    page: list[TaskTypeDefinition] = []
    for definition in definitions:
        if cursor_values is not None and (definition.name,) <= tuple(cursor_values):
            continue
        page.append(definition)
        if len(page) > limit:
            break

    response.headers[LIMIT_HEADER] = str(limit)
    if len(page) > limit:
        response.headers[NEXT_CURSOR_HEADER] = encode_cursor([page[limit - 1].name])
        return page[:limit]
    return page


def build_task_types_router(get_db_session: Callable[..., Session]) -> APIRouter:
    """Build the dedicated task-type registry router."""

    router = APIRouter()
    update_task_type_capability = require_capability(
        CapabilityKey.UPDATE_TASK,
        entity_type="task",
    )

    def _update_task_type_route_capability(
        request: Request,
        payload: dict[str, Any] | None = Body(default=None),
        session: Session = Depends(get_db_session),
    ) -> None:
        update_task_type_capability(
            request=request,
            session=session,
            payload=payload,
            entity_id=None,
        )

    _update_task_type_route_capability.__name__ = (
        update_task_type_capability.__name__
    )

    @router.get(
        "/task-types",
        include_in_schema=False,
        response_model=list[TaskTypeView],
    )
    @router.get("/v1/task-types", response_model=list[TaskTypeView])
    def list_task_types(
        request: Request,
        response: Response,
        limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
        cursor: str | None = Query(default=None),
    ) -> list[TaskTypeView]:
        _require_actor(request)
        definitions = sorted(
            _get_task_type_registry(request).list(),
            key=lambda definition: definition.name,
        )
        page = _paginate_definitions(
            definitions,
            response=response,
            limit=limit,
            cursor=cursor,
        )
        return [_task_type_view(definition) for definition in page]

    @router.post(
        "/task-types",
        include_in_schema=False,
        response_model=TaskTypeView,
        status_code=status.HTTP_201_CREATED,
    )
    @router.post(
        "/v1/task-types",
        response_model=TaskTypeView,
        status_code=status.HTTP_201_CREATED,
    )
    def register_task_type(
        payload: RegisterTaskTypeRequest,
        request: Request,
    ) -> TaskTypeView:
        _require_admin_actor(request)
        registry = _get_task_type_registry(request)
        try:
            definition = registry.register(
                name=payload.name,
                schema=payload.schema_document,
                policy=payload.policy,
            )
        except ValueError as error:
            raise_api_error(status.HTTP_400_BAD_REQUEST, str(error))
        return _task_type_view(definition)

    @router.get(
        "/task-types/{task_type_name}",
        include_in_schema=False,
        response_model=TaskTypeView,
    )
    @router.get("/v1/task-types/{task_type_name}", response_model=TaskTypeView)
    def get_task_type_endpoint(
        task_type_name: str,
        request: Request,
    ) -> TaskTypeView:
        _require_actor(request)
        try:
            definition = _get_task_type_registry(request).get(task_type_name)
        except ValueError:
            raise_api_error(status.HTTP_404_NOT_FOUND, "Task type not found")
        return _task_type_view(definition)

    @router.patch(
        "/v1/task-types/{task_type_name}",
        response_model=TaskTypeView,
        dependencies=[Depends(_update_task_type_route_capability)],
    )
    def update_task_type_endpoint(
        task_type_name: str,
        payload: UpdateTaskTypeRequest,
        request: Request,
    ) -> TaskTypeView:
        _require_admin_actor(request)
        registry = _get_task_type_registry(request)
        try:
            definition = registry.register(
                name=task_type_name,
                schema=payload.schema_document,
                policy=payload.policy,
            )
        except ValueError as error:
            raise_api_error(status.HTTP_400_BAD_REQUEST, str(error))
        return _task_type_view(definition)

    return router


__all__ = [
    "RegisterTaskTypeRequest",
    "TaskTypeView",
    "UpdateTaskTypeRequest",
    "build_task_types_router",
]
