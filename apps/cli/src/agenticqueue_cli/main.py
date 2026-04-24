"""Typer entrypoint for the AQ REST CLI."""

from __future__ import annotations

import typer

from agenticqueue_cli.client import OutputFormat, configure_state
from agenticqueue_cli.commands.actor import build_actor_app
from agenticqueue_cli.commands.admin import build_admin_app
from agenticqueue_cli.commands.audit import register_audit_command
from agenticqueue_cli.commands.artifact import build_artifact_app
from agenticqueue_cli.commands.decision import build_decision_app
from agenticqueue_cli.commands.factory import CommandSpec, build_group, register_spec
from agenticqueue_cli.commands.graph import build_graph_app
from agenticqueue_cli.commands.job import build_job_app
from agenticqueue_cli.commands.learning import build_learning_app
from agenticqueue_cli.commands.pipeline import build_pipeline_app
from agenticqueue_cli.commands.policy import build_policy_app
from agenticqueue_cli.commands.project import build_project_app
from agenticqueue_cli.commands.run import build_run_app
from agenticqueue_cli.commands.task_type import build_task_type_app

app = typer.Typer(
    help="AgenticQueue CLI thin wrapper over the REST surface.",
    no_args_is_help=True,
)


ROOT_SPECS = (
    CommandSpec(
        name="whoami",
        method="GET",
        path="/v1/auth/tokens",
        help="Return the current actor summary.",
        response_key="actor",
    ),
    CommandSpec(
        name="claim",
        method="POST",
        path="/v1/tasks/claim",
        help="Claim the next matching job/task.",
        accepts_filters=True,
    ),
    CommandSpec(
        name="release",
        method="POST",
        path="/v1/tasks/{entity_id}/release",
        help="Release one claimed job/task.",
        requires_id=True,
    ),
    CommandSpec(
        name="submit",
        method="POST",
        path="/v1/tasks/{entity_id}/submit",
        help="Submit one job/task payload.",
        requires_id=True,
        accepts_body=True,
        body_required=True,
    ),
    CommandSpec(
        name="approve",
        method="POST",
        path="/v1/tasks/{entity_id}/approve",
        help="Approve one HITL-gated job/task.",
        requires_id=True,
    ),
    CommandSpec(
        name="reject",
        method="POST",
        path="/v1/tasks/{entity_id}/reject",
        help="Reject one HITL-gated job/task.",
        requires_id=True,
        accepts_body=True,
    ),
    CommandSpec(
        name="packet",
        method="GET",
        path="/v1/tasks/{entity_id}/packet",
        help="Compile one context packet by task id.",
        requires_id=True,
    ),
    CommandSpec(
        name="audit",
        method="GET",
        path="/v1/audit",
        help="Query audit rows with optional filters.",
        accepts_filters=True,
        supports_pagination=True,
    ),
    CommandSpec(
        name="health",
        method="GET",
        path="/healthz",
        help="Check server health.",
        fallback_paths=("/health",),
    ),
    CommandSpec(
        name="stats",
        method="GET",
        path="/stats",
        help="Fetch server statistics.",
    ),
    CommandSpec(
        name="setup",
        method="POST",
        path="/api/auth/bootstrap_admin",
        help="Run first-time local owner bootstrap.",
        accepts_body=True,
    ),
)


KEY_APP = build_group(
    "Key-management commands.",
    (
        CommandSpec(
            name="rotate",
            method="POST",
            path="/v1/actors/me/rotate-key",
            help="Rotate the current actor token with an optional JSON payload.",
            accepts_body=True,
        ),
    ),
)

ESCROW_APP = build_group(
    "Escrow commands.",
    (
        CommandSpec(
            name="unlock",
            method="POST",
            path="/v1/tasks/{entity_id}/escrow-unlock",
            help="Force-unlock one escrowed job/task.",
            requires_id=True,
            accepts_body=True,
        ),
    ),
)

SURFACE_APP = build_group(
    "Surface-area search commands.",
    (
        CommandSpec(
            name="search",
            method="GET",
            path="/v1/graph/surface",
            help="Search by surface-area filters.",
            accepts_filters=True,
            supports_pagination=True,
        ),
    ),
)


@app.callback()
def main(
    ctx: typer.Context,
    server: str = typer.Option(
        "http://127.0.0.1:8000",
        "--server",
        envvar="AGENTICQUEUE_SERVER",
        help="AgenticQueue REST base URL.",
    ),
    token: str | None = typer.Option(
        None,
        "--token",
        help="Bearer token or use AGENTICQUEUE_TOKEN.",
    ),
    output: OutputFormat = typer.Option(
        OutputFormat.JSON,
        "--output",
        help="Render responses as json, table, or yaml.",
    ),
    verbose: bool = typer.Option(
        False,
        "--verbose",
        help="Print request metadata to stderr.",
    ),
) -> None:
    configure_state(
        ctx,
        server=server,
        token=token,
        output=output,
        verbose=verbose,
    )


for spec in ROOT_SPECS:
    if spec.name == "audit":
        continue
    register_spec(app, spec)
register_audit_command(app)

app.add_typer(KEY_APP, name="key")
app.add_typer(build_actor_app(), name="actor")
app.add_typer(build_project_app(), name="project")
app.add_typer(build_pipeline_app(), name="pipeline")
app.add_typer(build_job_app(), name="job")
app.add_typer(build_task_type_app(), name="task-type")
app.add_typer(build_decision_app(), name="decision")
app.add_typer(build_learning_app(), name="learning")
app.add_typer(build_graph_app(), name="graph")
app.add_typer(SURFACE_APP, name="surface")
app.add_typer(build_policy_app(), name="policy")
app.add_typer(build_run_app(), name="run")
app.add_typer(build_artifact_app(), name="artifact")
app.add_typer(ESCROW_APP, name="escrow")
app.add_typer(build_admin_app(), name="admin")


if __name__ == "__main__":
    app()
