"""FastAPI app for the AgenticQueue API surface."""

from __future__ import annotations

from contextlib import AsyncExitStack, asynccontextmanager
import datetime as dt
import uuid
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from typing import Any, cast
from pathlib import Path

import sqlalchemy as sa
from fastapi import Body, Depends, FastAPI, Query, Request, Response, status
from pydantic import ConfigDict, Field, ValidationError
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.audit import (
    set_session_audit_context,
    set_session_redaction_context,
)
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
    require_capability,
    revoke_capability_grant,
)
from agenticqueue_api.config import (
    get_max_body_bytes,
    get_mcp_transports,
    get_policies_dir,
    get_repo_root,
    get_psycopg_connect_args,
    get_rate_limit_burst,
    get_rate_limit_rps,
    get_reload_enabled,
    get_sqlalchemy_sync_database_url,
    get_task_types_dir,
)
from agenticqueue_api.crud import build_crud_router
from agenticqueue_api.db import write_timeout
from agenticqueue_api.errors import install_exception_handlers, raise_api_error
from agenticqueue_api.middleware import (
    ActorRateLimitMiddleware,
    ContentSizeLimitMiddleware,
    IdempotencyKeyMiddleware,
    RequestIdMiddleware,
    REQUEST_ID_HEADER,
    SecretRedactionMiddleware,
)
from agenticqueue_api.learnings import (
    ConfirmLearningDraftRequest,
    ConfirmedDraftLearningView,
    DedupeSuggestion,
    DraftLearningPatch,
    DraftLearningRecord,
    DraftLearningView,
    DraftRejectRequest,
    DraftStore,
)
from agenticqueue_api.models import (
    ActorModel,
    ActorRecord,
    ApiTokenModel,
    CapabilityGrantModel,
    CapabilityKey,
    LearningRecord,
    RoleAssignmentModel,
    RoleModel,
    RoleName,
    TaskModel,
    TaskRecord,
)
from agenticqueue_api.models.shared import SchemaModel
from agenticqueue_api.packet_cache import PacketCache
from agenticqueue_api.pagination import (
    DEFAULT_LIST_LIMIT,
    LIMIT_HEADER,
    MAX_LIST_LIMIT,
    NEXT_CURSOR_HEADER,
    coerce_cursor_value,
    decode_cursor,
    encode_cursor,
)
from agenticqueue_api.routers import (
    build_learnings_router,
    build_memory_router,
    build_packets_router,
)
from agenticqueue_api.roles import (
    assign_role,
    list_role_assignments_for_actor,
    list_roles,
    revoke_role_assignment,
)
from agenticqueue_api.task_type_registry import TaskTypeDefinition, TaskTypeRegistry
from agenticqueue_api.task_actions import (
    EscrowUnlockRequest,
    SubmitTaskResponse,
    submit_task,
    unlock_task_escrow,
)


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


class RoleView(SchemaModel):
    """Seeded RBAC role exposed over REST."""

    id: uuid.UUID
    name: str
    description: str
    capabilities: list[CapabilityKey] = Field(default_factory=list)
    scope: dict[str, Any] = Field(default_factory=dict)
    created_at: dt.datetime
    updated_at: dt.datetime


class RoleListResponse(SchemaModel):
    """List of seeded roles."""

    roles: list[RoleView]


class RoleAssignmentView(SchemaModel):
    """One actor-role assignment returned from the API."""

    id: uuid.UUID
    actor_id: uuid.UUID
    role_id: uuid.UUID
    role_name: str
    description: str
    capabilities: list[CapabilityKey] = Field(default_factory=list)
    scope: dict[str, Any] = Field(default_factory=dict)
    granted_by_actor_id: uuid.UUID | None = None
    expires_at: dt.datetime | None = None
    revoked_at: dt.datetime | None = None
    created_at: dt.datetime
    updated_at: dt.datetime


class ActorRoleListResponse(SchemaModel):
    """Active role assignments for one actor."""

    actor: ActorSummary
    roles: list[RoleAssignmentView]


class AssignRoleRequest(SchemaModel):
    """Payload for assigning one role to an actor."""

    actor_id: uuid.UUID
    role_name: RoleName
    expires_at: dt.datetime | None = None


