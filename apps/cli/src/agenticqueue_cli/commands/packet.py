"""Typer command for the Phase 3 packet CLI surface."""

from __future__ import annotations

import json
import uuid
from enum import Enum
from typing import Any

import sqlalchemy as sa
from sqlalchemy.orm import Session, sessionmaker
import typer

from agenticqueue_api.compiler import compile_packet
from agenticqueue_api.config import (
    get_psycopg_connect_args,
    get_sqlalchemy_sync_database_url,
)
from agenticqueue_api.packet_versions import packet_content_hash


class PacketOutputFormat(str, Enum):
    """Supported output encodings for the packet CLI."""

    JSON = "json"
    MARKDOWN = "markdown"


def _default_session_factory() -> sessionmaker[Session]:
    engine = sa.create_engine(
        get_sqlalchemy_sync_database_url(),
        future=True,
        connect_args=get_psycopg_connect_args(),
    )
    return sessionmaker(bind=engine, expire_on_commit=False)


def _render_list(items: list[str]) -> list[str]:
    return [f"- {item}" for item in items] if items else ["- None"]


def _append_json_section(
    lines: list[str],
    *,
    heading: str,
    payload: dict[str, Any] | list[dict[str, Any]] | list[Any],
) -> None:
    lines.extend(
        [
            f"## {heading}",
            "```json",
            json.dumps(payload, indent=2, sort_keys=True),
            "```",
            "",
        ]
    )


def _render_markdown(packet: dict[str, Any]) -> str:
    task = packet["task"]
    repo_scope = packet["repo_scope"]
    permissions = packet["permissions"]
    packet_hash = packet_content_hash(packet)

    lines = [
        "# Packet",
        "",
        "## Task",
        f"- ID: {task['id']}",
        f"- Title: {task['title']}",
        f"- Type: {task['task_type']}",
        f"- State: {task['state']}",
        f"- Packet Version: {packet['packet_version_id']}",
        f"- Packet Hash: {packet_hash}",
        "",
        "## Repo Scope",
        f"- Repo: {repo_scope['repo']}",
        f"- Branch: {repo_scope['branch']}",
        f"- Estimated Token Count: {repo_scope['estimated_token_count']}",
        "",
        "### File Scope",
        *_render_list(repo_scope["file_scope"]),
        "",
        "### Surface Area",
        *_render_list(repo_scope["surface_area"]),
        "",
        "## Definition Of Done",
        *_render_list(packet["definition_of_done"]),
        "",
        "## Open Questions",
        *_render_list(packet["open_questions"]),
        "",
        "## Permissions",
        f"- Policy Name: {permissions['policy_name']}",
        f"- Policy Version: {permissions['policy_version']}",
        f"- Source: {permissions['source']}",
        f"- HITL Required: {permissions['hitl_required']}",
        f"- Autonomy Tier: {permissions['autonomy_tier']}",
        f"- Validation Mode: {permissions['validation_mode']}",
        "",
        "### Capabilities",
        *_render_list(permissions["capabilities"]),
        "",
    ]
    _append_json_section(lines, heading="Task Contract", payload=packet["task_contract"])
    _append_json_section(
        lines,
        heading="Relevant Decisions",
        payload=packet["relevant_decisions"],
    )
    _append_json_section(
        lines,
        heading="Relevant Learnings",
        payload=packet["relevant_learnings"],
    )
    _append_json_section(
        lines,
        heading="Linked Artifacts",
        payload=packet["linked_artifacts"],
    )
    _append_json_section(
        lines,
        heading="Expected Output Schema",
        payload=packet["expected_output_schema"],
    )
    return "\n".join(lines).rstrip()


def register_packet_command(
    app: typer.Typer,
    *,
    session_factory: sessionmaker[Session] | None = None,
) -> None:
    """Attach the `aq packet` command to a Typer app."""

    resolved_factory = session_factory or _default_session_factory()

    @app.command("packet")
    def packet_command(
        task_id: uuid.UUID,
        output_format: PacketOutputFormat = typer.Option(
            PacketOutputFormat.MARKDOWN,
            "--format",
            help="Choose `markdown` for humans or `json` for agent consumption.",
        ),
        version: bool = typer.Option(
            False,
            "--version",
            help="Print the packet content hash and exit.",
        ),
    ) -> None:
        """Compile one task packet and print it to stdout."""

        with resolved_factory() as session:
            try:
                packet = compile_packet(session, task_id)
                session.commit()
            except KeyError as error:
                if session.in_transaction():
                    session.rollback()
                typer.echo(f"Task not found: {task_id}", err=True)
                raise typer.Exit(code=1) from error
            except Exception:
                if session.in_transaction():
                    session.rollback()
                raise

        if version:
            typer.echo(packet_content_hash(packet))
            return

        if output_format is PacketOutputFormat.JSON:
            typer.echo(json.dumps(packet, sort_keys=True))
            return

        typer.echo(_render_markdown(packet))


__all__ = ["PacketOutputFormat", "register_packet_command"]
