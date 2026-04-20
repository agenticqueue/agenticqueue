"""FastAPI app for the AgenticQueue API surface."""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Iterator
from typing import Any, cast

import sqlalchemy as sa
from fastapi import Depends, FastAPI, Request, status
from pydantic import ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.audit import set_session_audit_context
from agenticqueue_api.auth import (
    AgenticQueueAuthMiddleware,
    get_api_token,
    issue_api_token,
    list_api_tokens_for_actor,
    revoke_api_token,
    token_display_prefix,
)
from agenticqueue_api.capabilities import (
    grant_capability,
    list_capabilities_for_actor,
    revoke_capability_grant,
)
from agenticqueue_api.config import (
    get_reload_enabled,
    get_sqlalchemy_sync_database_url,
    get_task_types_dir,
)
from agenticqueue_api.crud import build_crud_router
from agenticqueue_api.errors import install_exception_handlers, raise_api_error
from agenticqueue_api.middleware import IdempotencyKeyMiddleware
from agenticqueue_api.models import (
    ActorModel,
    ActorRecord,
    ApiTokenModel,
    CapabilityGrantModel,
    CapabilityKey,
)
from agenticqueue_api.models.shared import SchemaModel
from agenticqueue_api.task_type_registry import TaskTypeDefinition, TaskTypeRegistry


class ActorSummary(SchemaModel):
    """Compact actor payload surfaced from authenticated requests."""

    id: uuid.UUID
    handle: str
    actor_type: str
    display_name: str


class ApiTokenView(SchemaModel):
    """Non-secret token metadata returned from the API."""

    id: uuid.UUID
    actor_id: uuid.UUID
    token_prefix: str
    scopes: list[str]
    expires_at: dt.datetime | None = None
    revoked_at: dt.datetime | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class ApiTokenListResponse(SchemaModel):
    """Token list for the authenticated actor."""

    actor: ActorSummary
    tokens: list[ApiTokenView]


class ProvisionApiTokenRequest(SchemaModel):
    """Payload for issuing a token to an actor."""

    actor_id: uuid.UUID
    scopes: list[str] = Field(default_factory=list)
    expires_at: dt.datetime | None = None


class ProvisionApiTokenResponse(SchemaModel):
    """Provisioning response including the raw token once."""

    token: str
    api_token: ApiTokenView


class CapabilityGrantView(SchemaModel):
    """Capability grant returned from the API."""

    id: uuid.UUID
    actor_id: uuid.UUID
    capability_id: uuid.UUID
    capability: CapabilityKey
    scope: dict[str, Any] = Field(default_factory=dict)
    granted_by_actor_id: uuid.UUID | None = None
    expires_at: dt.datetime | None = None
    revoked_at: dt.datetime | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class ActorCapabilityListResponse(SchemaModel):
    """Active capabilities for one actor."""

    actor: ActorSummary
    capabilities: list[CapabilityGrantView]


class GrantCapabilityRequest(SchemaModel):
    """Payload for granting a capability."""

    actor_id: uuid.UUID
    capability: CapabilityKey
    scope: dict[str, Any] = Field(default_factory=dict)
    expires_at: dt.datetime | None = None


class RevokeCapabilityRequest(SchemaModel):
    """Payload for revoking a capability grant."""

    grant_id: uuid.UUID


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


def _default_session_factory() -> sessionmaker[Session]:
    engine = sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _default_task_type_registry() -> TaskTypeRegistry:
    registry = TaskTypeRegistry(
        get_task_types_dir(),
        reload_enabled=get_reload_enabled(),
    )
    registry.load()
    return registry


def _actor_summary(actor: ActorModel) -> ActorSummary:
    return ActorSummary(
        id=actor.id,
        handle=actor.handle,
        actor_type=actor.actor_type,
        display_name=actor.display_name,
    )


