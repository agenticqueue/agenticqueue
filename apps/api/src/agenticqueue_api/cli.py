"""Typer CLI surface for AgenticQueue local tooling."""

from __future__ import annotations

import json

import sqlalchemy as sa
import typer
from sqlalchemy.orm import Session, sessionmaker

from agenticqueue_api.audit import set_session_audit_context
from agenticqueue_api.config import get_sqlalchemy_sync_database_url
from agenticqueue_api.middleware.idempotency import (
    cleanup_expired_idempotency_keys,
    get_idempotency_stats,
    stats_as_json,
)
from agenticqueue_api.seed import load_seed_fixture, seed_example_data

app = typer.Typer(help="AgenticQueue local developer commands.")
idempotency_app = typer.Typer(help="Inspect and maintain idempotency cache rows.")
app.add_typer(idempotency_app, name="idempotency")


def _default_session_factory() -> sessionmaker[Session]:
    engine = sa.create_engine(get_sqlalchemy_sync_database_url(), future=True)
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


if __name__ == "__main__":
    app()
