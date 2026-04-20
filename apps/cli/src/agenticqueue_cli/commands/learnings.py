"""Typer commands for the AQ-68 learnings transport surface."""

from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker
import typer

from agenticqueue_api.config import (
    get_psycopg_connect_args,
    get_sqlalchemy_sync_database_url,
)
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


def _echo_json(payload: dict[str, object], *, err: bool = False) -> None:
    typer.echo(json.dumps(payload, sort_keys=True), err=err)


def _load_json_argument(raw_value: str) -> dict[str, object]:
    try:
        payload = json.loads(raw_value)
    except json.JSONDecodeError as error:
        raise typer.BadParameter("expected valid JSON") from error
    if not isinstance(payload, dict):
        raise typer.BadParameter("expected a JSON object")
    return payload


def build_learnings_app(
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> typer.Typer:
    """Build the learnings CLI group."""

    resolved_factory = session_factory or _default_session_factory()
    app = typer.Typer(help="Call the learnings transport surface.")

    def _run_command(
        *,
        token: str | None,
        trace_name: str,
        mutation: bool,
        callback,
    ) -> None:
        with resolved_factory() as session:
            try:
                authenticated = authenticate_surface_token(
                    session,
                    token=token,
                    trace_id=f"aq-cli-{trace_name}-{uuid.uuid4()}",
                )
                result = callback(session, authenticated)
                if mutation:
                    session.commit()
                _echo_json(result.model_dump(mode="json"))
            except LearningSurfaceError as error:
                if session.in_transaction():
                    session.rollback()
                _echo_json(error.payload, err=True)
                raise typer.Exit(code=1) from error
            except Exception:
                if session.in_transaction():
                    session.rollback()
                raise

    @app.command("get")
    def get_command(
        task_id: uuid.UUID,
        actor_id: uuid.UUID,
        token: str | None = typer.Option(default=None, envvar="AQ_API_TOKEN"),
        scope: LearningScope | None = None,
        limit: int = typer.Option(default=5, min=1, max=10),
    ) -> None:
        """Return the top relevant learnings for one task."""

        _run_command(
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

    @app.command("search")
    def search_command(
        query: str,
        token: str | None = typer.Option(default=None, envvar="AQ_API_TOKEN"),
        project: uuid.UUID | None = None,
        task_type: str | None = None,
        repo_scope: str | None = None,
        limit: int = typer.Option(default=10, min=1, max=25),
    ) -> None:
        """Search active learnings."""

        _run_command(
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

    @app.command("submit")
    def submit_command(
        task_id: uuid.UUID,
        learning_object: str = typer.Option(..., help="Learning payload JSON object."),
        token: str | None = typer.Option(default=None, envvar="AQ_API_TOKEN"),
    ) -> None:
        """Create a task-linked learning."""

        _run_command(
            token=token,
            trace_name="submit-task-learning",
            mutation=True,
            callback=lambda session, authenticated: invoke_submit_task_learning(
                session,
                authenticated=authenticated,
                payload=SubmitTaskLearningRequest(
                    task_id=task_id,
                    learning_object=LearningSchemaModel.model_validate(
                        _load_json_argument(learning_object)
                    ),
                ),
            ),
        )

    @app.command("promote")
    def promote_command(
        learning_id: uuid.UUID,
        target_scope: LearningScope,
        token: str | None = typer.Option(default=None, envvar="AQ_API_TOKEN"),
    ) -> None:
        """Promote one learning."""

        _run_command(
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

    @app.command("supersede")
    def supersede_command(
        learning_id: uuid.UUID,
        replaced_by: uuid.UUID,
        token: str | None = typer.Option(default=None, envvar="AQ_API_TOKEN"),
        reason: str | None = None,
    ) -> None:
        """Supersede one learning."""

        _run_command(
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

    return app


__all__ = ["build_learnings_app"]