def _token_view(token: ApiTokenModel) -> ApiTokenView:
    return ApiTokenView(
        id=token.id,
        actor_id=token.actor_id,
        token_prefix=token_display_prefix(token.token_hash),
        scopes=token.scopes,
        expires_at=token.expires_at,
        revoked_at=token.revoked_at,
        created_at=token.created_at,
        updated_at=token.updated_at,
    )


def _capability_grant_view(grant: CapabilityGrantModel) -> CapabilityGrantView:
    return CapabilityGrantView(
        id=grant.id,
        actor_id=grant.actor_id,
        capability_id=grant.capability_id,
        capability=grant.capability,
        scope=grant.scope,
        granted_by_actor_id=grant.granted_by_actor_id,
        expires_at=grant.expires_at,
        revoked_at=grant.revoked_at,
        created_at=grant.created_at,
        updated_at=grant.updated_at,
    )


def _task_type_view(definition: TaskTypeDefinition) -> TaskTypeView:
    return TaskTypeView(
        name=definition.name,
        schema=definition.schema,
        policy=definition.policy,
        schema_path=str(definition.schema_path),
        policy_path=str(definition.policy_path),
    )


def _require_actor(request: Request) -> ActorModel:
    actor = getattr(request.state, "actor", None)
    if actor is None:
        raise_api_error(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token")
    return cast(ActorModel, actor)


def _require_admin_actor(request: Request) -> ActorModel:
    actor = _require_actor(request)
    if actor.actor_type != "admin":
        raise_api_error(status.HTTP_403_FORBIDDEN, "Admin actor required")
    return actor


def get_db_session(request: Request) -> Iterator[Session]:
    session = request.app.state.session_factory()
    actor = getattr(request.state, "actor", None)
    trace_id = request.headers.get("X-Trace-Id") or str(uuid.uuid4())
    request.state.trace_id = trace_id
    set_session_audit_context(
        session,
        actor_id=None if actor is None else actor.id,
        trace_id=trace_id,
    )
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_task_type_registry(request: Request) -> TaskTypeRegistry:
    return cast(TaskTypeRegistry, request.app.state.task_type_registry)


def create_app(
    *,
    session_factory: sessionmaker[Session] | None = None,
    task_type_registry: TaskTypeRegistry | None = None,
) -> FastAPI:
    """Create the FastAPI app with auth, CRUD, and task type routes."""
    app = FastAPI(
        title="AgenticQueue API",
        docs_url=None,
        redoc_url=None,
        openapi_url="/openapi.json",
    )
    app.state.session_factory = session_factory or _default_session_factory()
    app.state.task_type_registry = task_type_registry or _default_task_type_registry()
    app.add_middleware(IdempotencyKeyMiddleware)
    app.add_middleware(AgenticQueueAuthMiddleware)
    install_exception_handlers(app)
    app.include_router(build_crud_router(get_db_session))

    @app.get("/task-types", include_in_schema=False, response_model=list[TaskTypeView])
    @app.get("/v1/task-types", response_model=list[TaskTypeView])
    def list_task_types(request: Request) -> list[TaskTypeView]:
        _require_actor(request)
        registry = get_task_type_registry(request)
        return [_task_type_view(definition) for definition in registry.list()]

    @app.post(
        "/task-types",
        include_in_schema=False,
        response_model=TaskTypeView,
        status_code=status.HTTP_201_CREATED,
    )
    @app.post(
        "/v1/task-types",
        response_model=TaskTypeView,
        status_code=status.HTTP_201_CREATED,
    )
    def register_task_type(
        payload: RegisterTaskTypeRequest,
        request: Request,
    ) -> TaskTypeView:
        _require_admin_actor(request)
        registry = get_task_type_registry(request)
        try:
            definition = registry.register(
                name=payload.name,
                schema=payload.schema_document,
                policy=payload.policy,
            )
        except ValueError as error:
            raise_api_error(status.HTTP_400_BAD_REQUEST, str(error))
        return _task_type_view(definition)

    @app.get("/v1/auth/tokens", response_model=ApiTokenListResponse)
    def list_tokens(
        request: Request, session: Session = Depends(get_db_session)
    ) -> ApiTokenListResponse:
        actor = _require_actor(request)
        tokens = list_api_tokens_for_actor(session, actor.id)
        return ApiTokenListResponse(
            actor=_actor_summary(actor),
            tokens=[_token_view(token) for token in tokens],
        )

    @app.post(
        "/v1/auth/tokens",
        response_model=ProvisionApiTokenResponse,
        status_code=status.HTTP_201_CREATED,
    )
    def provision_token(
        payload: ProvisionApiTokenRequest,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> ProvisionApiTokenResponse:
        _require_admin_actor(request)
        actor_exists = session.get(ActorRecord, payload.actor_id)
        if actor_exists is None:
            raise_api_error(status.HTTP_404_NOT_FOUND, "Actor not found")

        api_token, raw_token = issue_api_token(
            session,
            actor_id=payload.actor_id,
            scopes=payload.scopes,
            expires_at=payload.expires_at,
        )
        return ProvisionApiTokenResponse(
            token=raw_token, api_token=_token_view(api_token)
        )

    @app.post("/v1/auth/tokens/{token_id}/revoke", response_model=ApiTokenView)
    def revoke_token(
        token_id: uuid.UUID,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> ApiTokenView:
        actor = _require_actor(request)
        existing = get_api_token(session, token_id)
        if existing is None:
            raise_api_error(status.HTTP_404_NOT_FOUND, "Token not found")
        if actor.actor_type != "admin" and existing.actor_id != actor.id:
            raise_api_error(status.HTTP_404_NOT_FOUND, "Token not found")

        revoked = revoke_api_token(session, token_id)
        if revoked is None:
            raise_api_error(status.HTTP_404_NOT_FOUND, "Token not found")
        return _token_view(revoked)

    @app.post(
        "/v1/capabilities/grant",
        response_model=CapabilityGrantView,
        status_code=status.HTTP_201_CREATED,
    )
    def grant_capability_endpoint(
        payload: GrantCapabilityRequest,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> CapabilityGrantView:
        admin_actor = _require_admin_actor(request)
        actor_exists = session.get(ActorRecord, payload.actor_id)
        if actor_exists is None:
            raise_api_error(status.HTTP_404_NOT_FOUND, "Actor not found")

        try:
            grant = grant_capability(
                session,
                actor_id=payload.actor_id,
                capability=payload.capability,
                scope=payload.scope,
                granted_by_actor_id=admin_actor.id,
                expires_at=payload.expires_at,
            )
        except ValueError as error:
            raise_api_error(status.HTTP_404_NOT_FOUND, str(error))
        return _capability_grant_view(grant)

    @app.post("/v1/capabilities/revoke", response_model=CapabilityGrantView)
    def revoke_capability_endpoint(
        payload: RevokeCapabilityRequest,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> CapabilityGrantView:
        _require_admin_actor(request)
        revoked_grant = revoke_capability_grant(session, payload.grant_id)
        if revoked_grant is None:
            raise_api_error(status.HTTP_404_NOT_FOUND, "Capability grant not found")
        return _capability_grant_view(revoked_grant)

    @app.get(
        "/v1/actors/{actor_id}/capabilities",
        response_model=ActorCapabilityListResponse,
    )
    def list_capability_grants(
        actor_id: uuid.UUID,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> ActorCapabilityListResponse:
        requesting_actor = _require_actor(request)
        if requesting_actor.actor_type != "admin" and requesting_actor.id != actor_id:
            raise_api_error(status.HTTP_403_FORBIDDEN, "Admin actor required")

        target_actor = session.get(ActorRecord, actor_id)
        if target_actor is None:
            raise_api_error(status.HTTP_404_NOT_FOUND, "Actor not found")

        grants = list_capabilities_for_actor(session, actor_id)
        return ActorCapabilityListResponse(
            actor=_actor_summary(ActorModel.model_validate(target_actor)),
            capabilities=[_capability_grant_view(grant) for grant in grants],
        )

    return app


app = create_app()
