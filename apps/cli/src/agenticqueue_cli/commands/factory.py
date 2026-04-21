"""Generic command registration helpers for the REST CLI."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

import typer

from agenticqueue_cli.client import (
    CliError,
    emit_payload,
    get_state,
    parse_json_mapping,
)


@dataclass(frozen=True)
class CommandSpec:
    """One REST-backed CLI command."""

    name: str
    method: str
    path: str
    help: str
    ok_statuses: tuple[int, ...] = (200,)
    requires_id: bool = False
    accepts_body: bool = False
    body_required: bool = False
    accepts_filters: bool = False
    supports_pagination: bool = False
    fallback_paths: tuple[str, ...] = ()
    response_key: str | None = None
    default_body: dict[str, Any] = field(default_factory=dict)


def build_group(help_text: str, specs: tuple[CommandSpec, ...]) -> typer.Typer:
    """Build one Typer command group from generic specs."""

    app = typer.Typer(help=help_text, no_args_is_help=True)
    for spec in specs:
        register_spec(app, spec)
    return app


def register_spec(app: typer.Typer, spec: CommandSpec) -> None:
    """Attach one REST-backed command to an app."""

    if spec.accepts_filters and spec.requires_id:
        _register_query_with_id(app, spec)
        return
    if spec.accepts_filters:
        _register_query(app, spec)
        return
    if spec.accepts_body and spec.requires_id:
        _register_body_with_id(app, spec)
        return
    if spec.accepts_body:
        _register_body(app, spec)
        return
    if spec.requires_id:
        _register_id_only(app, spec)
        return
    _register_empty(app, spec)


def _invoke_spec(
    ctx: typer.Context,
    spec: CommandSpec,
    *,
    entity_id: str | None = None,
    body: dict[str, Any] | None = None,
    params: dict[str, Any] | None = None,
) -> None:
    state = get_state(ctx)
    path = _format_path(spec.path, entity_id)
    try:
        payload = state.resolve_client().request_json(
            method=spec.method,
            path=path,
            params=params,
            json_body=body,
            ok_statuses=spec.ok_statuses,
            fallback_paths=_format_paths(spec.fallback_paths, entity_id),
        )
    except CliError as error:
        if error.payload is not None:
            emit_payload(state, error.payload)
        else:
            typer.echo(error.message, err=True)
        raise typer.Exit(code=error.exit_code) from error
    if spec.response_key and isinstance(payload, dict):
        payload = payload.get(spec.response_key, payload)
    emit_payload(state, payload)


def _format_paths(paths: tuple[str, ...], entity_id: str | None) -> tuple[str, ...]:
    return tuple(_format_path(path, entity_id) for path in paths)


def _format_path(template: str, entity_id: str | None) -> str:
    if entity_id is None:
        return template
    values: dict[str, str] = defaultdict(lambda: entity_id)
    values.update({"entity_id": entity_id})
    return template.format_map(values)


def _register_query(app: typer.Typer, spec: CommandSpec) -> None:
    @app.command(spec.name)
    def command(
        ctx: typer.Context,
        filters: str | None = typer.Option(
            None,
            "--filters",
            help="JSON object of query-string filters.",
        ),
        limit: int | None = typer.Option(None, "--limit"),
        cursor: str | None = typer.Option(None, "--cursor"),
    ) -> None:
        params = parse_json_mapping(filters, option_name="--filters")
        if spec.supports_pagination:
            if limit is not None:
                params["limit"] = limit
            if cursor is not None:
                params["cursor"] = cursor
        _invoke_spec(ctx, spec, params=params or None)

    command.__doc__ = spec.help


def _register_query_with_id(app: typer.Typer, spec: CommandSpec) -> None:
    @app.command(spec.name)
    def command(
        ctx: typer.Context,
        entity_id: str,
        filters: str | None = typer.Option(
            None,
            "--filters",
            help="JSON object of query-string filters.",
        ),
        limit: int | None = typer.Option(None, "--limit"),
        cursor: str | None = typer.Option(None, "--cursor"),
    ) -> None:
        params = parse_json_mapping(filters, option_name="--filters")
        if spec.supports_pagination:
            if limit is not None:
                params["limit"] = limit
            if cursor is not None:
                params["cursor"] = cursor
        _invoke_spec(ctx, spec, entity_id=entity_id, params=params or None)

    command.__doc__ = spec.help


def _register_body(app: typer.Typer, spec: CommandSpec) -> None:
    default = ... if spec.body_required else None

    @app.command(spec.name)
    def command(
        ctx: typer.Context,
        body: str | None = typer.Option(
            default,
            "--body",
            help="JSON object payload or @path/to/file.json.",
        ),
    ) -> None:
        payload = spec.default_body | parse_json_mapping(body, option_name="--body")
        _invoke_spec(ctx, spec, body=payload or None)

    command.__doc__ = spec.help


def _register_body_with_id(app: typer.Typer, spec: CommandSpec) -> None:
    default = ... if spec.body_required else None

    @app.command(spec.name)
    def command(
        ctx: typer.Context,
        entity_id: str,
        body: str | None = typer.Option(
            default,
            "--body",
            help="JSON object payload or @path/to/file.json.",
        ),
    ) -> None:
        payload = spec.default_body | parse_json_mapping(body, option_name="--body")
        _invoke_spec(ctx, spec, entity_id=entity_id, body=payload or None)

    command.__doc__ = spec.help


def _register_id_only(app: typer.Typer, spec: CommandSpec) -> None:
    @app.command(spec.name)
    def command(
        ctx: typer.Context,
        entity_id: str,
    ) -> None:
        _invoke_spec(ctx, spec, entity_id=entity_id)

    command.__doc__ = spec.help


def _register_empty(app: typer.Typer, spec: CommandSpec) -> None:
    @app.command(spec.name)
    def command(ctx: typer.Context) -> None:
        _invoke_spec(ctx, spec)

    command.__doc__ = spec.help

