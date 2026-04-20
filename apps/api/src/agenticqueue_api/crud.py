"""Generic CRUD routes for the Phase 1 entity surface."""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass, field
from enum import Enum
import uuid
from typing import Any, Callable

import pydantic
import sqlalchemy as sa
from fastapi import APIRouter, Depends, Request, Response, status
from jsonschema import ValidationError as JsonSchemaValidationError  # type: ignore[import-untyped]
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from agenticqueue_api.errors import raise_api_error
from agenticqueue_api.models import (
    ActorModel,
    ActorRecord,
    ArtifactModel,
    ArtifactRecord,
    DecisionModel,
    DecisionRecord,
    EdgeModel,
    EdgeRecord,
    EdgeRelation,
    LearningModel,
    LearningRecord,
    PolicyModel,
    PolicyRecord,
    ProjectModel,
    ProjectRecord,
    RunModel,
    RunRecord,
    TaskModel,
    TaskRecord,
    WorkspaceModel,
    WorkspaceRecord,
)
from agenticqueue_api.models.shared import SchemaModel
from agenticqueue_api.repo.graph import ensure_dependency_edge_is_acyclic
from agenticqueue_api.task_type_registry import SchemaLoadError, TaskTypeRegistry

IMMUTABLE_FIELDS = frozenset({"id", "created_at", "updated_at"})


@dataclass(frozen=True)
class CrudEntityConfig:
    """Configuration for one CRUD resource."""

    resource_name: str
    scope_name: str
    schema_type: type[SchemaModel]
    record_type: type[Any]
    field_to_record_attr: dict[str, str] = field(default_factory=dict)
    pre_persist: Any = None

    @property
    def read_scope(self) -> str:
        return f"{self.scope_name}:read"

    @property
    def write_scope(self) -> str:
        return f"{self.scope_name}:write"


ENTITY_CONFIGS = (
    CrudEntityConfig(
        resource_name="workspaces",
        scope_name="workspace",
        schema_type=WorkspaceModel,
        record_type=WorkspaceRecord,
    ),
    CrudEntityConfig(
        resource_name="projects",
        scope_name="project",
        schema_type=ProjectModel,
        record_type=ProjectRecord,
    ),
    CrudEntityConfig(
        resource_name="tasks",
        scope_name="task",
        schema_type=TaskModel,
        record_type=TaskRecord,
    ),
    CrudEntityConfig(
        resource_name="runs",
        scope_name="run",
        schema_type=RunModel,
        record_type=RunRecord,
    ),
    CrudEntityConfig(
        resource_name="artifacts",
        scope_name="artifact",
        schema_type=ArtifactModel,
        record_type=ArtifactRecord,
    ),
    CrudEntityConfig(
        resource_name="decisions",
        scope_name="decision",
        schema_type=DecisionModel,
        record_type=DecisionRecord,
    ),
    CrudEntityConfig(
        resource_name="actors",
        scope_name="actor",
        schema_type=ActorModel,
        record_type=ActorRecord,
    ),
    CrudEntityConfig(
        resource_name="policies",
        scope_name="policy",
        schema_type=PolicyModel,
        record_type=PolicyRecord,
    ),
    CrudEntityConfig(
        resource_name="learnings",
        scope_name="learning",
        schema_type=LearningModel,
        record_type=LearningRecord,
    ),
    CrudEntityConfig(
        resource_name="edges",
        scope_name="edge",
        schema_type=EdgeModel,
        record_type=EdgeRecord,
        field_to_record_attr={"metadata": "edge_metadata"},
        pre_persist=ensure_dependency_edge_is_acyclic,
    ),
)


def _require_scope(request: Request, required_scope: str) -> None:
    api_token = request.state.api_token
    scopes = set(api_token.scopes)
    if required_scope in scopes or "admin" in scopes:
        return

    raise_api_error(
        status.HTTP_403_FORBIDDEN,
        "Token missing required scope",
        details={
            "required_scope": required_scope,
            "granted_scopes": api_token.scopes,
        },
    )


def _record_attr_name(config: CrudEntityConfig, field_name: str) -> str:
    return config.field_to_record_attr.get(field_name, field_name)


def _get_record_or_404(
    session: Session,
    config: CrudEntityConfig,
    entity_id: uuid.UUID,
) -> Any:
    record = session.get(config.record_type, entity_id)
    if record is None:
        raise_api_error(
            status.HTTP_404_NOT_FOUND,
            f"{config.scope_name.capitalize()} not found",
        )
    return record


def _serialize_record(config: CrudEntityConfig, record: Any) -> SchemaModel:
    return config.schema_type.model_validate(record)


