"""FastAPI app for the AgenticQueue API surface."""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
import datetime as dt
import uuid
from collections.abc import AsyncIterator, Callable, Iterator, Sequence
from typing import Any, cast
from pathlib import Path

import sqlalchemy as sa
from fastapi import Body, Depends, FastAPI, Query, Request, Response, status
from pydantic import ConfigDict, Field
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.audit import (
    set_session_audit_context,
    set_session_redaction_context,
)
from agenticqueue_api.auth import (
    AgenticQueueAuthMiddleware,
    token_display_prefix,
)
from agenticqueue_api.capabilities import (
    ensure_actor_has_capability,
    require_capability,
)
from agenticqueue_api.config import (
    get_auto_setup_enabled,
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
from agenticqueue_api.errors import (
    install_exception_handlers,
    raise_api_error,
)
from agenticqueue_api.local_auth import (
    CSRF_COOKIE_NAME,
    SESSION_COOKIE_NAME,
    SESSION_MAX_AGE_SECONDS,
    authenticate_email_password,
    create_browser_session,
)
from agenticqueue_api.migrations import apply_database_migrations
from agenticqueue_api.middleware import (
    ActorRateLimitMiddleware,
    ContentSizeLimitMiddleware,
    IdempotencyKeyMiddleware,
    RequestIdMiddleware,
    REQUEST_ID_HEADER,
    SecretRedactionMiddleware,
)
from agenticqueue_api.models import (
    ActorModel,
    ApiTokenModel,
    AuditLogRecord,
    CapabilityGrantModel,
    CapabilityKey,
    DecisionRecord,
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
    LIMIT_HEADER,
    NEXT_CURSOR_HEADER,
    coerce_cursor_value,
    decode_cursor,
    encode_cursor,
)
from agenticqueue_api.routers import (
    build_analytics_router,
    build_auth_tokens_router,
    build_audit_router,
    build_bootstrap_router,
    build_decisions_router,
    build_graph_router,
    build_learnings_router,
    build_memory_router,
    build_operational_router,
    build_packets_router,
    build_rbac_router,
    build_task_types_router,
)
from agenticqueue_api.repo import claim_next, claim_task, release_claim
from agenticqueue_api.task_type_registry import TaskTypeDefinition, TaskTypeRegistry
from agenticqueue_api.task_actions import (
    TaskDecisionRequest,
    EscrowUnlockRequest,
    SubmitTaskResponse,
    approve_task,
    reject_task,
    submit_task,
    unlock_task_escrow,
)
from agenticqueue_api.task_retry import with_retry_fields
from agenticqueue_api.transitions import TaskState


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
    name: str
    token_prefix: str
    token_preview: str
    scopes: list[str]
    last_used_at: dt.datetime | None = None
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
    name: str = Field(default="api-token", min_length=1, max_length=120)
    scopes: list[str] = Field(default_factory=list)
    expires_at: dt.datetime | None = None


class ProvisionApiTokenResponse(SchemaModel):
    """Provisioning response including the raw token once."""

    token: str
    api_token: ApiTokenView


class BrowserTokenCreateRequest(SchemaModel):
    """Browser-session payload for creating an admin API token."""

    name: str = Field(min_length=1, max_length=120)


class BrowserTokenListResponse(SchemaModel):
    """Browser-session token list response."""

    tokens: list[ApiTokenView]


class BrowserTokenCreateResponse(ApiTokenView):
    """Browser-session create response with the raw token shown once."""

    token: str


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


class RotateOwnKeyRequest(SchemaModel):
    """Optional overrides when rotating the current actor token."""

    scopes: list[str] | None = None
    expires_at: dt.datetime | None = None


class LocalSessionRequest(SchemaModel):
    """Local email/password session creation payload."""

    email: str = Field(min_length=3, max_length=320, pattern=r"^[^@\s]+@[^@\s]+$")
    password: str = Field(min_length=1)


class LocalSessionUser(SchemaModel):
    """Local user fields returned after session creation."""

    email: str
    is_admin: bool


class LocalSessionResponse(SchemaModel):
    """Successful local browser session response."""

    user: LocalSessionUser


class TaskCommentRequest(SchemaModel):
    """Comment body attached to one task."""

    body: str


class TaskCommentResponse(SchemaModel):
    """Acknowledgement for one posted task comment."""

    job_id: uuid.UUID
    commented: bool


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
    token_prefix = token_display_prefix(token.token_hash)
    return ApiTokenView(
        id=token.id,
        actor_id=token.actor_id,
        name=token.name,
        token_prefix=token_prefix,
        token_preview=f"{token_prefix[:8]}...",
        scopes=token.scopes,
        last_used_at=token.last_used_at,
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


def _task_audit(
    session: Session,
    *,
    actor_id: uuid.UUID | None,
    task_id: uuid.UUID,
    action: str,
    after: dict[str, Any],
) -> None:
    session.execute(
        sa.insert(AuditLogRecord).values(
            actor_id=actor_id,
            entity_type="task",
            entity_id=task_id,
            action=action,
            before=None,
            after=after,
            trace_id=session.info.get("agenticqueue_audit_trace_id"),
            redaction=session.info.get("agenticqueue_audit_redaction"),
        )
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


def _require_api_token(request: Request) -> ApiTokenModel:
    api_token = getattr(request.state, "api_token", None)
    if not isinstance(api_token, ApiTokenModel):
        raise_api_error(status.HTTP_401_UNAUTHORIZED, "Invalid bearer token")
    return api_token


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


def _client_ip(request: Request) -> str | None:
    forwarded_for = request.headers.get("X-Forwarded-For")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    if request.client is None:
        return None
    return request.client.host


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
    session = getattr(request.state, "db_session", None)
    created_here = session is None
    if session is None:
        session = request.app.state.session_factory()
        request.state.db_session = session
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
        if created_here:
            session.commit()
    except Exception:
        if created_here:
            session.rollback()
        raise
    finally:
        if created_here:
            request.state.db_session = None
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

    def run_auto_migrations() -> None:
        apply_database_migrations()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if get_auto_setup_enabled():
            await asyncio.to_thread(run_auto_migrations)

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
    app.include_router(build_analytics_router(get_db_session))
    app.include_router(build_audit_router(get_db_session))
    app.include_router(build_decisions_router(get_db_session))
    app.include_router(build_graph_router(get_db_session))
    app.include_router(build_learnings_router(get_db_session))
    app.include_router(build_memory_router(get_db_session))
    app.include_router(build_operational_router(app, get_db_session))
    app.include_router(build_packets_router(get_db_session))
    app.include_router(build_crud_router(get_db_session))
    app.include_router(build_bootstrap_router(get_db_session))

    @app.post("/api/session", response_model=LocalSessionResponse)
    def create_local_session(
        payload: LocalSessionRequest,
        request: Request,
        response: Response,
        session: Session = Depends(get_db_session),
    ) -> LocalSessionResponse:
        user = authenticate_email_password(
            session,
            email=str(payload.email),
            password=payload.password,
        )
        if user is None:
            raise_api_error(
                status.HTTP_401_UNAUTHORIZED,
                "Invalid email or password",
                error_code="auth_failed",
            )

        session_token, csrf_token = create_browser_session(
            session,
            user=user,
            ip_address=_client_ip(request),
        )
        response.set_cookie(
            SESSION_COOKIE_NAME,
            session_token,
            max_age=SESSION_MAX_AGE_SECONDS,
            path="/",
            secure=True,
            httponly=True,
            samesite="lax",
        )
        response.set_cookie(
            CSRF_COOKIE_NAME,
            csrf_token,
            max_age=SESSION_MAX_AGE_SECONDS,
            path="/",
            secure=True,
            httponly=False,
            samesite="lax",
        )
        return LocalSessionResponse(
            user=LocalSessionUser(
                email=user.email,
                is_admin=user.is_admin,
            )
        )

    app.include_router(build_task_types_router(get_db_session))
    app.include_router(build_auth_tokens_router(get_db_session))
    app.include_router(build_rbac_router(get_db_session))

    @app.post(
        "/v1/tasks/claim",
        response_model=TaskModel,
    )
    def claim_next_task_endpoint(
        request: Request,
        project_id: uuid.UUID | None = Query(default=None),
        labels: list[str] | None = Query(default=None),
        claim_states: list[str] | None = Query(default=None),
        claimed_state: str = Query(default="claimed", min_length=1),
        session: Session = Depends(get_db_session),
    ) -> TaskModel:
        with write_timeout(session, endpoint="v1.tasks.claim_next"):
            actor = _require_actor(request)
            claimed = claim_next(
                session,
                actor_id=actor.id,
                labels=labels,
                project_id=project_id,
                claim_states=claim_states,
                claimed_state=claimed_state,
            )
            if claimed is None:
                raise_api_error(
                    status.HTTP_404_NOT_FOUND,
                    "No matching task found",
                    details={
                        "project_id": None if project_id is None else str(project_id),
                        "labels": labels or [],
                        "claim_states": claim_states or [],
                    },
                )
            refreshed = _task_record_or_404(session, claimed.id)
            return with_retry_fields(
                session,
                refreshed,
                task_type_registry=get_task_type_registry(request),
            )

    @app.post(
        "/tasks/{task_id}/claim",
        include_in_schema=False,
        response_model=TaskModel,
    )
    @app.post(
        "/v1/tasks/{task_id}/claim",
        response_model=TaskModel,
    )
    def claim_task_endpoint(
        task_id: uuid.UUID,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> TaskModel:
        with write_timeout(session, endpoint="v1.tasks.claim"):
            actor = _require_actor(request)
            task = session.get(TaskRecord, task_id)
            if task is None:
                raise_api_error(status.HTTP_404_NOT_FOUND, "Task not found")
            if task.state == TaskState.DLQ.value:
                raise_api_error(
                    status.HTTP_409_CONFLICT,
                    "Task is in the dead letter queue",
                    error_code="in_dlq",
                    details={"task_id": str(task_id), "state": task.state},
                )
            claimed = claim_task(
                session,
                task_id=task_id,
                actor_id=actor.id,
            )
            if claimed is None:
                raise_api_error(
                    status.HTTP_409_CONFLICT,
                    "Task is not claimable",
                    error_code="not_claimable",
                    details={"task_id": str(task_id), "state": task.state},
                )
            refreshed = session.get(TaskRecord, task_id)
            assert refreshed is not None
            return with_retry_fields(
                session,
                refreshed,
                task_type_registry=get_task_type_registry(request),
            )

    @app.post("/v1/tasks/{task_id}/release", response_model=TaskModel)
    def release_task_endpoint(
        task_id: uuid.UUID,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> TaskModel:
        with write_timeout(session, endpoint="v1.tasks.release"):
            actor = _require_actor(request)
            released = release_claim(
                session,
                task_id=task_id,
                expected_actor_id=(None if actor.actor_type == "admin" else actor.id),
            )
            if released is None:
                raise_api_error(
                    status.HTTP_404_NOT_FOUND,
                    "Task not found or not releasable",
                )
            refreshed = _task_record_or_404(session, task_id)
            return with_retry_fields(
                session,
                refreshed,
                task_type_registry=get_task_type_registry(request),
            )

    @app.post("/v1/tasks/{task_id}/reset", response_model=TaskModel)
    def reset_task_endpoint(
        task_id: uuid.UUID,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> TaskModel:
        with write_timeout(session, endpoint="v1.tasks.reset"):
            actor = _require_admin_actor(request)
            task = _task_record_or_404(session, task_id)
            task.state = TaskState.QUEUED.value
            task.attempt_count = 0
            task.last_failure = None
            task.claimed_by_actor_id = None
            task.claimed_at = None
            _task_audit(
                session,
                actor_id=actor.id,
                task_id=task.id,
                action="JOB_RESET",
                after={
                    "state": task.state,
                    "attempt_count": task.attempt_count,
                    "last_failure": task.last_failure,
                },
            )
            session.flush()
            session.refresh(task)
            return with_retry_fields(
                session,
                task,
                task_type_registry=get_task_type_registry(request),
            )

    @app.post("/v1/tasks/{task_id}/comments", response_model=TaskCommentResponse)
    def comment_on_task_endpoint(
        task_id: uuid.UUID,
        payload: TaskCommentRequest,
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> TaskCommentResponse:
        with write_timeout(session, endpoint="v1.tasks.comments"):
            actor = _require_actor(request)
            task = _task_record_or_404(session, task_id)
            _task_audit(
                session,
                actor_id=actor.id,
                task_id=task.id,
                action="JOB_COMMENTED",
                after={
                    "body": payload.body,
                    "commented_at": dt.datetime.now(dt.UTC).isoformat(),
                },
            )
            return TaskCommentResponse(job_id=task.id, commented=True)

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
        "/tasks/{task_id}/approve",
        include_in_schema=False,
        response_model=TaskModel,
    )
    @app.post(
        "/v1/tasks/{task_id}/approve",
        response_model=TaskModel,
    )
    def approve_task_endpoint(
        task_id: uuid.UUID,
        request: Request,
        payload: TaskDecisionRequest | None = Body(default=None),
        session: Session = Depends(get_db_session),
    ) -> TaskModel:
        with write_timeout(session, endpoint="v1.tasks.approve"):
            actor = _require_actor(request)
            return approve_task(
                session,
                task_id=task_id,
                actor=actor,
                task_type_registry=get_task_type_registry(request),
                reason=None if payload is None else payload.reason,
            )

    @app.post(
        "/tasks/{task_id}/reject",
        include_in_schema=False,
        response_model=TaskModel,
    )
    @app.post(
        "/v1/tasks/{task_id}/reject",
        response_model=TaskModel,
    )
    def reject_task_endpoint(
        task_id: uuid.UUID,
        request: Request,
        payload: TaskDecisionRequest | None = Body(default=None),
        session: Session = Depends(get_db_session),
    ) -> TaskModel:
        with write_timeout(session, endpoint="v1.tasks.reject"):
            actor = _require_actor(request)
            return reject_task(
                session,
                task_id=task_id,
                actor=actor,
                task_type_registry=get_task_type_registry(request),
                reason=None if payload is None else payload.reason,
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
