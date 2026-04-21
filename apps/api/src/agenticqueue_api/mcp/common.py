"""Shared helpers for AgenticQueue MCP surfaces."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
import os
from pathlib import Path
import re
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
    params: dict[str, Any] | None = None,
    json_body: Any = None,
) -> dict[str, Any]:
    """Call the in-process FastAPI app and normalize JSON responses."""

    async def _call() -> dict[str, Any]:
        headers = {}
        if token is not None and token.strip():
            headers["Authorization"] = f"Bearer {token.strip()}"
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://agenticqueue.local",
        ) as client:
            response = await client.request(
                method=method,
                url=path,
                headers=headers,
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

    return asyncio.run(_call())


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