def _policy_is_attached(session: Session, policy_id: uuid.UUID) -> bool:
    statements = (
        sa.select(WorkspaceRecord.id)
        .where(WorkspaceRecord.policy_id == policy_id)
        .limit(1),
        sa.select(ProjectRecord.id)
        .where(ProjectRecord.policy_id == policy_id)
        .limit(1),
        sa.select(TaskRecord.id).where(TaskRecord.policy_id == policy_id).limit(1),
    )
    return any(session.scalar(statement) is not None for statement in statements)


def _apply_schema_to_record(
    config: CrudEntityConfig,
    record: Any,
    payload: SchemaModel,
) -> None:
    for field_name, value in payload.model_dump().items():
        setattr(record, _record_attr_name(config, field_name), value)


def _validate_payload(
    config: CrudEntityConfig,
    payload: dict[str, Any],
) -> SchemaModel:
    try:
        return config.schema_type.model_validate(payload)
    except pydantic.ValidationError as error:
        raise_api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Request validation failed",
            details=error.errors(),
        )


def _validate_task_contract(
    request: Request,
    config: CrudEntityConfig,
    payload: SchemaModel,
) -> None:
    if not (config.resource_name == "tasks" and isinstance(payload, TaskModel)):
        return

    registry = getattr(request.app.state, "task_type_registry", None)
    if not isinstance(registry, TaskTypeRegistry):
        raise_api_error(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "Task type registry not configured",
        )

    try:
        registry.validate_contract(payload.task_type, payload.contract)
    except SchemaLoadError as error:
        raise_api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Task contract validation failed",
            details={"task_type": payload.task_type, "reason": str(error)},
        )
    except JsonSchemaValidationError as error:
        raise_api_error(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Task contract validation failed",
            details={
                "task_type": payload.task_type,
                "message": error.message,
                "path": list(error.path),
                "schema_path": [str(part) for part in error.schema_path],
            },
        )


def _validate_patch(
    config: CrudEntityConfig,
    existing: SchemaModel,
    patch: dict[str, Any],
) -> SchemaModel:
    immutable_fields = sorted(IMMUTABLE_FIELDS.intersection(patch.keys()))
    if immutable_fields:
        raise_api_error(
            status.HTTP_400_BAD_REQUEST,
            "Immutable fields cannot be updated",
            details={"fields": immutable_fields},
        )

    merged = existing.model_dump()
    merged.update(patch)
    return _validate_payload(config, merged)


def _order_columns(record_type: type[Any]) -> list[Any]:
    columns = record_type.__table__.columns
    order = []
    for column_name in ("created_at", "updated_at", "id"):
        if column_name in columns:
            order.append(columns[column_name].asc())
    return order or [columns[0].asc()]


def _coerce_filter_value(column: Any, raw_value: str) -> Any:
    python_type = column.type.python_type

    if python_type is bool:
        normalized = raw_value.strip().lower()
        if normalized in {"true", "1"}:
            return True
        if normalized in {"false", "0"}:
            return False
        raise_api_error(
            status.HTTP_400_BAD_REQUEST,
            f"Invalid boolean value for '{column.name}'",
            details={"value": raw_value},
        )

    converters: dict[type[Any], Callable[[str], Any]] = {
        uuid.UUID: uuid.UUID,
        dt.datetime: dt.datetime.fromisoformat,
        dt.date: dt.date.fromisoformat,
        int: int,
    }
    converter = converters.get(python_type)

    try:
        if converter is not None:
            return converter(raw_value)
        if isinstance(python_type, type) and issubclass(python_type, Enum):
            return python_type(raw_value)
    except (TypeError, ValueError):
        raise_api_error(
            status.HTTP_400_BAD_REQUEST,
            f"Invalid filter value for '{column.name}'",
            details={"value": raw_value},
        )

    return raw_value


def _apply_filters(
    statement: Any,
    config: CrudEntityConfig,
    request: Request,
) -> Any:
    for field_name, raw_value in request.query_params.multi_items():
        if field_name not in config.record_type.__table__.columns:
            raise_api_error(
                status.HTTP_400_BAD_REQUEST,
                f"Unknown filter field '{field_name}'",
            )

        column = config.record_type.__table__.columns[field_name]
        coerced_value = _coerce_filter_value(column, raw_value)
        statement = statement.where(
            getattr(config.record_type, field_name) == coerced_value
        )
    return statement


