"""Typer commands for the AQ-86 memory surface."""

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


def _echo_json(payload: dict[str, object], *, err: bool = False) -> None:
    typer.echo(json.dumps(payload, sort_keys=True), err=err)


def build_memory_app(
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> typer.Typer:
    """Build the `aq memory` command group."""

    resolved_factory = session_factory or _default_session_factory()
    app = typer.Typer(help="Call the memory transport surface.")

    def _run_command(
        *,
        token: str | None,
        trace_name: str,
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
                session.commit()
                _echo_json(result.model_dump(mode="json"))
            except MemorySurfaceError as error:
                if session.in_transaction():
                    session.rollback()
                _echo_json(error.payload, err=True)
                raise typer.Exit(code=1) from error
            except Exception:
                if session.in_transaction():
                    session.rollback()
                raise

    @app.command("search")
    def search_command(
        query: str,
        token: str | None = typer.Option(None, envvar="AQ_API_TOKEN"),
        layer: list[str] | None = typer.Option(None, "--layer"),
        project_id: uuid.UUID | None = typer.Option(None, "--project-id"),
        surface_area: list[str] | None = typer.Option(None, "--surface-area"),
        owner: list[str] | None = typer.Option(None, "--owner"),
        learning_type: list[str] | None = typer.Option(
            None,
            "--learning-type",
        ),
        max_age_days: int | None = typer.Option(None, "--max-age-days"),
        k: int = typer.Option(10, min=1, max=25),
        fuzzy_global_search: bool = typer.Option(
            True,
            "--fuzzy/--no-fuzzy",
        ),
    ) -> None:
        """Search memory via the tiered retrieval surface."""

        scope = MemorySearchScope(
            project_id=project_id,
            surface_area=list(surface_area or []),
            owners=list(owner or []),
            learning_types=list(learning_type or []),
            max_age_days=max_age_days,
        )
        _run_command(
            token=token,
            trace_name="search-memory",
            callback=lambda session, authenticated: invoke_search_memory(
                session,
                authenticated=authenticated,
                payload=MemorySearchRequest(
                    query=query,
                    layers=list(layer or []),
                    scope=scope,
                    k=k,
                    fuzzy_global_search=fuzzy_global_search,
                ),
            ),
        )

    @app.command("sync")
    def sync_command(
        scope_id: uuid.UUID,
        token: str | None = typer.Option(None, envvar="AQ_API_TOKEN"),
        layer: MemoryLayer = typer.Option(..., "--layer"),
        path: list[str] | None = typer.Option(None, "--path"),
        full_sync: bool = typer.Option(False, "--full-sync"),
    ) -> None:
        """Sync one set of source files into `memory_item`."""

        _run_command(
            token=token,
            trace_name="sync-memory",
            callback=lambda session, authenticated: invoke_sync_memory(
                session,
                authenticated=authenticated,
                payload=SyncMemoryRequest(
                    layer=layer,
                    scope_id=scope_id,
                    paths=list(path or []),
                    full_sync=full_sync,
                ),
            ),
        )

    @app.command("stats")
    def stats_command(
        token: str | None = typer.Option(None, envvar="AQ_API_TOKEN"),
        layer: MemoryLayer | None = typer.Option(None, "--layer"),
        scope_id: uuid.UUID | None = typer.Option(None, "--scope-id"),
    ) -> None:
        """Return aggregate counts for stored memory rows."""

        _run_command(
            token=token,
            trace_name="memory-stats",
            callback=lambda session, authenticated: invoke_memory_stats(
                session,
                authenticated=authenticated,
                layer=layer,
                scope_id=scope_id,
            ),
        )

    return app


__all__ = ["build_memory_app"]
