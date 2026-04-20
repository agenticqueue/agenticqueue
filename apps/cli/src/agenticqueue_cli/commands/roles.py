"""Typer commands for the Phase 9 RBAC transport surface."""

from __future__ import annotations

import datetime as dt
import json
import uuid

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker
import typer

from agenticqueue_api.auth import authenticate_api_token
from agenticqueue_api.config import (
    get_psycopg_connect_args,
    get_sqlalchemy_sync_database_url,
)
from agenticqueue_api.roles import (
    RoleName,
    assign_role,
    list_role_assignments_for_actor,
    list_roles,
    revoke_role_assignment,
)


def _default_session_factory() -> sessionmaker[Session]:
    engine = sa.create_engine(
        get_sqlalchemy_sync_database_url(),
        future=True,
        connect_args=get_psycopg_connect_args(),
    )
    return sessionmaker(bind=engine, expire_on_commit=False)


def _echo_json(payload: dict[str, object], *, err: bool = False) -> None:
    typer.echo(json.dumps(payload, sort_keys=True), err=err)


def _require_admin_token(session: Session, token: str | None):
    if token is None or not token.strip():
        _echo_json(
            {"error_code": "unauthorized", "message": "AQ_API_TOKEN is required"},
            err=True,
        )
        raise typer.Exit(code=1)

    authenticated = authenticate_api_token(session, token.strip())
    if authenticated is None:
        _echo_json(
            {"error_code": "unauthorized", "message": "Invalid bearer token"},
            err=True,
        )
        raise typer.Exit(code=1)
    if authenticated.actor.actor_type != "admin":
        _echo_json(
            {"error_code": "forbidden", "message": "Admin actor required"},
            err=True,
        )
        raise typer.Exit(code=1)
    return authenticated


def build_roles_app(
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> typer.Typer:
    """Build the RBAC CLI group."""

    resolved_factory = session_factory or _default_session_factory()
    app = typer.Typer(help="Call the RBAC transport surface.")

    @app.command("list")
    def list_command(
        token: str | None = typer.Option(default=None, envvar="AQ_API_TOKEN"),
    ) -> None:
        """List the seeded RBAC roles."""

        with resolved_factory() as session:
            _require_admin_token(session, token)
            roles = [role.model_dump(mode="json") for role in list_roles(session)]
        _echo_json({"roles": roles})

    @app.command("assign")
    def assign_command(
        actor_id: uuid.UUID,
        role_name: RoleName,
        token: str | None = typer.Option(default=None, envvar="AQ_API_TOKEN"),
        expires_at: dt.datetime | None = typer.Option(default=None),
    ) -> None:
        """Assign one seeded role to an actor."""

        with resolved_factory() as session:
            authenticated = _require_admin_token(session, token)
            try:
                assignment = assign_role(
                    session,
                    actor_id=actor_id,
                    role_name=role_name,
                    granted_by_actor_id=authenticated.actor.id,
                    expires_at=expires_at,
                )
            except ValueError as error:
                if session.in_transaction():
                    session.rollback()
                _echo_json(
                    {"error_code": "not_found", "message": str(error)},
                    err=True,
                )
                raise typer.Exit(code=1) from error
            session.commit()
        _echo_json(assignment.model_dump(mode="json"))

    @app.command("revoke")
    def revoke_command(
        assignment_id: uuid.UUID,
        token: str | None = typer.Option(default=None, envvar="AQ_API_TOKEN"),
    ) -> None:
        """Revoke one actor-role assignment."""

        with resolved_factory() as session:
            _require_admin_token(session, token)
            assignment = revoke_role_assignment(session, assignment_id)
            if assignment is None:
                if session.in_transaction():
                    session.rollback()
                _echo_json(
                    {
                        "error_code": "not_found",
                        "message": "Role assignment not found",
                    },
                    err=True,
                )
                raise typer.Exit(code=1)
            session.commit()
        _echo_json(assignment.model_dump(mode="json"))

    @app.command("actor")
    def actor_command(
        actor_id: uuid.UUID,
        token: str | None = typer.Option(default=None, envvar="AQ_API_TOKEN"),
    ) -> None:
        """List active role assignments for one actor."""

        with resolved_factory() as session:
            _require_admin_token(session, token)
            assignments = [
                assignment.model_dump(mode="json")
                for assignment in list_role_assignments_for_actor(session, actor_id)
            ]
        _echo_json({"actor_id": str(actor_id), "roles": assignments})

    return app


__all__ = ["build_roles_app"]