def _maybe_validate_edge(
    config: CrudEntityConfig,
    session: Session,
    payload: SchemaModel,
) -> None:
    if not (
        config.pre_persist is ensure_dependency_edge_is_acyclic
        and isinstance(payload, EdgeModel)
        and payload.relation is EdgeRelation.DEPENDS_ON
        and payload.is_active
    ):
        return

    ensure_dependency_edge_is_acyclic(
        session,
        src_entity_type=payload.src_entity_type,
        src_entity_id=payload.src_id,
        dst_entity_type=payload.dst_entity_type,
        dst_entity_id=payload.dst_id,
    )


def _register_entity_routes(
    router: APIRouter,
    config: CrudEntityConfig,
    get_db_session: Any,
) -> None:
    def create_entity(
        payload: dict[str, Any],
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> SchemaModel:
        _require_scope(request, config.write_scope)
        validated = _validate_payload(config, payload)
        _validate_task_contract(request, config, validated)
        _maybe_validate_edge(config, session, validated)

        record = config.record_type()
        _apply_schema_to_record(config, record, validated)
        session.add(record)
        try:
            session.flush()
        except IntegrityError as error:
            raise_api_error(
                status.HTTP_409_CONFLICT,
                f"{config.scope_name.capitalize()} could not be created",
                details={"reason": str(error.orig) if error.orig is not None else None},
            )
        session.refresh(record)
        return _serialize_record(config, record)

    def get_entity(
        entity_id: uuid.UUID,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> SchemaModel:
        _require_scope(request, config.read_scope)
        record = _get_record_or_404(session, config, entity_id)
        return _serialize_record(config, record)

    def list_entities(
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> list[dict[str, Any]]:
        _require_scope(request, config.read_scope)
        statement = sa.select(config.record_type)
        statement = _apply_filters(statement, config, request)
        statement = statement.order_by(*_order_columns(config.record_type))
        return [
            _serialize_record(config, record).model_dump(mode="json")
            for record in session.scalars(statement).all()
        ]

    def update_entity(
        entity_id: uuid.UUID,
        payload: dict[str, Any],
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> SchemaModel:
        _require_scope(request, config.write_scope)
        record = _get_record_or_404(session, config, entity_id)
        if (
            config.resource_name == "policies"
            and payload
            and _policy_is_attached(session, entity_id)
        ):
            raise_api_error(
                status.HTTP_409_CONFLICT,
                "Policy version is immutable once attached",
                details={"policy_id": str(entity_id)},
            )
        validated = _validate_patch(config, _serialize_record(config, record), payload)
        _validate_task_contract(request, config, validated)
        _maybe_validate_edge(config, session, validated)
        _apply_schema_to_record(config, record, validated)
        try:
            session.flush()
        except IntegrityError as error:
            raise_api_error(
                status.HTTP_409_CONFLICT,
                f"{config.scope_name.capitalize()} could not be updated",
                details={"reason": str(error.orig) if error.orig is not None else None},
            )
        session.refresh(record)
        return _serialize_record(config, record)

    def delete_entity(
        entity_id: uuid.UUID,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> Response:
        _require_scope(request, config.write_scope)
        record = _get_record_or_404(session, config, entity_id)
        if hasattr(record, "is_active"):
            setattr(record, "is_active", False)
            session.flush()
            return Response(status_code=status.HTTP_204_NO_CONTENT)

        session.delete(record)
        session.flush()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    create_entity.__name__ = f"create_{config.resource_name}"
    get_entity.__name__ = f"get_{config.resource_name}"
    list_entities.__name__ = f"list_{config.resource_name}"
    update_entity.__name__ = f"update_{config.resource_name}"
    delete_entity.__name__ = f"delete_{config.resource_name}"
    router.add_api_route(
        f"/{config.resource_name}",
        create_entity,
        methods=["POST"],
        response_model=config.schema_type,
        status_code=status.HTTP_201_CREATED,
    )
    router.add_api_route(
        f"/{config.resource_name}/{{entity_id}}",
        get_entity,
        methods=["GET"],
        response_model=config.schema_type,
    )
    router.add_api_route(
        f"/{config.resource_name}",
        list_entities,
        methods=["GET"],
    )
    router.add_api_route(
        f"/{config.resource_name}/{{entity_id}}",
        update_entity,
        methods=["PATCH"],
        response_model=config.schema_type,
    )
    router.add_api_route(
        f"/{config.resource_name}/{{entity_id}}",
        delete_entity,
        methods=["DELETE"],
        status_code=status.HTTP_204_NO_CONTENT,
    )


def build_crud_router(get_db_session: Any) -> APIRouter:
    """Build the CRUD router for the Phase 1 entity surface."""

    router = APIRouter(prefix="/v1")
    for config in ENTITY_CONFIGS:
        _register_entity_routes(router, config, get_db_session)
    return router
