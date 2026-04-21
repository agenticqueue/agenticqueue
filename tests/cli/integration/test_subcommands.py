from __future__ import annotations

from dataclasses import dataclass
import json

import pytest

from agenticqueue_cli.commands import (
    actor,
    admin,
    artifact,
    decision,
    graph,
    job,
    learning,
    pipeline,
    policy,
    project,
    run,
    task_type,
)
from agenticqueue_cli.commands.factory import CommandSpec
from agenticqueue_cli.main import ROOT_SPECS

SAMPLE_ID = "entity-123"
SAMPLE_BODY = {
    "name": "Demo",
    "note": "integration",
    "reason": "integration",
}
SAMPLE_FILTERS = {"q": "alpha"}
SAMPLE_LIMIT = "2"
SAMPLE_CURSOR = "cursor-2"


@dataclass(frozen=True)
class CommandCase:
    tokens: tuple[str, ...]
    spec: CommandSpec

    @property
    def id(self) -> str:
        return " ".join(self.tokens)


def _root_extra_specs() -> dict[tuple[str, ...], CommandSpec]:
    return {
        ("key", "rotate"): CommandSpec(
            name="rotate",
            method="POST",
            path="/v1/actors/me/rotate-key",
            help="Rotate the current actor token with an optional JSON payload.",
            accepts_body=True,
        ),
        ("escrow", "unlock"): CommandSpec(
            name="unlock",
            method="POST",
            path="/v1/tasks/{entity_id}/escrow-unlock",
            help="Force-unlock one escrowed job/task.",
            requires_id=True,
            accepts_body=True,
        ),
        ("surface", "search"): CommandSpec(
            name="search",
            method="GET",
            path="/v1/graph/surface",
            help="Search by surface-area filters.",
            accepts_filters=True,
            supports_pagination=True,
        ),
    }


def _all_command_cases() -> list[CommandCase]:
    registry: dict[tuple[str, ...], CommandSpec] = {
        (spec.name,): spec for spec in ROOT_SPECS
    }
    grouped_specs = {
        "actor": actor.SPECS,
        "project": project.SPECS,
        "pipeline": pipeline.SPECS,
        "job": job.SPECS,
        "task-type": task_type.SPECS,
        "decision": decision.SPECS,
        "learning": learning.SPECS,
        "graph": graph.SPECS,
        "policy": policy.SPECS,
        "run": run.SPECS,
        "artifact": artifact.SPECS,
        "admin": admin.SPECS,
    }
    for prefix, specs in grouped_specs.items():
        for spec in specs:
            registry[(prefix, spec.name)] = spec
    registry.update(_root_extra_specs())
    return [CommandCase(tokens=tokens, spec=spec) for tokens, spec in registry.items()]


ALL_COMMAND_CASES = sorted(_all_command_cases(), key=lambda case: case.tokens)


def _command_args(case: CommandCase, *, output: str | None = None) -> list[str]:
    args: list[str] = []
    if output is not None:
        args.extend(["--output", output])
    args.extend(case.tokens)

    if case.spec.requires_id:
        args.append(SAMPLE_ID)

    if case.spec.accepts_filters:
        args.extend(["--filters", json.dumps(SAMPLE_FILTERS, sort_keys=True)])
        if case.spec.supports_pagination:
            args.extend(["--limit", SAMPLE_LIMIT, "--cursor", SAMPLE_CURSOR])

    if case.spec.accepts_body:
        if case.tokens == ("learning", "expire"):
            return args
        args.extend(["--body", json.dumps(SAMPLE_BODY, sort_keys=True)])

    return args


def _expected_path(case: CommandCase) -> str:
    return case.spec.path.replace("{entity_id}", SAMPLE_ID)


def _expected_body(case: CommandCase) -> dict[str, str] | None:
    if not case.spec.accepts_body:
        return None
    if case.tokens == ("learning", "expire"):
        return {"status": "expired"}
    return SAMPLE_BODY


@pytest.mark.parametrize("case", ALL_COMMAND_CASES, ids=lambda case: case.id)
def test_cli_subcommands_happy_path_reaches_live_server(
    case: CommandCase,
    run_aq,
) -> None:
    result, requests = run_aq(
        _command_args(case),
        include_token=True,
    )

    assert result.returncode == 0, result.stderr
    assert requests, case.id

    if case.spec.path == "/healthz":
        assert [request.path for request in requests] == ["/healthz", "/health"]
        payload = json.loads(result.stdout)
        assert payload == {"status": "ok"}
        return

    request = requests[-1]
    assert request.method == case.spec.method
    assert request.path == _expected_path(case)
    assert request.headers["Authorization"] == "Bearer test-token"

    if case.spec.accepts_filters:
        assert request.query["q"] == [SAMPLE_FILTERS["q"]]
        if case.spec.supports_pagination:
            assert request.query["limit"] == [SAMPLE_LIMIT]
            assert request.query["cursor"] == [SAMPLE_CURSOR]
    else:
        assert request.query == {}

    assert request.json_body == _expected_body(case)

    payload = json.loads(result.stdout)
    if case.spec.response_key == "actor":
        assert payload == {"actor_type": "agent", "handle": "codex"}
    else:
        assert payload is not None


@pytest.mark.parametrize("case", ALL_COMMAND_CASES, ids=lambda case: case.id)
def test_cli_subcommands_surface_auth_failures_with_structured_exit_codes(
    case: CommandCase,
    run_aq,
) -> None:
    result, requests = run_aq(
        _command_args(case),
        include_token=False,
    )

    assert result.returncode == 2, result.stderr
    assert requests, case.id

    payload = json.loads(result.stdout)
    assert payload["status_code"] == 401
    assert payload["response"] == {"message": "missing bearer token"}


@pytest.mark.parametrize(
    "case",
    [
        case
        for case in ALL_COMMAND_CASES
        if case.tokens in {("actor", "list"), ("project", "list"), ("stats",)}
    ],
    ids=lambda case: case.id,
)
def test_cli_table_output_renders_markdown_like_rows(
    case: CommandCase,
    run_aq,
) -> None:
    result, requests = run_aq(
        _command_args(case, output="table"),
        include_token=True,
    )

    assert result.returncode == 0, result.stderr
    assert requests, case.id
    assert "|" in result.stdout
    assert "---" in result.stdout
