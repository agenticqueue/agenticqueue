from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.types import Message, Receive, Scope, Send

from agenticqueue_api.middleware.secret_redaction import (
    SECRET_BLOCKED_HEADER,
    SecretRedactionMiddleware,
)
from tests.secret_redaction_support import (
    build_app,
    fake_aws_access_key,
    fake_github_pat,
    policy_dir,
)


def test_secret_redaction_blocks_payload_when_policy_enables_hard_block(
    tmp_path: Path,
) -> None:
    app = build_app(tmp_path, hard_block_secrets=True)
    with TestClient(app) as client:
        response = client.post(
            "/v1/tasks",
            json={"description": fake_aws_access_key()},
        )

    assert response.status_code == 400
    assert response.headers[SECRET_BLOCKED_HEADER] == "aws_access_key"
    assert response.json()["message"] == "Request payload contains secret material"


def test_secret_redaction_rewrites_payload_and_sets_request_context(
    tmp_path: Path,
) -> None:
    app = build_app(tmp_path, hard_block_secrets=False)
    with TestClient(app) as client:
        response = client.post(
            "/v1/tasks",
            json={
                "description": fake_aws_access_key(),
                "notes": ["plain", fake_github_pat()],
            },
        )

    assert response.status_code == 200
    body = response.json()
    assert body["payload"]["description"] == "[REDACTED:aws_access_key]"
    assert body["payload"]["notes"][0] == "plain"
    assert body["payload"]["notes"][1] == "[REDACTED:github_pat]"
    assert body["redaction"]["redaction_count"] == 2
    assert body["redaction"]["types_matched"] == ["aws_access_key", "github_pat"]
    assert len(body["redaction"]["original_sha256"]) == 64


def test_secret_redaction_skips_get_invalid_json_and_missing_policy(
    tmp_path: Path,
) -> None:
    app = build_app(tmp_path, hard_block_secrets=True)
    with TestClient(app) as client:
        health = client.get("/healthz")
        invalid_json = client.post(
            "/v1/tasks",
            content='{"description": "unterminated"',
            headers={"Content-Type": "application/json"},
        )

    assert health.status_code == 200
    assert invalid_json.status_code == 422

    fallback_app = FastAPI()
    fallback_app.add_middleware(
        SecretRedactionMiddleware,
        policy_directory=tmp_path / "missing-policies",
    )

    @fallback_app.post("/v1/tasks")
    async def create_task(payload: dict[str, Any]) -> dict[str, Any]:
        return payload

    with TestClient(fallback_app) as client:
        blocked = client.post(
            "/v1/tasks",
            json={"description": fake_aws_access_key()},
        )

    assert blocked.status_code == 400


def test_secret_redaction_handles_list_root_payloads(tmp_path: Path) -> None:
    app = build_app(tmp_path, hard_block_secrets=False)
    with TestClient(app) as client:
        response = client.post(
            "/v1/tasks",
            content=json.dumps(["plain", fake_aws_access_key()]),
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 422


def test_secret_redaction_forwards_non_json_and_safe_payloads(tmp_path: Path) -> None:
    app = build_app(tmp_path, hard_block_secrets=False)
    with TestClient(app) as client:
        non_json = client.post(
            "/v1/tasks",
            content="plain text body",
            headers={"Content-Type": "text/plain"},
        )
        safe = client.post("/v1/tasks", json={"description": "plain text only"})

    assert non_json.status_code == 422
    assert safe.status_code == 200
    assert safe.json()["redaction"] is None


def test_secret_redaction_internal_async_paths_cover_disconnect_and_replay(
    tmp_path: Path,
) -> None:
    app = FastAPI()

    async def downstream(scope: Scope, receive: Receive, send: Send) -> None:
        first = await receive()
        second = await receive()
        assert first["body"] == b'{"description":"plain"}'
        assert second == {"type": "http.request", "body": b"", "more_body": False}
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    middleware = SecretRedactionMiddleware(
        downstream,
        policy_directory=policy_dir(tmp_path, hard_block_secrets=False),
    )

    sent_messages: list[Message] = []

    async def send(message: Message) -> None:
        sent_messages.append(message)

    request_messages = [
        {
            "type": "http.request",
            "body": b'{"description":"plain"}',
            "more_body": True,
        },
        {
            "type": "http.request",
            "body": b"",
            "more_body": False,
        },
    ]

    async def receive() -> Message:
        return request_messages.pop(0)

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/tasks",
        "headers": [(b"content-type", b"application/json")],
    }
    asyncio.run(middleware(scope, receive, send))
    assert sent_messages[0]["status"] == 204

    disconnect_middleware = SecretRedactionMiddleware(app)
    disconnect_messages: list[Message] = []

    async def disconnect_send(message: Message) -> None:
        disconnect_messages.append(message)

    async def disconnect_receive() -> Message:
        return {"type": "http.disconnect"}

    asyncio.run(disconnect_middleware(scope, disconnect_receive, disconnect_send))
    assert disconnect_messages == []
    assert asyncio.run(disconnect_middleware._empty_receive()) == {
        "type": "http.request",
        "body": b"",
        "more_body": False,
    }


def test_secret_redaction_missing_policy_pack_falls_back_to_default(
    tmp_path: Path,
) -> None:
    middleware = SecretRedactionMiddleware(
        FastAPI(),
        policy_directory=policy_dir(tmp_path, hard_block_secrets=False),
        policy_pack_name="missing-pack",
        hard_block_default=True,
    )

    assert middleware._hard_block_secrets() is True
