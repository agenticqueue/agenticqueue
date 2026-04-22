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
    ensure_actor_has_capability,
    grant_capability,
    list_capabilities_for_actor,
    require_capability,
    revoke_capability_grant,
)
from agenticqueue_api.config import (
    get_mcp_http_port,
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
    HTTP_422_STATUS,
    install_exception_handlers,
    raise_api_error,
)
from agenticqueue_api.init_wizard import (
    BOOT_TRACE_ID,
    SETUP_ROUTE_TRACE_ID,
    InitWizardResult,
    apply_database_migrations,
    emit_bootstrap_message,
    run_first_run_setup,
)
from agenticqueue_api.middleware import (
    ActorRateLimitMiddleware,
    ContentSizeLimitMiddleware,
    IdempotencyKeyMiddleware,
    RequestIdMiddleware,
    REQUEST_ID_HEADER,
    SecretRedactionMiddleware,
)
from agenticqueue_api.middleware.idempotency import get_idempotency_stats
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
    AuditLogRecord,
    CapabilityGrantModel,
    CapabilityKey,
    DecisionRecord,
    EdgeModel,
    EdgeRelation,
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
    build_analytics_router,
    build_audit_router,
    build_graph_router,
    build_learnings_router,
    build_memory_router,
    build_packets_router,
)
from agenticqueue_api.repo import claim_next, claim_task, create_edge, release_claim
from agenticqueue_api.roles import (
    assign_role,
    list_role_assignments_for_actor,
    list_roles,
    revoke_role_assignment,
)
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


class UpdateTaskTypeRequest(SchemaModel):
    """Payload for replacing one task type definition."""

    model_config = ConfigDict(populate_by_name=True)

    schema_document: dict[str, Any] = Field(alias="schema")
    policy: dict[str, Any] = Field(default_factory=dict)


class RotateOwnKeyRequest(SchemaModel):
    """Optional overrides when rotating the current actor token."""

    scopes: list[str] | None = None
    expires_at: dt.datetime | None = None


class TaskCommentRequest(SchemaModel):
    """Comment body attached to one task."""

    body: str


class TaskCommentResponse(SchemaModel):
    """Acknowledgement for one posted task comment."""

    job_id: uuid.UUID
    commented: bool


class DecisionSupersedeRequest(SchemaModel):
    """Payload linking a replacement decision to the superseded one."""

    replaced_by: uuid.UUID


class DecisionLinkRequest(SchemaModel):
    """Payload linking one decision to one job/task."""

    job_id: uuid.UUID
    relation: EdgeRelation = EdgeRelation.INFORMED_BY


class IdempotencyStatsResponse(SchemaModel):
    """Current idempotency cache counters."""

    hit_count: int
    row_count: int
    expired_count: int
    active_count: int


class PacketCacheStatsResponse(SchemaModel):
    """Current compiled-packet cache counters."""

    enabled: bool
    hits: int | None = None
    misses: int | None = None
    hit_rate: float | None = None
    miss_reasons: dict[str, int] = Field(default_factory=dict)
    invalidations: int | None = None
    listener_error: str | None = None


class McpStatsResponse(SchemaModel):
    """Current MCP transport statistics."""

    tool_count: int | None = None
    transports: list[str] = Field(default_factory=list)
    http_port: int | None = None


class StatsResponse(SchemaModel):
    """System stats exposed over the REST surface."""

    idempotency: IdempotencyStatsResponse
    packet_cache: PacketCacheStatsResponse
    mcp: McpStatsResponse


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


