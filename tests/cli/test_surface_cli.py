from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from typing import Any

from typer.testing import CliRunner

from agenticqueue_cli.catalog import SURFACE_COMMANDS, VISIBLE_GROUPS
from agenticqueue_cli.client import CliState, OutputFormat
from agenticqueue_cli.main import app


class FakeClient:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def request_json(
        self,
        *,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        ok_statuses: tuple[int, ...] = (200,),
        fallback_paths: tuple[str, ...] = (),
    ) -> Any:
        self.calls.append(
            {
                "method": method,
                "path": path,
                "params": params,
                "json_body": json_body,
                "ok_statuses": ok_statuses,
                "fallback_paths": fallback_paths,
            }
        )
        return {
            "method": method,
            "path": path,
            "params": params or {},
            "json_body": json_body or {},
        }


def build_state(
    client: FakeClient, *, output: OutputFormat = OutputFormat.JSON
) -> CliState:
    return CliState(
        server="http://testserver",
        token="test-token",
        output=output,
        verbose=False,
        client=client,
    )


def test_root_help_lists_the_expected_command_groups() -> None:
    runner = CliRunner()

    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for group in VISIBLE_GROUPS:
        assert group in result.stdout


def test_every_surface_command_has_a_help_entry() -> None:
    runner = CliRunner()

    for command_tokens in SURFACE_COMMANDS:
        result = runner.invoke(app, [*command_tokens, "--help"])
        assert result.exit_code == 0, f"missing help for {' '.join(command_tokens)}"


def test_representative_group_commands_issue_expected_requests() -> None:
    runner = CliRunner()
    cases = (
        (("actor", "list"), "GET", "/v1/actors"),
        (("project", "create", "--body", '{"name":"Demo"}'), "POST", "/v1/workspaces"),
        (("pipeline", "get", "pipe-123"), "GET", "/v1/projects/pipe-123"),
        (
            ("job", "comment", "job-123", "--body", '{"body":"note"}'),
            "POST",
            "/v1/tasks/job-123/comments",
        ),
        (("task-type", "list"), "GET", "/v1/task-types"),
        (
            ("decision", "link", "dec-123", "--body", '{"job_id":"job-1"}'),
            "POST",
            "/v1/decisions/dec-123/link",
        ),
        (
            ("learning", "search", "--filters", '{"q":"alpha"}'),
            "GET",
            "/v1/learnings/search",
        ),
        (
            ("graph", "neighborhood", "node-123", "--filters", '{"hops":2}'),
            "GET",
            "/v1/graph/neighborhood/node-123",
        ),
        (
            ("policy", "attach", "pipe-123", "--body", '{"policy_id":"pol-1"}'),
            "PATCH",
            "/v1/projects/pipe-123",
        ),
        (("run", "list"), "GET", "/v1/runs"),
        (
            ("artifact", "attach", "--body", '{"job_id":"job-123"}'),
            "POST",
            "/v1/artifacts",
        ),
        (("admin", "stats"), "GET", "/stats"),
    )

    for args, expected_method, expected_path in cases:
        client = FakeClient()
        result = runner.invoke(app, list(args), obj=build_state(client))

        assert result.exit_code == 0, args
        payload = json.loads(result.stdout)
        assert payload["method"] == expected_method
        assert payload["path"] == expected_path


def test_root_aliases_route_to_the_expected_paths() -> None:
    runner = CliRunner()
    client = FakeClient()

    result = runner.invoke(
        app,
        ["claim", "--filters", '{"project_id":"proj-1"}'],
        obj=build_state(client),
    )

    assert result.exit_code == 0
    assert client.calls[0]["method"] == "POST"
    assert client.calls[0]["path"] == "/v1/tasks/claim"
    assert client.calls[0]["params"] == {"project_id": "proj-1"}

    packet_client = FakeClient()
    packet_result = runner.invoke(
        app,
        ["packet", "task-123"],
        obj=build_state(packet_client),
    )

    assert packet_result.exit_code == 0
    assert packet_client.calls[0]["path"] == "/v1/tasks/task-123/packet"


def test_output_flag_supports_yaml() -> None:
    runner = CliRunner()
    client = FakeClient()

    result = runner.invoke(
        app,
        ["--output", "yaml", "actor", "list"],
        obj=build_state(client, output=OutputFormat.YAML),
    )

    assert result.exit_code == 0
    assert "method: GET" in result.stdout
    assert "path: /v1/actors" in result.stdout


def test_whoami_returns_the_actor_subpayload() -> None:
    class WhoAmIClient(FakeClient):
        def request_json(self, **kwargs: Any) -> Any:  # type: ignore[override]
            self.calls.append(kwargs)
            return {
                "actor": {"handle": "codex", "actor_type": "agent"},
                "tokens": [],
            }

    runner = CliRunner()
    client = WhoAmIClient()

    result = runner.invoke(app, ["whoami"], obj=build_state(client))

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"actor_type": "agent", "handle": "codex"}


def test_health_command_uses_live_http_server() -> None:
    class HealthHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                self.send_response(404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"message":"missing"}')
                return
            if self.path == "/health":
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(b'{"status":"ok"}')
                return
            self.send_response(500)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()

    try:
        runner = CliRunner()
        result = runner.invoke(
            app,
            ["--server", f"http://127.0.0.1:{server.server_port}", "health"],
        )
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {"status": "ok"}
