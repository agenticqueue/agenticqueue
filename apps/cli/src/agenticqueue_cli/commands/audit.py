"""Dedicated root-level audit query command."""

from __future__ import annotations

import typer

from agenticqueue_cli.client import CliError, emit_payload, get_state, parse_json_mapping


def register_audit_command(app: typer.Typer) -> None:
    """Register the root `aq audit` command."""

    @app.command("audit")
    def audit_command(
        ctx: typer.Context,
        filters: str | None = typer.Option(
            None,
            "--filters",
            help="JSON object of query-string filters.",
        ),
        actor_id: str | None = typer.Option(
            None,
            "--actor",
            "--actor-id",
            help="Filter by actor UUID.",
        ),
        entity_type: str | None = typer.Option(
            None,
            "--type",
            help="Filter by entity type.",
        ),
        entity_id: str | None = typer.Option(
            None,
            "--entity-id",
            help="Filter by entity UUID.",
        ),
        action: str | None = typer.Option(
            None,
            "--action",
            help="Filter by audit action.",
        ),
        since: str | None = typer.Option(
            None,
            "--since",
            help="Inclusive ISO-8601 lower timestamp bound.",
        ),
        until: str | None = typer.Option(
            None,
            "--until",
            help="Inclusive ISO-8601 upper timestamp bound.",
        ),
        limit: int | None = typer.Option(None, "--limit"),
        cursor: str | None = typer.Option(None, "--cursor"),
    ) -> None:
        """Query audit rows with optional filters."""

        params = parse_json_mapping(filters, option_name="--filters")
        explicit_filters = {
            "actor_id": actor_id,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "action": action,
            "since": since,
            "until": until,
        }
        for key, value in explicit_filters.items():
            if value is not None:
                params[key] = value
        if limit is not None:
            params["limit"] = limit
        if cursor is not None:
            params["cursor"] = cursor

        state = get_state(ctx)
        try:
            payload = state.resolve_client().request_json(
                method="GET",
                path="/v1/audit",
                params=params or None,
            )
        except CliError as error:
            if error.payload is not None:
                emit_payload(state, error.payload)
            else:
                typer.echo(error.message, err=True)
            raise typer.Exit(code=error.exit_code) from error

        emit_payload(state, payload)
