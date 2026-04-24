"""Shared helpers for AgenticQueue MCP surfaces."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from enum import StrEnum
import os
from pathlib import Path
import re
import threading
import uuid
from typing import Any, Callable

import httpx
import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.audit import set_session_audit_context
from agenticqueue_api.auth import AuthenticatedRequest, authenticate_api_token
from agenticqueue_api.config import (
    get_psycopg_connect_args,
    get_repo_root,
    get_sqlalchemy_sync_database_url,
)
from agenticqueue_api.errors import error_payload


@dataclass(frozen=True)
class McpSurfaceError(Exception):
    """Structured error raised by MCP tool helpers."""

    status_code: int
    payload: dict[str, Any]


def surface_error(
    status_code: int,
    message: str,
    *,
    error_code: str | None = None,
    details: Any = None,
) -> McpSurfaceError:
    """Build one structured MCP error."""

    return McpSurfaceError(
        status_code=status_code,
        payload=error_payload(
            status_code=status_code,
            message=message,
            error_code=error_code,
            details=details,
        ),
    )


def default_session_factory() -> sessionmaker[Session]:
    """Return the default sync SQLAlchemy session factory."""

    engine = sa.create_engine(
        get_sqlalchemy_sync_database_url(),
        future=True,
        connect_args=get_psycopg_connect_args(),
    )
    return sessionmaker(bind=engine, expire_on_commit=False)


def authenticate_surface_token(
    session: Session,
    *,
    token: str | None,
    trace_name: str,
) -> AuthenticatedRequest:
    """Authenticate a bearer token supplied as a tool argument."""

    if token is None or not token.strip():
        raise surface_error(401, "Missing Authorization header")

    authenticated = authenticate_api_token(session, token.strip())
    if authenticated is None:
        raise surface_error(401, "Invalid bearer token")

    set_session_audit_context(
        session,
        actor_id=authenticated.actor.id,
        trace_id=f"aq-mcp-{trace_name}-{uuid.uuid4()}",
    )
    return authenticated


def run_session_tool(
    session_factory: sessionmaker[Session],
    *,
    token: str | None,
    trace_name: str,
    callback: Callable[[Session, AuthenticatedRequest], Any],
    mutation: bool = False,
) -> dict[str, Any]:
    """Run one tool callback inside a managed DB session."""

    with session_factory() as session:
        try:
            authenticated = authenticate_surface_token(
                session,
                token=token,
                trace_name=trace_name,
            )
            result = callback(session, authenticated)
            if mutation:
                session.commit()
            return serialize_model(result)
        except McpSurfaceError as error:
            if session.in_transaction():
                session.rollback()
            return error.payload
        except Exception:
            if session.in_transaction():
                session.rollback()
            raise


def serialize_model(value: Any) -> Any:
    """Serialize pydantic models recursively."""

    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, list):
        return [serialize_model(item) for item in value]
    if isinstance(value, tuple):
        return [serialize_model(item) for item in value]
    if isinstance(value, dict):
        return {key: serialize_model(item) for key, item in value.items()}
    return value


def call_internal_api(
    app: Any,
    *,
    method: str,
    path: str,
    token: str | None = None,
    headers: dict[str, str] | None = None,
    params: dict[str, Any] | None = None,
    json_body: Any = None,
) -> dict[str, Any]:
    """Call the in-process FastAPI app and normalize JSON responses."""

    async def _call() -> dict[str, Any]:
        resolved_headers = {} if headers is None else dict(headers)
        if token is not None and token.strip():
            resolved_headers["Authorization"] = f"Bearer {token.strip()}"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://agenticqueue.local",
        ) as client:
            response = await client.request(
                method=method,
                url=path,
                headers=resolved_headers,
                params=params,
                json=json_body,
            )
        if not response.content:
            return {"ok": True, "status_code": response.status_code}
        payload = response.json()
        if response.status_code < 400:
            return payload
        if isinstance(payload, dict) and isinstance(payload.get("detail"), dict):
            return payload["detail"]
        if isinstance(payload, dict):
            return payload
        return error_payload(
            status_code=response.status_code,
            message="Request failed",
            details=payload,
        )

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_call())

    result: dict[str, dict[str, Any]] = {}
    error: dict[str, BaseException] = {}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(_call())
        except BaseException as exc:  # pragma: no cover - exercised via caller paths
            error["value"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()

    if "value" in error:
        raise error["value"]
    return result["value"]


def surface_plan_path() -> Path:
    """Return the canonical public-surface spec path."""

    configured = os.getenv("AGENTICQUEUE_SURFACE_PLAN_PATH") or os.getenv(
        "SURFACE_PLAN_PATH"
    )
    if configured:
        return Path(configured)

    repo_root = get_repo_root()
    candidate = repo_root / "docs" / "surface-1.0.md"
    if candidate.is_file():
        return candidate
    raise FileNotFoundError(
        "Unable to locate surface-1.0.md in the public repo docs/ directory."
    )


def canonical_surface_tool_names() -> list[str]:
    """Parse the canonical MCP tool names from `surface-1.0.md`."""

    operation_row = re.compile(r"^\d+\.\d+")
    tool_names: list[str] = []
    for line in surface_plan_path().read_text(encoding="utf-8").splitlines():
        if not line.startswith("|"):
            continue
        parts = [part.strip() for part in line.strip("|").split("|")]
        if len(parts) != 7 or not operation_row.match(parts[0]):
            continue
        tool_name = parts[4].strip("` ")
        if tool_name in {"MCP tool", "n/a", "—", "---", ""}:
            continue
        if tool_name not in tool_names:
            tool_names.append(tool_name)
    return tool_names


class McpToolProfile(StrEnum):
    """Discoverability profiles for unified MCP tool listings."""

    WORKER = "worker"
    REVIEWER = "reviewer"
    SUPERVISOR = "supervisor"
    ADMIN = "admin"


def tool_visibility_profiles() -> dict[str, frozenset[McpToolProfile]]:
    """Return the AQ-228 profile visibility map for canonical MCP tools."""

    all_profiles = frozenset(McpToolProfile)
    worker_execution_profiles = frozenset(
        {
            McpToolProfile.WORKER,
            McpToolProfile.SUPERVISOR,
            McpToolProfile.ADMIN,
        }
    )
    reviewer_profiles = frozenset(
        {
            McpToolProfile.REVIEWER,
            McpToolProfile.SUPERVISOR,
            McpToolProfile.ADMIN,
        }
    )
    supervisor_profiles = frozenset(
        {
            McpToolProfile.SUPERVISOR,
            McpToolProfile.ADMIN,
        }
    )
    admin_profiles = frozenset({McpToolProfile.ADMIN})

    visibility: dict[str, frozenset[McpToolProfile]] = {
        name: all_profiles
        for name in (
            "attach_artifact",
            "comment_on_job",
            "compile_packet",
            "create_decision",
            "get_artifact",
            "get_decision",
            "get_job",
            "get_learning",
            "get_pipeline",
            "get_project",
            "get_self",
            "get_task_type",
            "health_check",
            "link_decision",
            "list_artifacts",
            "list_decisions",
            "list_jobs",
            "list_learnings",
            "list_pipelines",
            "list_projects",
            "list_task_types",
            "query_graph",
            "rotate_own_key",
            "search_learnings",
            "search_surface",
            "submit_learning",
            "traverse_graph",
        )
    }
    visibility.update(
        {
            name: worker_execution_profiles
            for name in (
                "claim_next_job",
                "release_job",
                "submit_payload",
            )
        }
    )
    visibility.update(
        {
            name: reviewer_profiles
            for name in (
                "approve_job",
                "edit_learning",
                "expire_learning",
                "get_policy_pack",
                "get_run",
                "list_policy_packs",
                "list_runs",
                "promote_learning",
                "reject_job",
                "supersede_decision",
                "supersede_learning",
            )
        }
    )
    visibility.update(
        {
            name: supervisor_profiles
            for name in (
                "force_unlock_escrow",
                "get_stats",
                "query_audit_log",
                "reset_job",
            )
        }
    )
    visibility.update(
        {
            name: admin_profiles
            for name in (
                "archive_project",
                "attach_policy",
                "cancel_pipeline",
                "create_actor",
                "create_job",
                "create_pipeline",
                "create_project",
                "grant_capability",
                "list_actors",
                "load_policy_pack",
                "register_task_type",
                "revoke_actor",
                "revoke_capability",
                "update_job",
                "update_pipeline",
                "update_project",
                "update_task_type",
            )
        }
    )

    canonical_tools = set(canonical_surface_tool_names())
    if set(visibility) != canonical_tools:
        missing = sorted(canonical_tools - set(visibility))
        extra = sorted(set(visibility) - canonical_tools)
        problems: list[str] = []
        if missing:
            problems.append(f"missing={missing}")
        if extra:
            problems.append(f"extra={extra}")
        raise RuntimeError(
            "MCP tool visibility map drifted from docs/surface-1.0.md: "
            + "; ".join(problems)
        )

    return visibility


def visible_tool_names(profile: McpToolProfile | str) -> list[str]:
    """Return canonical tools visible for one discoverability profile."""

    resolved_profile = (
        profile if isinstance(profile, McpToolProfile) else McpToolProfile(profile)
    )
    visibility = tool_visibility_profiles()
    return [
        tool_name
        for tool_name in canonical_surface_tool_names()
        if resolved_profile in visibility[tool_name]
    ]


def worker_visible_tool_names() -> list[str]:
    """Return the default worker-safe MCP listing."""

    return visible_tool_names(McpToolProfile.WORKER)