class RevokeRoleRequest(SchemaModel):
    """Payload for revoking one role assignment."""

    assignment_id: uuid.UUID


class AuditVerifyResponse(SchemaModel):
    """Verification result for the append-only audit ledger."""

    chain_length: int
    verified_count: int
    first_break_id_or_null: uuid.UUID | None = None


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
    engine = sa.create_engine(
        get_sqlalchemy_sync_database_url(),
        future=True,
        connect_args=get_psycopg_connect_args(),
    )
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


def _role_view(role: RoleModel) -> RoleView:
    return RoleView(
        id=role.id,
        name=role.name,
        description=role.description,
        capabilities=role.capabilities,
        scope=role.scope,
        created_at=role.created_at,
        updated_at=role.updated_at,
    )


def _role_assignment_view(assignment: RoleAssignmentModel) -> RoleAssignmentView:
    return RoleAssignmentView(
        id=assignment.id,
        actor_id=assignment.actor_id,
        role_id=assignment.role_id,
        role_name=assignment.role_name,
        description=assignment.description,
        capabilities=assignment.capabilities,
        scope=assignment.scope,
        granted_by_actor_id=assignment.granted_by_actor_id,
        expires_at=assignment.expires_at,
        revoked_at=assignment.revoked_at,
        created_at=assignment.created_at,
        updated_at=assignment.updated_at,
    )


def _task_type_view(definition: TaskTypeDefinition) -> TaskTypeView:
    return TaskTypeView(
        name=definition.name,
        schema=definition.schema,
        policy=definition.policy,
        schema_path=str(definition.schema_path),
        policy_path=str(definition.policy_path),
    )


def _paginate_sequence(
    values: Sequence[Any],
    *,
    response: Response,
    limit: int,
    cursor: str | None,
    key_types: list[type[Any]],
    key_fn: Callable[[Any], list[Any]],
) -> list[Any]:
    cursor_values = None
    if cursor is not None:
        raw_values = decode_cursor(cursor, expected_size=len(key_types))
        cursor_values = [
            coerce_cursor_value(raw_value, key_type)
            for raw_value, key_type in zip(raw_values, key_types)
        ]

    page = []
    for value in values:
        if cursor_values is not None and tuple(key_fn(value)) <= tuple(cursor_values):
            continue
        page.append(value)
        if len(page) > limit:
            break

    response.headers[LIMIT_HEADER] = str(limit)
    if len(page) > limit:
        response.headers[NEXT_CURSOR_HEADER] = encode_cursor(key_fn(page[limit - 1]))
        return page[:limit]
    return page


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


def _learning_draft_capability_scope(
    request: Request,
    session: Session,
    payload: dict[str, Any] | None,
    entity_id: uuid.UUID | None,
) -> dict[str, str]:
    del request, payload
    if entity_id is None:
        return {}
    project_id = session.scalar(
        sa.select(TaskRecord.project_id)
        .join(
            DraftLearningRecord,
            DraftLearningRecord.task_id == TaskRecord.id,
        )
        .where(DraftLearningRecord.id == entity_id)
    )
    return {} if project_id is None else {"project_id": str(project_id)}


_require_learning_draft_write_capability = require_capability(
    CapabilityKey.WRITE_LEARNING,
    _learning_draft_capability_scope,
    entity_type="learning",
)


def _learning_promotion_capability_scope(
    request: Request,
    session: Session,
    payload: dict[str, Any] | None,
    entity_id: uuid.UUID | None,
) -> dict[str, str]:
    del request, payload
    if entity_id is None:
        return {}
    project_id = session.scalar(
        sa.select(TaskRecord.project_id)
        .join(LearningRecord, LearningRecord.task_id == TaskRecord.id)
        .where(LearningRecord.id == entity_id)
    )
    return {} if project_id is None else {"project_id": str(project_id)}


_require_learning_promotion_capability = require_capability(
    CapabilityKey.PROMOTE_LEARNING,
    _learning_promotion_capability_scope,
    entity_type="learning",
)


