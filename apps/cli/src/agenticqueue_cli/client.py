"""Shared REST client and output helpers for the AgenticQueue CLI."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import json
import os
import uuid
from typing import Any, Protocol

import httpx
import typer
import yaml  # type: ignore[import-untyped]

DEFAULT_SERVER = "http://127.0.0.1:8000"


class OutputFormat(str, Enum):
    """Supported CLI renderers."""

    JSON = "json"
    TABLE = "table"
    YAML = "yaml"


class RestClient(Protocol):
    """Minimal request contract used by the CLI command handlers."""

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
        """Dispatch one HTTP request and return the decoded response payload."""


@dataclass(slots=True)
class CliError(Exception):
    """Structured CLI failure with a deterministic exit code."""

    message: str
    exit_code: int
    payload: Any | None = None


@dataclass(slots=True)
class CliState:
    """Shared Typer context state."""

    server: str = DEFAULT_SERVER
    token: str | None = None
    output: OutputFormat = OutputFormat.JSON
    verbose: bool = False
    client: RestClient | None = None

    def resolve_client(self) -> RestClient:
        if self.client is None:
            self.client = AgenticQueueClient(
                server=self.server,
                token=self.token,
                verbose=self.verbose,
            )
        return self.client


def _resolve_token(explicit_token: str | None) -> str | None:
    if explicit_token:
        return explicit_token
    for env_var in ("AGENTICQUEUE_TOKEN", "AQ_API_TOKEN"):
        value = os.getenv(env_var)
        if value:
            return value
    return None


def get_state(ctx: typer.Context) -> CliState:
    """Return the typed CLI state from the Typer context."""

    if isinstance(ctx.obj, CliState):
        return ctx.obj
    state = CliState()
    ctx.obj = state
    return state


def configure_state(
    ctx: typer.Context,
    *,
    server: str,
    token: str | None,
    output: OutputFormat,
    verbose: bool,
) -> None:
    """Initialize the root Typer context for one invocation."""

    state = get_state(ctx)
    state.server = server.rstrip("/")
    state.token = _resolve_token(token)
    state.output = output
    state.verbose = verbose
    if isinstance(state.client, AgenticQueueClient):
        state.client = AgenticQueueClient(
            server=state.server,
            token=state.token,
            verbose=state.verbose,
        )
    ctx.obj = state


class AgenticQueueClient:
    """Thin HTTP wrapper with exit-code aware error handling."""

    def __init__(
        self,
        *,
        server: str,
        token: str | None,
        verbose: bool = False,
        timeout_seconds: float = 15.0,
    ) -> None:
        self.server = server.rstrip("/")
        self.token = token
        self.verbose = verbose
        self.timeout_seconds = timeout_seconds

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
        paths = (path, *fallback_paths)
        last_error: CliError | None = None
        for candidate in paths:
            try:
                return self._request_once(
                    method=method,
                    path=candidate,
                    params=params,
                    json_body=json_body,
                    ok_statuses=ok_statuses,
                )
            except CliError as error:
                if error.exit_code == 1 and getattr(error.payload, "get", lambda *_: None)(
                    "status_code"
                ) == 404 and candidate != paths[-1]:
                    last_error = error
                    continue
                raise
        if last_error is not None:
            raise last_error
        raise CliError("Request failed", exit_code=3)

    def _request_once(
        self,
        *,
        method: str,
        path: str,
        params: dict[str, Any] | None,
        json_body: dict[str, Any] | None,
        ok_statuses: tuple[int, ...],
    ) -> Any:
        headers: dict[str, str] = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        if method.upper() != "GET":
            headers["Idempotency-Key"] = str(uuid.uuid4())
        if self.verbose:
            typer.echo(
                f"{method.upper()} {self.server}{path}",
                err=True,
            )

        try:
            with httpx.Client(
                base_url=self.server,
                follow_redirects=True,
                timeout=self.timeout_seconds,
            ) as client:
                response = client.request(
                    method=method.upper(),
                    url=path,
                    params=params,
                    json=json_body,
                    headers=headers,
                )
        except httpx.RequestError as error:
            raise CliError(
                f"Network error: {error}",
                exit_code=4,
            ) from error

        payload = _decode_response(response)
        if response.status_code in ok_statuses:
            return payload

        exit_code = 3 if response.status_code >= 500 else 2 if response.status_code in (401, 403) else 1
        message = _error_message(payload, fallback=f"HTTP {response.status_code}")
        raise CliError(
            message,
            exit_code=exit_code,
            payload={"status_code": response.status_code, "response": payload},
        )


def _decode_response(response: httpx.Response) -> Any:
    if response.status_code == 204 or not response.content:
        return {"ok": True, "status_code": response.status_code}
    content_type = response.headers.get("content-type", "")
    if "json" in content_type:
        return response.json()
    try:
        return response.json()
    except ValueError:
        return {"text": response.text, "status_code": response.status_code}


def _error_message(payload: Any, *, fallback: str) -> str:
    if isinstance(payload, dict):
        for key in ("message", "detail", "error", "text"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    return fallback


def parse_json_mapping(raw: str | None, *, option_name: str) -> dict[str, Any]:
    """Parse an inline or @file JSON object."""

    if raw is None:
        return {}
    content = raw
    if raw.startswith("@"):
        with open(raw[1:], "r", encoding="utf-8") as handle:
            content = handle.read()
    try:
        payload = json.loads(content)
    except json.JSONDecodeError as error:
        raise typer.BadParameter("expected a JSON object") from error
    if not isinstance(payload, dict):
        raise typer.BadParameter(
            f"{option_name} expects a JSON object",
        )
    return payload


def emit_payload(state: CliState, payload: Any) -> None:
    """Render one payload using the configured output format."""

    typer.echo(format_payload(payload, state.output))


def format_payload(payload: Any, output: OutputFormat) -> str:
    """Render one response payload for stdout."""

    if output is OutputFormat.JSON:
        return json.dumps(payload, sort_keys=True)
    if output is OutputFormat.YAML:
        return yaml.safe_dump(payload, sort_keys=False).rstrip()
    return _format_table(payload)


def _format_table(payload: Any) -> str:
    if isinstance(payload, dict):
        if len(payload) == 1:
            only_value = next(iter(payload.values()))
            if isinstance(only_value, list):
                return _format_table(only_value)
        if all(not isinstance(value, (dict, list)) for value in payload.values()):
            return "\n".join(f"{key}: {value}" for key, value in payload.items())
        return yaml.safe_dump(payload, sort_keys=False).rstrip()
    if isinstance(payload, list) and payload and all(isinstance(item, dict) for item in payload):
        keys: list[str] = []
        for item in payload:
            for key in item:
                if key not in keys:
                    keys.append(key)
        lines = [" | ".join(keys), " | ".join(["---"] * len(keys))]
        for item in payload:
            lines.append(" | ".join(str(item.get(key, "")) for key in keys))
        return "\n".join(lines)
    if isinstance(payload, list) and not payload:
        return "(empty)"
    return yaml.safe_dump(payload, sort_keys=False).rstrip()
