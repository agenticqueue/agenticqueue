from __future__ import annotations

from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import subprocess
import threading
from typing import Any, Generator
from urllib.parse import parse_qs, urlparse

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

EXPECTED_TOKEN = "test-token"
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
class RouteSpec:
    method: str
    path_template: str
    path_regex: re.Pattern[str]
    success_status: int
    payload_kind: str
    fallback_paths: tuple[str, ...]
    response_key: str | None


@dataclass(frozen=True)
class RecordedRequest:
    method: str
    path: str
    query: dict[str, list[str]]
    json_body: dict[str, Any] | None
    headers: dict[str, str]


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


def _command_registry() -> dict[tuple[str, ...], CommandSpec]:
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
    return registry


def _payload_kind(command_tokens: tuple[str, ...], spec: CommandSpec) -> str:
    if spec.response_key == "actor":
        return "whoami"
    if spec.path == "/healthz":
        return "health"
    if command_tokens in {
        ("actor", "list"),
        ("project", "list"),
        ("pipeline", "list"),
        ("job", "list"),
        ("task-type", "list"),
        ("decision", "list"),
        ("learning", "list"),
        ("learning", "search"),
        ("graph", "surface-search"),
        ("surface", "search"),
        ("run", "list"),
        ("run", "audit"),
        ("audit",),
        ("artifact", "list"),
        ("admin", "stats"),
        ("stats",),
    }:
        return "list"
    return "echo"


def _compile_path_regex(path_template: str) -> re.Pattern[str]:
    pattern = re.sub(r"\{[^/]+\}", r"[^/]+", path_template)
    return re.compile(rf"^{pattern}$")


def _route_specs() -> list[RouteSpec]:
    route_specs = []
    for command_tokens, spec in _command_registry().items():
        route_specs.append(
            RouteSpec(
                method=spec.method.upper(),
                path_template=spec.path,
                path_regex=_compile_path_regex(spec.path),
                success_status=spec.ok_statuses[0],
                payload_kind=_payload_kind(command_tokens, spec),
                fallback_paths=spec.fallback_paths,
                response_key=spec.response_key,
            )
        )
    return route_specs


class CliTestServer(ThreadingHTTPServer):
    allow_reuse_address = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_class: type[BaseHTTPRequestHandler],
        *,
        route_specs: list[RouteSpec],
        expected_token: str,
    ) -> None:
        super().__init__(server_address, handler_class)
        self.route_specs = route_specs
        self.expected_token = expected_token
        self._lock = threading.Lock()
        self._requests: list[RecordedRequest] = []

    def clear_requests(self) -> None:
        with self._lock:
            self._requests.clear()

    def snapshot_requests(self) -> list[RecordedRequest]:
        with self._lock:
            return list(self._requests)

    def record_request(self, request: RecordedRequest) -> None:
        with self._lock:
            self._requests.append(request)

    def match_route(self, method: str, path: str) -> RouteSpec | None:
        if path == "/health":
            return RouteSpec(
                method="GET",
                path_template="/health",
                path_regex=re.compile(r"^/health$"),
                success_status=200,
                payload_kind="health",
                fallback_paths=(),
                response_key=None,
            )
        for route_spec in self.route_specs:
            if route_spec.method == method and route_spec.path_regex.fullmatch(path):
                return route_spec
        return None


class CliRequestHandler(BaseHTTPRequestHandler):
    server: CliTestServer

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch()

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch()

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch()

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        del format, args
        return

    def _dispatch(self) -> None:
        parsed = urlparse(self.path)
        body = self._read_json_body()
        recorded = RecordedRequest(
            method=self.command,
            path=parsed.path,
            query=parse_qs(parsed.query),
            json_body=body,
            headers={key: value for key, value in self.headers.items()},
        )
        self.server.record_request(recorded)

        route_spec = self.server.match_route(self.command, parsed.path)
        if route_spec is None:
            self._send_json(404, {"message": "route not found"})
            return

        auth_header = self.headers.get("Authorization")
        if auth_header != f"Bearer {self.server.expected_token}":
            self._send_json(401, {"message": "missing bearer token"})
            return

        if parsed.path == "/healthz":
            self._send_json(404, {"message": "fallback to /health"})
            return

        if route_spec.payload_kind == "health":
            self._send_json(200, {"status": "ok"})
            return

        if route_spec.payload_kind == "whoami":
            self._send_json(
                route_spec.success_status,
                {
                    "actor": {
                        "handle": "codex",
                        "actor_type": "agent",
                    },
                    "tokens": [],
                },
            )
            return

        if route_spec.payload_kind == "list":
            self._send_json(
                route_spec.success_status,
                [
                    {
                        "id": "item-1",
                        "method": self.command,
                        "path": parsed.path,
                    }
                ],
            )
            return

        self._send_json(
            route_spec.success_status,
            {
                "ok": True,
                "method": self.command,
                "path": parsed.path,
                "query": {
                    key: values for key, values in parse_qs(parsed.query).items()
                },
                "json_body": body or {},
            },
        )

    def _read_json_body(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return None
        raw_body = self.rfile.read(length)
        if not raw_body:
            return None
        return json.loads(raw_body.decode("utf-8"))

    def _send_json(self, status_code: int, payload: Any) -> None:
        encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[3]


@pytest.fixture(scope="session")
def aq_executable(repo_root: Path) -> Path:
    executable = repo_root / ".venv" / "Scripts" / "aq.exe"
    if not executable.exists():
        raise AssertionError(f"Expected aq CLI at {executable}")
    return executable


@pytest.fixture(scope="session")
def live_cli_server() -> Generator[tuple[str, CliTestServer], None, None]:
    server = CliTestServer(
        ("127.0.0.1", 0),
        CliRequestHandler,
        route_specs=_route_specs(),
        expected_token=EXPECTED_TOKEN,
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        yield base_url, server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.fixture()
def run_aq(
    aq_executable: Path,
    live_cli_server: tuple[str, CliTestServer],
    repo_root: Path,
):
    base_url, server = live_cli_server

    def _run(
        command_args: list[str],
        *,
        include_token: bool,
        timeout_seconds: int = 30,
    ) -> tuple[subprocess.CompletedProcess[str], list[RecordedRequest]]:
        env = {
            **dict(os.environ),
            "AGENTICQUEUE_SERVER": base_url,
        }
        if include_token:
            env["AGENTICQUEUE_TOKEN"] = EXPECTED_TOKEN
        else:
            env.pop("AGENTICQUEUE_TOKEN", None)

        server.clear_requests()
        result = subprocess.run(
            [str(aq_executable), *command_args],
            cwd=repo_root,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout_seconds,
            env=env,
        )
        return result, server.snapshot_requests()

    return _run
