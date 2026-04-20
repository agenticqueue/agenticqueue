from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
import uuid

import yaml
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.types import Message

from agenticqueue_api.middleware import secret_redaction as secret_redaction_module
from agenticqueue_api.middleware.secret_redaction import (
    SECRET_BLOCKED_HEADER,
    SecretMatch,
    SecretRedactionMiddleware,
    _apply_redactions,
    _replace_content_length,
    _request_looks_json,
    find_secret_matches,
    has_dictionary_hit,
    scan_json_payload,
    shannon_entropy,
)


def _fake_aws_access_key() -> str:
    return "AKIA" + "1234567890ABCDEF"


def _fake_aws_secret_access_key() -> str:
    return "wJalrXUtnFEMI/K7MDENG/bPxRfiC" + "YEXAMPLEKEY"


def _fake_github_pat() -> str:
    return "gh" + "p_" + "1234567890abcdef1234567890abcdef1234"


def _fake_stripe_live_secret() -> str:
    return "sk" + "_live_" + "1234567890abcdefghijklmnop"


def _fake_slack_bot_token() -> str:
    return "xox" + "b-" + "123456789012-abcdefabcdefabcd"


def _policy_dir(tmp_path: Path, *, hard_block_secrets: bool) -> Path:
    policy_dir = tmp_path / "policies"
    policy_dir.mkdir()
    (policy_dir / "default-coding.policy.yaml").write_text(
        yaml.safe_dump(
            {
                "version": "1.0.0",
                "hitl_required": True,
                "autonomy_tier": 3,
                "capabilities": ["read_repo", "write_branch"],
                "body": {"hard_block_secrets": hard_block_secrets},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    return policy_dir


def _build_app(tmp_path: Path, *, hard_block_secrets: bool) -> FastAPI:
    app = FastAPI()
    app.add_middleware(
        SecretRedactionMiddleware,
        policy_directory=_policy_dir(
            tmp_path,
            hard_block_secrets=hard_block_secrets,
        ),
    )

    @app.post("/v1/tasks")
    async def create_task(request: Request, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "payload": payload,
            "redaction": getattr(request.state, "secret_redaction_context", None),
        }

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    return app


def test_find_secret_matches_covers_known_patterns_and_generic_entropy() -> None:
    cases = {
        "aws_access_key": f"deploy with key {_fake_aws_access_key()} immediately",
        "aws_secret_access_key": _fake_aws_secret_access_key(),
        "github_pat": _fake_github_pat(),
        "gcp_service_account": '{"type":"service_account","private_key_id":"abc123"}',
        "ssh_private_key": "-----BEGIN OPENSSH PRIVATE KEY-----\nabc\n-----END OPENSSH PRIVATE KEY-----",
        "jwt": "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTYifQ.signaturepart",
        "stripe_live_secret": _fake_stripe_live_secret(),
        "slack_bot_token": _fake_slack_bot_token(),
        "bearer_token_url": "https://example.com/hook?access_token=Bearer%20abcdEFGH1234",
        "generic_high_entropy": "Q29kZXhTZWNyZXQtVG9rZW4tQUJDREVGR0hJSktMTU5PUFFSU1RVVldY",
    }

    for expected_kind, sample in cases.items():
        matches = find_secret_matches(sample)
        assert matches
        assert matches[0].kind == expected_kind


def test_entropy_and_dictionary_helpers_discriminate_clean_text() -> None:
    assert shannon_entropy("") == 0.0
    assert shannon_entropy("aaaaabbbbb") < shannon_entropy("abc123XYZ+/=")
    assert has_dictionary_hit("artifact review payload warning")
    assert not find_secret_matches("artifact review payload warning for the next task")
    assert not find_secret_matches("artifact123review123build")
    assert not find_secret_matches(str(uuid.uuid4()))
    assert not find_secret_matches("abcdefghijklmnopqrst....")


def test_request_and_header_helpers_cover_fallback_paths() -> None:
    scope = {"type": "http", "method": "POST", "path": "/v1/tasks", "headers": []}
    assert secret_redaction_module._content_type(scope) == ""
    assert _request_looks_json(scope, b'{"description":"hello"}')
    updated = _replace_content_length(scope, 17)
    assert (b"content-length", b"17") in updated["headers"]


def test_apply_redactions_skips_overlapping_matches() -> None:
    value = "abcdefghij"
    redacted = _apply_redactions(
        value,
        [
            SecretMatch(kind="alpha", start=0, end=5),
            SecretMatch(kind="beta", start=3, end=8),
        ],
    )
    assert redacted == "[REDACTED:alpha]fghij"


def test_scan_json_payload_redacts_nested_values_and_counts_matches() -> None:
    payload = {
        "description": f"Use {_fake_aws_access_key()} and {_fake_github_pat()}",
        "nested": [
            "plain text",
            {"token": _fake_slack_bot_token()},
        ],
    }

    result = scan_json_payload(payload, hard_block=False)

    assert result.redaction_count == 3
    assert result.types_matched == (
        "aws_access_key",
        "github_pat",
        "slack_bot_token",
    )
    assert (
        result.sanitized_payload["description"]
        == "Use [REDACTED:aws_access_key] and [REDACTED:github_pat]"
    )
    assert result.sanitized_payload["nested"][0] == "plain text"
    assert (
        result.sanitized_payload["nested"][1]["token"] == "[REDACTED:slack_bot_token]"
    )


def test_scan_json_payload_hard_block_mode_leaves_payload_unmodified() -> None:
    payload = {"description": _fake_aws_access_key()}

    result = scan_json_payload(payload, hard_block=True)

    assert result.redaction_count == 1
    assert result.types_matched == ("aws_access_key",)
    assert result.sanitized_payload == payload


def test_scan_json_payload_preserves_non_string_scalars() -> None:
    payload = {"count": 7, "enabled": True, "nested": [None, 3.14]}

    result = scan_json_payload(payload, hard_block=False)

    assert result.redaction_count == 0
    assert result.types_matched == ()
    assert result.sanitized_payload == payload


def test_secret_redaction_blocks_payload_when_policy_enables_hard_block(
    tmp_path: Path,
) -> None:
    app = _build_app(tmp_path, hard_block_secrets=True)
    with TestClient(app) as client:
        response = client.post(
            "/v1/tasks",
            json={"description": _fake_aws_access_key()},
        )

    assert response.status_code == 400
    assert response.headers[SECRET_BLOCKED_HEADER] == "aws_access_key"
    assert response.json()["message"] == "Request payload contains secret material"


def test_secret_redaction_rewrites_payload_and_sets_request_context(
    tmp_path: Path,
) -> None:
    app = _build_app(tmp_path, hard_block_secrets=False)
    with TestClient(app) as client:
        response = client.post(
            "/v1/tasks",
            json={
                "description": _fake_aws_access_key(),
                "notes": ["plain", _fake_github_pat()],
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
    app = _build_app(tmp_path, hard_block_secrets=True)
    with TestClient(app) as client:
        health = client.get("/healthz")
        invalid_json = client.post(
            "/v1/tasks",
            data='{"description": "unterminated"',
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
            json={"description": _fake_aws_access_key()},
        )

    assert blocked.status_code == 400


def test_secret_redaction_handles_list_root_payloads(tmp_path: Path) -> None:
    app = _build_app(tmp_path, hard_block_secrets=False)
    with TestClient(app) as client:
        response = client.post(
            "/v1/tasks",
            data=json.dumps(["plain", _fake_aws_access_key()]),
            headers={"Content-Type": "application/json"},
        )

    assert response.status_code == 422


def test_secret_redaction_forwards_non_json_and_safe_payloads(tmp_path: Path) -> None:
    app = _build_app(tmp_path, hard_block_secrets=False)
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

    async def downstream(scope: dict[str, Any], receive: Any, send: Any) -> None:
        first = await receive()
        second = await receive()
        assert first["body"] == b'{"description":"plain"}'
        assert second == {"type": "http.request", "body": b"", "more_body": False}
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b"", "more_body": False})

    middleware = SecretRedactionMiddleware(
        downstream,
        policy_directory=_policy_dir(tmp_path, hard_block_secrets=False),
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
        policy_directory=_policy_dir(tmp_path, hard_block_secrets=False),
        policy_pack_name="missing-pack",
        hard_block_default=True,
    )

    assert middleware._hard_block_secrets() is True
