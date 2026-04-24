from __future__ import annotations

import copy
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.types import Message

from agenticqueue_api.middleware.payload_limits import (
    ContentSizeLimitMiddleware,
    DEFAULT_CONTENT_SIZE_LIMIT,
    MAX_PAYLOAD_DEPTH,
    UPLOAD_CONTENT_SIZE_LIMIT,
    parse_content_length,
    parse_json_payload_depth,
    payload_depth,
    resolve_content_size_limit,
)
from agenticqueue_api.schemas.submit import TaskCompletionSubmission


def _submission_payload() -> dict[str, Any]:
    return {
        "output": {
            "diff_url": "artifacts/diffs/aq-176.patch",
            "test_report": "artifacts/tests/aq-176.txt",
            "artifacts": [
                {
                    "kind": "patch",
                    "uri": "artifacts/diffs/aq-176.patch",
                    "details": {"format": "unified-diff"},
                }
            ],
            "learnings": [
                {
                    "title": "Strict submit payloads prevent silent coercion",
                    "type": "pattern",
                    "what_happened": "A completion payload mixed valid structure with lax field typing.",
                    "what_learned": "Strict models reject ambiguous payloads before they reach deeper validators.",
                    "action_rule": "Validate completion envelopes with strict Pydantic models first.",
                    "applies_when": "Submission payloads feed task closeout or validator logic.",
                    "does_not_apply_when": "The route is read-only and carries no JSON body.",
                    "evidence": ["tests/unit/test_payload_caps.py"],
                    "scope": "project",
                    "confidence": "confirmed",
                    "status": "active",
                    "owner": "agenticqueue-core",
                    "review_date": "2026-04-21",
                }
            ],
        },
        "dod_results": [
            {
                "dod_id": "dod-1",
                "status": "passed",
                "evidence": ["artifacts/diffs/aq-176.patch"],
                "summary": "Payload caps reject oversized request bodies.",
                "failure_reason": None,
                "next_action": None,
            }
        ],
        "had_failure": False,
        "had_block": False,
        "had_retry": False,
    }


def _build_app(counter: dict[str, int]) -> FastAPI:
    app = FastAPI()
    app.add_middleware(ContentSizeLimitMiddleware)

    @app.post("/v1/artifacts")
    def create_artifact(payload: dict[str, Any]) -> dict[str, Any]:
        counter["artifacts"] += 1
        return payload

    @app.post("/v1/tasks/demo/complete")
    def complete_task(payload: TaskCompletionSubmission) -> dict[str, Any]:
        counter["complete"] += 1
        return payload.model_dump(mode="json")

    @app.post("/v1/artifacts/upload")
    def upload_artifact(payload: dict[str, Any]) -> dict[str, Any]:
        counter["upload"] += 1
        return payload

    @app.get("/healthz")
    def healthcheck() -> dict[str, bool]:
        counter["health"] += 1
        return {"ok": True}

    return app


def _run_middleware_with_chunks(
    *,
    chunks: list[bytes],
    scope: MutableMapping[str, Any],
    read_twice: bool = False,
) -> list[Message]:
    sent_messages: list[Message] = []
    request_messages = [
        {
            "type": "http.request",
            "body": chunk,
            "more_body": index < len(chunks) - 1,
        }
        for index, chunk in enumerate(chunks)
    ]
    request_messages.append({"type": "http.disconnect"})

    async def receive() -> Message:
        return request_messages.pop(0)

    async def send(message: Message) -> None:
        sent_messages.append(message)

    async def downstream_app(
        _scope: MutableMapping[str, Any],
        _receive: Callable[[], Awaitable[Message]],
        _send: Callable[[Message], Awaitable[None]],
    ) -> None:
        first = await _receive()
        assert first["type"] == "http.request"
        if read_twice:
            second = await _receive()
            assert second == {
                "type": "http.request",
                "body": b"",
                "more_body": False,
            }
        await _send(
            {
                "type": "http.response.start",
                "status": 204,
                "headers": [],
            }
        )
        await _send({"type": "http.response.body", "body": b"", "more_body": False})

    middleware = ContentSizeLimitMiddleware(downstream_app)

    import asyncio

    asyncio.run(middleware(scope, receive, send))
    return sent_messages