def get_db_session(request: Request) -> Iterator[Session]:
    session = request.app.state.session_factory()
    actor = getattr(request.state, "actor", None)
    request_id = (
        getattr(request.state, "request_id", None)
        or request.headers.get(REQUEST_ID_HEADER)
        or request.headers.get("X-Trace-Id")
        or str(uuid.uuid4())
    )
    request.state.request_id = request_id
    redaction = getattr(request.state, "secret_redaction_context", None)
    request.state.trace_id = request_id
    set_session_audit_context(
        session,
        actor_id=None if actor is None else actor.id,
        trace_id=request_id,
    )
    set_session_redaction_context(session, redaction=redaction)
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
    policies_dir: Path | None = None,
    artifact_root: Path | None = None,
) -> FastAPI:
    """Create the FastAPI app with auth, CRUD, and task type routes."""
    resolved_session_factory = session_factory or _default_session_factory()
    packet_cache = PacketCache(session_factory=resolved_session_factory)
    mcp_lifespan_apps: list[Any] = []

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        del app
        packet_cache.start()
        async with AsyncExitStack() as stack:
            for mounted_app in mcp_lifespan_apps:
                await stack.enter_async_context(
                    mounted_app.router.lifespan_context(mounted_app)
                )
            try:
                yield
            finally:
                packet_cache.close()

    app = FastAPI(
        title="AgenticQueue API",
        summary="REST coordination surface for AgenticQueue.",
        description=(
            "Typed task, memory, packet, and governance APIs for the "
            "AgenticQueue coordination plane."
        ),
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
        lifespan=lifespan,
    )
    app.state.session_factory = resolved_session_factory
    app.state.packet_cache = packet_cache
    app.state.task_type_registry = task_type_registry or _default_task_type_registry()
    app.state.artifact_root = artifact_root or get_repo_root()
    app.add_middleware(IdempotencyKeyMiddleware)
    app.add_middleware(
        SecretRedactionMiddleware,
        policy_directory=policies_dir or get_policies_dir(),
    )
    app.add_middleware(
        ActorRateLimitMiddleware,
        rate_per_second=get_rate_limit_rps(),
        burst_size=get_rate_limit_burst(),
    )
    app.add_middleware(AgenticQueueAuthMiddleware)
    app.add_middleware(
        ContentSizeLimitMiddleware,
        default_limit=get_max_body_bytes(),
    )
    app.add_middleware(RequestIdMiddleware)
    install_exception_handlers(app)
    app.include_router(build_learnings_router(get_db_session))
    app.include_router(build_memory_router(get_db_session))
    app.include_router(build_packets_router(get_db_session))
    app.include_router(build_crud_router(get_db_session))

    @app.get(
        "/audit/verify",
        include_in_schema=False,
        response_model=AuditVerifyResponse,
    )
    @app.get("/v1/audit/verify", response_model=AuditVerifyResponse)
    def verify_audit_log_chain(
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> AuditVerifyResponse:
        _require_admin_actor(request)
        query = sa.text("""
            SELECT
              chain_length,
              verified_count,
              first_break_id_or_null
            FROM agenticqueue.verify_audit_log_chain()
            """)
        report = session.execute(query).mappings().one()
        return AuditVerifyResponse.model_validate(dict(report))

    @app.get("/task-types", include_in_schema=False, response_model=list[TaskTypeView])
    @app.get("/v1/task-types", response_model=list[TaskTypeView])
    def list_task_types(
        request: Request,
        response: Response,
        limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
        cursor: str | None = Query(default=None),
    ) -> list[TaskTypeView]:
        _require_actor(request)
        registry = get_task_type_registry(request)
        definitions = sorted(registry.list(), key=lambda definition: definition.name)
        page = _paginate_sequence(
            definitions,
            response=response,
            limit=limit,
            cursor=cursor,
            key_types=[str],
            key_fn=lambda definition: [definition.name],
        )
        return [_task_type_view(definition) for definition in page]

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
        request: Request,
        response: Response,
        limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
        cursor: str | None = Query(default=None),
        session: Session = Depends(get_db_session),
    ) -> ApiTokenListResponse:
        actor = _require_actor(request)
        tokens = list_api_tokens_for_actor(session, actor.id)
        page = _paginate_sequence(
            tokens,
            response=response,
            limit=limit,
            cursor=cursor,
            key_types=[str, str],
            key_fn=lambda token: [token.created_at.isoformat(), str(token.id)],
        )
        return ApiTokenListResponse(
            actor=_actor_summary(actor),
            tokens=[_token_view(token) for token in page],
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
        with write_timeout(session, endpoint="v1.auth.tokens.provision"):
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
                token=raw_token,
                api_token=_token_view(api_token),
            )

    @app.post("/v1/auth/tokens/{token_id}/revoke", response_model=ApiTokenView)
    def revoke_token(
        token_id: uuid.UUID,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> ApiTokenView:
        with write_timeout(session, endpoint="v1.auth.tokens.revoke"):
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
        with write_timeout(session, endpoint="v1.capabilities.grant"):
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
        with write_timeout(session, endpoint="v1.capabilities.revoke"):
            _require_admin_actor(request)
            revoked_grant = revoke_capability_grant(session, payload.grant_id)
            if revoked_grant is None:
                raise_api_error(
                    status.HTTP_404_NOT_FOUND,
                    "Capability grant not found",
                )
            return _capability_grant_view(revoked_grant)

    @app.get(
        "/v1/actors/{actor_id}/capabilities",
        response_model=ActorCapabilityListResponse,
    )
    def list_capability_grants(
        actor_id: uuid.UUID,
        request: Request,
        response: Response,
        limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
        cursor: str | None = Query(default=None),
        session: Session = Depends(get_db_session),
    ) -> ActorCapabilityListResponse:
        requesting_actor = _require_actor(request)
        if requesting_actor.actor_type != "admin" and requesting_actor.id != actor_id:
            raise_api_error(status.HTTP_403_FORBIDDEN, "Admin actor required")

        target_actor = session.get(ActorRecord, actor_id)
        if target_actor is None:
            raise_api_error(status.HTTP_404_NOT_FOUND, "Actor not found")

        grants = list_capabilities_for_actor(session, actor_id)
        page = _paginate_sequence(
            grants,
            response=response,
            limit=limit,
            cursor=cursor,
            key_types=[str, str],
            key_fn=lambda grant: [grant.created_at.isoformat(), str(grant.id)],
        )
        return ActorCapabilityListResponse(
            actor=_actor_summary(ActorModel.model_validate(target_actor)),
            capabilities=[_capability_grant_view(grant) for grant in page],
        )

    @app.get("/v1/roles", response_model=RoleListResponse)
    def list_roles_endpoint(
        request: Request,
        response: Response,
        limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
        cursor: str | None = Query(default=None),
        session: Session = Depends(get_db_session),
    ) -> RoleListResponse:
        _require_admin_actor(request)
        roles = sorted(
            list_roles(session),
            key=lambda role: (role.created_at.isoformat(), str(role.id)),
        )
        page = _paginate_sequence(
            roles,
            response=response,
            limit=limit,
            cursor=cursor,
            key_types=[str, str],
            key_fn=lambda role: [role.created_at.isoformat(), str(role.id)],
        )
        return RoleListResponse(roles=[_role_view(role) for role in page])

    @app.post(
        "/v1/roles/assign",
        response_model=RoleAssignmentView,
        status_code=status.HTTP_201_CREATED,
    )
    def assign_role_endpoint(
        payload: AssignRoleRequest,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> RoleAssignmentView:
        with write_timeout(session, endpoint="v1.roles.assign"):
            admin_actor = _require_admin_actor(request)
            actor_exists = session.get(ActorRecord, payload.actor_id)
            if actor_exists is None:
                raise_api_error(status.HTTP_404_NOT_FOUND, "Actor not found")

            try:
                assignment = assign_role(
                    session,
                    actor_id=payload.actor_id,
                    role_name=payload.role_name,
                    granted_by_actor_id=admin_actor.id,
                    expires_at=payload.expires_at,
                )
            except ValueError as error:
                raise_api_error(status.HTTP_404_NOT_FOUND, str(error))
            return _role_assignment_view(assignment)

    @app.post("/v1/roles/revoke", response_model=RoleAssignmentView)
    def revoke_role_endpoint(
        payload: RevokeRoleRequest,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> RoleAssignmentView:
        with write_timeout(session, endpoint="v1.roles.revoke"):
            _require_admin_actor(request)
            assignment = revoke_role_assignment(session, payload.assignment_id)
            if assignment is None:
                raise_api_error(status.HTTP_404_NOT_FOUND, "Role assignment not found")
            return _role_assignment_view(assignment)

    @app.get("/v1/actors/{actor_id}/roles", response_model=ActorRoleListResponse)
    def list_actor_roles(
        actor_id: uuid.UUID,
        request: Request,
        response: Response,
        limit: int = Query(default=DEFAULT_LIST_LIMIT, ge=1, le=MAX_LIST_LIMIT),
        cursor: str | None = Query(default=None),
        session: Session = Depends(get_db_session),
    ) -> ActorRoleListResponse:
        requesting_actor = _require_actor(request)
        if requesting_actor.actor_type != "admin" and requesting_actor.id != actor_id:
            raise_api_error(status.HTTP_403_FORBIDDEN, "Admin actor required")

        target_actor = session.get(ActorRecord, actor_id)
        if target_actor is None:
            raise_api_error(status.HTTP_404_NOT_FOUND, "Actor not found")

        assignments = list_role_assignments_for_actor(session, actor_id)
        page = _paginate_sequence(
            assignments,
            response=response,
            limit=limit,
            cursor=cursor,
            key_types=[str, str],
            key_fn=lambda assignment: [
                assignment.created_at.isoformat(),
                str(assignment.id),
            ],
        )
        return ActorRoleListResponse(
            actor=_actor_summary(ActorModel.model_validate(target_actor)),
            roles=[_role_assignment_view(assignment) for assignment in page],
        )

    def _draft_store_or_error(
        session: Session,
        draft_id: uuid.UUID,
    ) -> DraftStore:
        store = DraftStore(session)
        try:
            draft = store.get(draft_id)
        except ValidationError as error:
            raise_api_error(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "Learning draft payload is invalid",
                details=error.errors(),
            )
        if draft is None:
            raise_api_error(status.HTTP_404_NOT_FOUND, "Learning draft not found")
        return store

    @app.post(
        "/learnings/drafts/{draft_id}/edit",
        include_in_schema=False,
        response_model=DraftLearningView,
    )
    @app.post(
        "/v1/learnings/drafts/{draft_id}/edit",
        response_model=DraftLearningView,
    )
    def edit_learning_draft(
        draft_id: uuid.UUID,
        payload: DraftLearningPatch,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> DraftLearningView:
        actor = _require_actor(request)
        _require_token_scope(request, "learning:write")
        store = _draft_store_or_error(session, draft_id)
        with write_timeout(session, endpoint="v1.learnings.drafts.edit"):
            _require_learning_draft_write_capability(
                request=request,
                session=session,
                entity_id=draft_id,
            )
            try:
                return store.edit(draft_id, payload)
            except ValidationError as error:
                raise_api_error(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "Learning draft payload is invalid",
                    details=error.errors(),
                )
            except KeyError:
                raise_api_error(
                    status.HTTP_404_NOT_FOUND,
                    "Learning draft not found",
                )
            except ValueError as error:
                raise_api_error(
                    status.HTTP_409_CONFLICT,
                    str(error),
                    details={"draft_id": str(draft_id), "actor_id": str(actor.id)},
                )

    @app.post(
        "/learnings/drafts/{draft_id}/reject",
        include_in_schema=False,
        response_model=DraftLearningView,
    )
    @app.post(
        "/v1/learnings/drafts/{draft_id}/reject",
        response_model=DraftLearningView,
    )
    def reject_learning_draft(
        draft_id: uuid.UUID,
        payload: DraftRejectRequest,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> DraftLearningView:
        actor = _require_actor(request)
        _require_token_scope(request, "learning:write")
        store = _draft_store_or_error(session, draft_id)
        with write_timeout(session, endpoint="v1.learnings.drafts.reject"):
            _require_learning_draft_write_capability(
                request=request,
                session=session,
                entity_id=draft_id,
            )
            try:
                return store.reject(draft_id, reason=payload.reason)
            except KeyError:
                raise_api_error(
                    status.HTTP_404_NOT_FOUND,
                    "Learning draft not found",
                )
            except ValueError as error:
                raise_api_error(
                    status.HTTP_409_CONFLICT,
                    str(error),
                    details={"draft_id": str(draft_id), "actor_id": str(actor.id)},
                )

    @app.post(
        "/learnings/drafts/{draft_id}/confirm",
        include_in_schema=False,
        response_model=ConfirmedDraftLearningView | DedupeSuggestion,
    )
    @app.post(
        "/v1/learnings/drafts/{draft_id}/confirm",
        response_model=ConfirmedDraftLearningView | DedupeSuggestion,
    )
    def confirm_learning_draft(
        draft_id: uuid.UUID,
        request: Request,
        session: Session = Depends(get_db_session),
        payload: ConfirmLearningDraftRequest | None = Body(default=None),
    ) -> ConfirmedDraftLearningView | DedupeSuggestion:
        actor = _require_actor(request)
        _require_token_scope(request, "learning:write")
        store = _draft_store_or_error(session, draft_id)
        with write_timeout(session, endpoint="v1.learnings.drafts.confirm"):
            _require_learning_draft_write_capability(
                request=request,
                session=session,
                entity_id=draft_id,
            )
            try:
                return store.confirm(
                    draft_id,
                    owner_actor_id=actor.id,
                    request=payload,
                )
            except ValidationError as error:
                raise_api_error(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    "Learning draft payload is invalid",
                    details=error.errors(),
                )
            except KeyError:
                raise_api_error(
                    status.HTTP_404_NOT_FOUND,
                    "Learning draft not found",
                )
            except ValueError as error:
                raise_api_error(
                    status.HTTP_409_CONFLICT,
                    str(error),
                    details={"draft_id": str(draft_id), "actor_id": str(actor.id)},
                )

    @app.post(
        "/tasks/{task_id}/submit",
        include_in_schema=False,
        response_model=SubmitTaskResponse,
    )
    @app.post(
        "/v1/tasks/{task_id}/submit",
        response_model=SubmitTaskResponse,
    )
    def submit_task_endpoint(
        task_id: uuid.UUID,
        payload: dict[str, Any],
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> SubmitTaskResponse:
        with write_timeout(session, endpoint="v1.tasks.submit"):
            actor = _require_actor(request)
            return submit_task(
                session,
                task_id=task_id,
                actor=actor,
                submission=payload,
                task_type_registry=get_task_type_registry(request),
                artifact_root=cast(Path, request.app.state.artifact_root),
                packet_cache=request.app.state.packet_cache,
            )

    @app.post(
        "/tasks/{task_id}/escrow-unlock",
        include_in_schema=False,
        response_model=TaskModel,
    )
    @app.post(
        "/v1/tasks/{task_id}/escrow-unlock",
        response_model=TaskModel,
    )
    def force_unlock_task_escrow(
        task_id: uuid.UUID,
        request: Request,
        payload: EscrowUnlockRequest | None = Body(default=None),
        session: Session = Depends(get_db_session),
    ) -> TaskModel:
        with write_timeout(session, endpoint="v1.tasks.escrow_unlock"):
            actor = _require_admin_actor(request)
            return unlock_task_escrow(
                session,
                task_id=task_id,
                actor=actor,
                reason=None if payload is None else payload.reason,
            )

    from agenticqueue_api.mcp.server import build_agenticqueue_mcp

    mcp_server = build_agenticqueue_mcp(
        app=app,
        session_factory=resolved_session_factory,
        task_type_registry=cast(TaskTypeRegistry, app.state.task_type_registry),
    )
    app.state.mcp_server = mcp_server
    transports = set(get_mcp_transports())
    if "sse" in transports:
        mcp_sse_app = mcp_server.http_app(path="/", transport="sse")
        mcp_lifespan_apps.append(mcp_sse_app)
        app.mount(
            "/mcp/sse",
            mcp_sse_app,
        )
    if "http" in transports:
        mcp_http_app = mcp_server.http_app(path="/", transport="streamable-http")
        mcp_lifespan_apps.append(mcp_http_app)
        app.mount(
            "/mcp",
            mcp_http_app,
        )

    return app


app = create_app()
