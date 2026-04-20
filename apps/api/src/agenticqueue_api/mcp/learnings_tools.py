"""FastMCP learnings tools for AgenticQueue."""

from __future__ import annotations

from collections.abc import Callable
import uuid
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker

from fastmcp import FastMCP

from agenticqueue_api.config import (
    get_psycopg_connect_args,
    get_sqlalchemy_sync_database_url,
)
from agenticqueue_api.auth import AuthenticatedRequest
from agenticqueue_api.learnings import PromoteLearningRequest
from agenticqueue_api.routers.learnings import (
    LearningSurfaceError,
    RelevantLearningsRequest,
    SearchLearningsRequest,
    SubmitTaskLearningRequest,
    SupersedeLearningRequest,
    authenticate_surface_token,
    invoke_get_relevant_learnings,
    invoke_promote_learning,
    invoke_search_learnings,
    invoke_submit_task_learning,
    invoke_supersede_learning,
)
from agenticqueue_api.schemas.learning import LearningSchemaModel, LearningScope


def _default_session_factory() -> sessionmaker[Session]:
    engine = sa.create_engine(
        get_sqlalchemy_sync_database_url(),
        future=True,
        connect_args=get_psycopg_connect_args(),
    )
    return sessionmaker(bind=engine, expire_on_commit=False)


def _run_tool(
    session_factory: sessionmaker[Session],
    *,
    token: str | None,
    trace_name: str,
    mutation: bool,
    callback: Callable[[Session, AuthenticatedRequest], Any],
) -> dict[str, Any]:
    with session_factory() as session:
        try:
            authenticated = authenticate_surface_token(
                session,
                token=token,
                trace_id=f"aq-mcp-{trace_name}-{uuid.uuid4()}",
            )
            result = callback(session, authenticated)
            if mutation:
                session.commit()
            return result.model_dump(mode="json")
        except LearningSurfaceError as error:
            if session.in_transaction():
                session.rollback()
            return error.payload
        except Exception:
            if session.in_transaction():
                session.rollback()
            raise


def build_learnings_mcp(
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> FastMCP:
    """Build the FastMCP learnings tool surface."""

    resolved_factory = session_factory or _default_session_factory()
    mcp = FastMCP(name="AgenticQueue Learnings")

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    def get_relevant_learnings(
        task_id: uuid.UUID,
        actor_id: uuid.UUID,
        token: str | None = None,
        scope: LearningScope | None = None,
        limit: int = 5,
    ) -> dict[str, Any]:
        """Return the top active learnings for a task."""

        return _run_tool(
            resolved_factory,
            token=token,
            trace_name="get-relevant-learnings",
            mutation=False,
            callback=lambda session, authenticated: invoke_get_relevant_learnings(
                session,
                authenticated=authenticated,
                payload=RelevantLearningsRequest(
                    task_id=task_id,
                    actor_id=actor_id,
                    scope=scope,
                    limit=limit,
                ),
            ),
        )

    @mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": False})
    def search_learnings(
        query: str,
        token: str | None = None,
        project: uuid.UUID | None = None,
        task_type: str | None = None,
        repo_scope: str | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        """Search active learnings with optional project and task filters."""

        return _run_tool(
            resolved_factory,
            token=token,
            trace_name="search-learnings",
            mutation=False,
            callback=lambda session, authenticated: invoke_search_learnings(
                session,
                authenticated=authenticated,
                payload=SearchLearningsRequest(
                    query=query,
                    project=project,
                    task_type=task_type,
                    repo_scope=repo_scope,
                    limit=limit,
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
    def submit_task_learning(
        task_id: uuid.UUID,
        learning_object: dict[str, Any],
        token: str | None = None,
    ) -> dict[str, Any]:
        """Create one task-linked learning."""

        return _run_tool(
            resolved_factory,
            token=token,
            trace_name="submit-task-learning",
            mutation=True,
            callback=lambda session, authenticated: invoke_submit_task_learning(
                session,
                authenticated=authenticated,
                payload=SubmitTaskLearningRequest(
                    task_id=task_id,
                    learning_object=LearningSchemaModel.model_validate(learning_object),
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
    def promote_learning(
        learning_id: uuid.UUID,
        target_scope: LearningScope,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Promote one learning to project or global scope."""

        return _run_tool(
            resolved_factory,
            token=token,
            trace_name="promote-learning",
            mutation=True,
            callback=lambda session, authenticated: invoke_promote_learning(
                session,
                authenticated=authenticated,
                learning_id=learning_id,
                payload=PromoteLearningRequest(target_scope=target_scope),
            ),
        )

    @mcp.tool(
        annotations={
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": False,
            "openWorldHint": False,
        }
    )
    def supersede_learning(
        learning_id: uuid.UUID,
        replaced_by: uuid.UUID,
        token: str | None = None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Supersede one learning with another active learning."""

        return _run_tool(
            resolved_factory,
            token=token,
            trace_name="supersede-learning",
            mutation=True,
            callback=lambda session, authenticated: invoke_supersede_learning(
                session,
                authenticated=authenticated,
                learning_id=learning_id,
                payload=SupersedeLearningRequest(
                    replaced_by=replaced_by,
                    reason=reason,
                ),
            ),
        )

    return mcp


__all__ = ["build_learnings_mcp"]