def test_payload_limit_helpers_cover_path_header_and_depth_logic() -> None:
    artifact_scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/artifacts",
        "headers": [(b"content-length", b"12")],
    }
    upload_scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/artifacts/upload",
        "headers": [],
    }
    health_scope = {
        "type": "http",
        "method": "GET",
        "path": "/healthz",
        "headers": [],
    }
    post_health_scope = {
        "type": "http",
        "method": "POST",
        "path": "/healthz",
        "headers": [],
    }

    assert resolve_content_size_limit(artifact_scope) == DEFAULT_CONTENT_SIZE_LIMIT
    assert resolve_content_size_limit(upload_scope) == UPLOAD_CONTENT_SIZE_LIMIT
    assert resolve_content_size_limit(health_scope) is None
    assert resolve_content_size_limit(post_health_scope) is None
    assert parse_content_length(artifact_scope) == 12
    assert (
        parse_content_length(
            {
                **artifact_scope,
                "headers": [(b"content-length", b"nope")],
            }
        )
        is None
    )
    assert payload_depth({}) == 1
    assert payload_depth([]) == 1
    assert payload_depth({"a": {"b": [1, {"c": 2}]}}) == 4
    assert parse_json_payload_depth(b"") is None
    assert parse_json_payload_depth(b'{"a":{"b":[1,{"c":2}]}}') == 4
    assert parse_json_payload_depth(b"not-json") is None


def test_chunked_oversize_request_without_content_length_returns_413() -> None:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/artifacts",
        "headers": [(b"content-type", b"application/json")],
    }
    chunk = b"x" * (DEFAULT_CONTENT_SIZE_LIMIT // 2)
    messages = _run_middleware_with_chunks(
        chunks=[chunk, chunk, b"x"],
        scope=scope,
    )

    assert messages[0]["type"] == "http.response.start"
    assert messages[0]["status"] == 413


def test_disconnect_and_replay_paths_are_covered() -> None:
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/artifacts",
        "headers": [(b"content-type", b"application/json")],
    }
    replay_messages = _run_middleware_with_chunks(
        chunks=[b'{"ok": true}'],
        scope=scope,
        read_twice=True,
    )
    assert replay_messages[0]["status"] == 204

    disconnect_messages = _run_middleware_with_chunks(chunks=[], scope=scope)
    assert disconnect_messages == []

    async def no_op_app(
        _scope: MutableMapping[str, Any],
        _receive: Callable[[], Awaitable[Message]],
        _send: Callable[[Message], Awaitable[None]],
    ) -> None:
        return None

    middleware = ContentSizeLimitMiddleware(no_op_app)
    import asyncio

    assert asyncio.run(middleware._empty_receive()) == {
        "type": "http.request",
        "body": b"",
        "more_body": False,
    }


def test_oversized_payload_returns_413_before_handler_runs() -> None:
    counter = {"artifacts": 0, "complete": 0, "upload": 0, "health": 0}
    app = _build_app(counter)
    with TestClient(app) as client:
        response = client.post(
            "/v1/artifacts",
            json={"blob": "x" * (1024 * 1024)},
        )

    assert response.status_code == 413
    assert response.json()["message"] == "Payload exceeds request body limit"
    assert counter["artifacts"] == 0


def test_submit_payload_route_accepts_valid_payload_and_bypasses_get() -> None:
    counter = {"artifacts": 0, "complete": 0, "upload": 0, "health": 0}
    app = _build_app(counter)
    with TestClient(app) as client:
        health = client.get("/healthz")
        complete = client.post("/v1/tasks/demo/complete", json=_submission_payload())

    assert health.status_code == 200
    assert complete.status_code == 200
    assert counter["health"] == 1
    assert counter["complete"] == 1


def test_submit_payload_rejects_excessive_nesting_with_422() -> None:
    counter = {"artifacts": 0, "complete": 0, "upload": 0, "health": 0}
    app = _build_app(counter)
    payload = _submission_payload()
    nested: dict[str, Any] = {"level_0": "done"}
    for index in range(MAX_PAYLOAD_DEPTH + 1):
        nested = {f"level_{index + 1}": nested}
    payload["output"]["artifacts"][0]["details"] = nested

    with TestClient(app) as client:
        response = client.post("/v1/tasks/demo/complete", json=payload)

    assert response.status_code == 422
    assert response.json()["message"] == "Request payload exceeds maximum nesting depth"
    assert counter["complete"] == 0


def test_submit_payload_rejects_stringified_bool_and_extra_fields_with_422() -> None:
    counter = {"artifacts": 0, "complete": 0, "upload": 0, "health": 0}
    app = _build_app(counter)

    bad_bool = _submission_payload()
    bad_bool["had_retry"] = "true"

    extra_field = copy.deepcopy(_submission_payload())
    extra_field["output"]["learnings"][0]["unexpected"] = "nope"

    with TestClient(app) as client:
        bool_response = client.post("/v1/tasks/demo/complete", json=bad_bool)
        extra_response = client.post("/v1/tasks/demo/complete", json=extra_field)

    assert bool_response.status_code == 422
    assert extra_response.status_code == 422
    assert counter["complete"] == 0
