"""Typer CLI surface for AgenticQueue local tooling."""

from __future__ import annotations

import json
import uuid

import sqlalchemy as sa
import typer
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_cli.commands.learnings import build_learnings_app
from agenticqueue_cli.commands.memory import build_memory_app
from agenticqueue_cli.commands.packet import register_packet_command
from agenticqueue_cli.commands.roles import build_roles_app
from agenticqueue_api.audit import set_session_audit_context
from agenticqueue_api.config import (
    get_psycopg_connect_args,
    get_sqlalchemy_sync_database_url,
)
from agenticqueue_api.learnings import LearningPromotionService
from agenticqueue_api.migrations import apply_database_migrations
from agenticqueue_api.middleware.idempotency import (
    cleanup_expired_idempotency_keys,
    get_idempotency_stats,
    stats_as_json,
)
from agenticqueue_api.schemas.learning import LearningScope
from agenticqueue_api.seed import load_seed_fixture, seed_example_data

app = typer.Typer(help="AgenticQueue local developer commands.")
idempotency_app = typer.Typer(help="Inspect and maintain idempotency cache rows.")
learning_app = typer.Typer(help="Inspect and promote learnings.")


def _default_session_factory() -> sessionmaker[Session]:
    engine = sa.create_engine(
        get_sqlalchemy_sync_database_url(),
        future=True,
        connect_args=get_psycopg_connect_args(),
    )
    return sessionmaker(bind=engine, expire_on_commit=False)


@app.callback()
def main() -> None:
    """AgenticQueue local CLI."""


@app.command("seed")
def seed_command() -> None:
    """Seed one deterministic local workspace, project, admin actor, and task."""

    fixture = load_seed_fixture()
    session_factory = _default_session_factory()
    with session_factory() as session:
        set_session_audit_context(
            session,
            actor_id=None,
            trace_id="aq-seed-cli",
        )
        result = seed_example_data(session, fixture)
        session.commit()

    typer.echo(json.dumps(result.model_dump(mode="json"), sort_keys=True))


@app.command("init")
def init_command() -> None:
    """Run database migrations for a local deployment."""

    apply_database_migrations()
    typer.echo(json.dumps({"status": "migrated"}, sort_keys=True))


@idempotency_app.command("stats")
def idempotency_stats_command() -> None:
    """Print idempotency hit/miss/expiry counters as JSON."""

    session_factory = _default_session_factory()
    with session_factory() as session:
        typer.echo(stats_as_json(get_idempotency_stats(session)))


@idempotency_app.command("cleanup")
def idempotency_cleanup_command() -> None:
    """Delete expired idempotency rows for a nightly cleanup hook."""

    session_factory = _default_session_factory()
    with session_factory() as session:
        deleted = cleanup_expired_idempotency_keys(session)
        session.commit()
    typer.echo(json.dumps({"deleted": deleted}, sort_keys=True))


@learning_app.command("promote")
def learning_promote_command(
    learning_id: uuid.UUID,
    target_scope: LearningScope,
) -> None:
    """Promote one learning to project or global scope."""

    session_factory = _default_session_factory()
    with session_factory() as session:
        set_session_audit_context(
            session,
            actor_id=None,
            trace_id="aq-learning-promote-cli",
        )
        service = LearningPromotionService(session)
        try:
            promoted = service.promote(
                learning_id=learning_id,
                target_scope=target_scope,
            )
        except (KeyError, ValueError) as error:
            typer.echo(str(error), err=True)
            raise typer.Exit(code=1) from error
        session.commit()

    typer.echo(json.dumps(promoted.model_dump(mode="json"), sort_keys=True))


learnings_app = build_learnings_app(session_factory=_default_session_factory())
memory_app = build_memory_app(session_factory=_default_session_factory())
roles_app = build_roles_app(session_factory=_default_session_factory())
app.add_typer(idempotency_app, name="idempotency")
app.add_typer(learning_app, name="learning")
app.add_typer(learnings_app, name="learnings")
app.add_typer(memory_app, name="memory")
app.add_typer(roles_app, name="roles")
register_packet_command(app, session_factory=_default_session_factory())


if __name__ == "__main__":
    app()