def _packet_cache_stats_response(app: FastAPI) -> PacketCacheStatsResponse:
    packet_cache = getattr(app.state, "packet_cache", None)
    if packet_cache is None:
        return PacketCacheStatsResponse(enabled=False)

    stats = packet_cache.stats()
    return PacketCacheStatsResponse(
        enabled=True,
        hits=stats.hits,
        misses=stats.misses,
        hit_rate=stats.hit_rate,
        miss_reasons=stats.miss_reasons,
        invalidations=stats.invalidations,
        listener_error=(
            None
            if packet_cache.listener_error is None
            else str(packet_cache.listener_error)
        ),
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

    def run_auto_setup() -> None:
        apply_database_migrations()
        setup_session = resolved_session_factory()
        try:
            set_session_audit_context(
                setup_session,
                actor_id=None,
                trace_id=BOOT_TRACE_ID,
            )
            bootstrap = run_first_run_setup(setup_session)
            setup_session.commit()
            emit_bootstrap_message(bootstrap)
        except Exception:
            setup_session.rollback()
            raise
        finally:
            setup_session.close()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if get_auto_setup_enabled():
            await asyncio.to_thread(run_auto_setup)

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
    app.include_router(build_graph_router(get_db_session))
    app.include_router(build_learnings_router(get_db_session))
    app.include_router(build_memory_router(get_db_session))
    app.include_router(build_packets_router(get_db_session))
    app.include_router(build_crud_router(get_db_session))

    @app.get("/healthz")
    @app.get("/health", include_in_schema=False)
    @app.get("/v1/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "version": app.version,
        }

    @app.get("/stats", response_model=StatsResponse)
    def stats(
        request: Request,
        session: Session = Depends(get_db_session),
    ) -> StatsResponse:
        _require_actor(request)
        idempotency = get_idempotency_stats(session)
        mcp_server = getattr(app.state, "mcp_server", None)
        registered_tools = (
            None
            if mcp_server is None
            else getattr(mcp_server, "agenticqueue_registered_tools", None)
        )
        return StatsResponse(
            idempotency=IdempotencyStatsResponse(
                hit_count=idempotency.hit_count,
                row_count=idempotency.row_count,
                expired_count=idempotency.expired_count,
                active_count=idempotency.active_count,
            ),
            packet_cache=_packet_cache_stats_response(app),
            mcp=McpStatsResponse(
                tool_count=(
                    None if registered_tools is None else len(registered_tools)
                ),
                transports=list(get_mcp_transports()),
                http_port=get_mcp_http_port(),
            ),
        )

    @app.post(
        "/setup",
        response_model=InitWizardResult,
        status_code=status.HTTP_201_CREATED,
    )
    def setup(request: Request) -> InitWizardResult:
        apply_database_migrations()
        session = request.app.state.session_factory()
        trace_id = getattr(request.state, "request_id", None) or SETUP_ROUTE_TRACE_ID
        try:
            set_session_audit_context(
                session,
                actor_id=None,
                trace_id=trace_id,
            )
            result = run_first_run_setup(session)
            if result.status == "noop":
                session.rollback()
                raise_api_error(
                    status.HTTP_409_CONFLICT,
                    "First-run setup already completed",
                    details={"workspace_id": str(result.workspace_id)},
                )
            session.commit()
            return result
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

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

    @app.get(
        "/task-types/{task_type_name}",
        include_in_schema=False,
        response_model=TaskTypeView,
    )
    @app.get("/v1/task-types/{task_type_name}", response_model=TaskTypeView)
    def get_task_type_endpoint(
        task_type_name: str,
        request: Request,
    ) -> TaskTypeView:
        _require_actor(request)
        registry = get_task_type_registry(request)
        try:
            definition = registry.get(task_type_name)
        except ValueError:
            raise_api_error(status.HTTP_404_NOT_FOUND, "Task type not found")
        return _task_type_view(definition)

    _update_task_type_capability = require_capability(
        CapabilityKey.UPDATE_TASK,
        entity_type="task",
    )

    def _update_task_type_route_capability(
        request: Request,
        payload: dict[str, Any] | None = Body(default=None),
        session: Session = Depends(get_db_session),
    ) -> None:
        _update_task_type_capability(
            request=request,
            session=session,
            payload=payload,
            entity_id=None,
        )

    _update_task_type_route_capability.__name__ = _update_task_type_capability.__name__

    @app.patch(
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
        registry = get_task_type_registry(request)
        try:
            definition = registry.register(
                name=task_type_name,
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

    @app.post(
        "/v1/actors/me/rotate-key",
        response_model=ProvisionApiTokenResponse,
    )
    def rotate_own_key_endpoint(
        request: Request,
        session: Session = Depends(get_db_session),
        payload: RotateOwnKeyRequest | None = Body(default=None),
    ) -> ProvisionApiTokenResponse:
        with write_timeout(session, endpoint="v1.actors.me.rotate_key"):
            actor = _require_actor(request)
            current_api_token = _require_api_token(request)
            revoke_api_token(session, current_api_token.id)
            api_token, raw_token = issue_api_token(
                session,
                actor_id=actor.id,
                scopes=(
                    current_api_token.scopes
                    if payload is None or payload.scopes is None
                    else payload.scopes
                ),
                expires_at=None if payload is None else payload.expires_at,
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

    @app.post(
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

    @app.post(
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

    def _draft_store_or_error(
        session: Session,
        draft_id: uuid.UUID,
    ) -> DraftStore:
        store = DraftStore(session)
        try:
            draft = store.get(draft_id)
        except ValidationError as error:
            raise_api_error(
                HTTP_422_STATUS,
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
                    HTTP_422_STATUS,
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
                    HTTP_422_STATUS,
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
