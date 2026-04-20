"""FastMCP memory tools for AgenticQueue."""

from __future__ import annotations

from typing import Any
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from fastmcp import FastMCP

from agenticqueue_api.config import (
    get_psycopg_connect_args,
    get_sqlalchemy_sync_database_url,
)
from agenticqueue_api.memory import MemoryLayer
from agenticqueue_api.routers.memory import (
    MemorySearchRequest,
    MemorySearchScope,
    MemorySurfaceError,
    SyncMemoryRequest,
    authenticate_surface_token,
    invoke_memory_stats,
    invoke_search_memory,
    invoke_sync_memory,
)


def _default_session_factory() -> sessionmaker[Session]:
    engine = sa.create_engine(
        get_sqlalchemy_sync_database_url(),
        future=True,
        connect_args=get_psycopg_connect_args(),
    )
    return sessionmaker(bind=engine, expire_on_commit=False)


def _run_read_tool(
    session_factory: sessionmaker[Session],
    *,
    token: str | None,
    trace_name: str,
    callback,
) -> dict[str, Any]:
    with session_factory() as session:
        try:
            authenticated = authenticate_surface_token(
                session,
                token=token,
                trace_id=f"aq-mcp-{trace_name}-{uuid.uuid4()}",
            )
            payload = callback(session, authenticated)
            session.commit()
            return payload.model_dump(mode="json")
        except MemorySurfaceError as error:
            if session.in_transaction():
                session.rollback()
            return error.payload
        except Exception:
            if session.in_transaction():
                session.rollback()
            raise


def _run_write_tool(
    session_factory: sessionmaker[Session],
    *,
    token: str | None,
    trace_name: str,
    callback,
) -> dict[str, Any]:
    return _run_read_tool(
        session_factory,
        token=token,
        trace_name=trace_name,
        callback=callback,
    )


def build_memory_mcp(
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> FastMCP:
    """Build the FastMCP memory tool surface."""

    resolved_factory = session_factory or _default_session_factory()
    mcp = FastMCP(name="AgenticQueue Memory")

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    def search_memory(
        query: str,
        token: str | None = None,
        layers: list[str] | None = None,
        scope: dict[str, Any] | None = None,
        k: int = 10,
        fuzzy_global_search: bool = True,
    ) -> dict[str, Any]:
        """Search memory via the shared retrieval surface."""

        return _run_read_tool(
            resolved_factory,
            token=token,
            trace_name="search-memory",
            callback=lambda session, authenticated: invoke_search_memory(
                session,
                authenticated=authenticated,
                payload=MemorySearchRequest(
                    query=query,
                    layers=list(layers or []),
                    scope=(
                        MemorySearchScope.model_validate(scope)
                        if scope is not None
                        else None
                    ),
                    k=k,
                    fuzzy_global_search=fuzzy_global_search,
                ),
            ),
        )

    @mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        }
    )
    def sync_memory(
        layer: MemoryLayer,
        scope_id: uuid.UUID,
        paths: list[str],
        token: str | None = None,
        full_sync: bool = False,
    ) -> dict[str, Any]:
        """Sync source files into `memory_item`."""

        return _run_write_tool(
            resolved_factory,
            token=token,
            trace_name="sync-memory",
            callback=lambda session, authenticated: invoke_sync_memory(
                session,
                authenticated=authenticated,
                payload=SyncMemoryRequest(
                    layer=layer,
                    scope_id=scope_id,
                    paths=paths,
                    full_sync=full_sync,
                ),
            ),
        )

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    def memory_stats(
        token: str | None = None,
        layer: MemoryLayer | None = None,
        scope_id: uuid.UUID | None = None,
    ) -> dict[str, Any]:
        """Return aggregate counts for stored memory rows."""

        return _run_read_tool(
            resolved_factory,
            token=token,
            trace_name="memory-stats",
            callback=lambda session, authenticated: invoke_memory_stats(
                session,
                authenticated=authenticated,
                layer=layer,
                scope_id=scope_id,
            ),
        )

    return mcp


__all__ = ["build_memory_mcp"]
