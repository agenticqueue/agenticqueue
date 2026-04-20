from __future__ import annotations

import codecs
import copy
import datetime as dt
import json
import uuid
from collections.abc import Awaitable, Callable, MutableMapping
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.types import Message

from agenticqueue_api.middleware.idempotency import (
    IDEMPOTENCY_KEY_HEADER,
    IDEMPOTENCY_REPLAYED_HEADER,
)
from agenticqueue_api.models.idempotency_key import IdempotencyKeyRecord
from agenticqueue_api.middleware.payload_limits import (
    ContentSizeLimitMiddleware,
    DEFAULT_CONTENT_SIZE_LIMIT,
)
from agenticqueue_api.middleware.secret_redaction import SECRET_BLOCKED_HEADER
from agenticqueue_api.middleware import idempotency as idempotency_module


def _fake_aws_access_key() -> str:
    return "AKIA" + "1234567890ABCDEF"


def _fake_github_pat() -> str:
    return "gh" + "p_" + "1234567890abcdef1234567890abcdef1234"


def _fake_slack_bot_token() -> str:
    return "xox" + "b-" + "123456789012-abcdefabcdefabcd"


def _auth_headers(
    token: str | None,
    *,
    idempotency_key: str | None = None,
    content_type: str = "application/json",
) -> dict[str, str]:
    headers = {"Content-Type": content_type}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    if idempotency_key is not None:
        headers[IDEMPOTENCY_KEY_HEADER] = idempotency_key
    return headers


def _assert_structured_error(response: Any, expected_status: int) -> None:
    assert response.status_code == expected_status
    body = response.json()
    assert body.keys() >= {"error_code", "message", "details"}


def test_record_type_points_at_the_live_model() -> None:
    assert idempotency_module._record_type() is IdempotencyKeyRecord


AUTH_CASES = [
    ("missing_authorization", "missing", "/v1/tasks/demo/complete", 401),
    ("invalid_bearer_scheme", "invalid_scheme", "/v1/tasks/demo/complete", 401),
    ("expired_token", "expired", "/v1/tasks/demo/complete", 401),
    ("revoked_token", "revoked", "/v1/tasks/demo/complete", 401),
    ("wrong_actor_on_admin_route", "agent", "/v1/admin-only", 403),
]


@pytest.mark.parametrize(
    ("name", "case_kind", "path", "expected_status"),
    AUTH_CASES,
    ids=[case[0] for case in AUTH_CASES],
)
def test_auth_vectors_fail_cleanly(
    firewall_app_factory: Callable[..., Any],
    token_factory: Callable[..., tuple[Any, str]],
    submission_payload_factory: Callable[[], dict[str, Any]],
    name: str,
    case_kind: str,
    path: str,
    expected_status: int,
) -> None:
    del name
    _, admin_token = token_factory(handle="firewall-admin", actor_type="admin")
    payload: dict[str, Any] = (
        submission_payload_factory()
        if path.endswith("complete")
        else {"message": "admin-check"}
    )

    if case_kind == "missing":
        headers = _auth_headers(None, idempotency_key=str(uuid.uuid4()))
    elif case_kind == "invalid_scheme":
        headers = {
            "Authorization": f"Token {admin_token}",
            IDEMPOTENCY_KEY_HEADER: str(uuid.uuid4()),
            "Content-Type": "application/json",
        }
    elif case_kind == "expired":
        _, expired_token = token_factory(
            handle="expired-admin",
            actor_type="admin",
            expires_at=dt.datetime.now(dt.UTC) - dt.timedelta(minutes=1),
        )
        headers = _auth_headers(expired_token, idempotency_key=str(uuid.uuid4()))
    elif case_kind == "revoked":
        _, revoked_token = token_factory(
            handle="revoked-admin",
            actor_type="admin",
            revoked=True,
        )
        headers = _auth_headers(revoked_token, idempotency_key=str(uuid.uuid4()))
    else:
        _, agent_token = token_factory(handle="plain-agent", actor_type="agent")
        headers = _auth_headers(agent_token, idempotency_key=str(uuid.uuid4()))

    with TestClient(firewall_app_factory()) as client:
        response = client.post(path, headers=headers, json=payload)

    _assert_structured_error(response, expected_status)


MALFORMED_JSON_CASES = [
    ("truncated_json", b'{"output":{"diff_url":"oops"}', 422),
    ("extra_comma", b'{"output":{"diff_url":"oops",},"dod_results":[]}', 422),
    ("invalid_utf8", b'{"output":{"diff_url":"\xff"}}', 400),
    ("non_json_text", b"plain text body", 422),
]


@pytest.mark.parametrize(
    ("name", "body", "expected_status"),
    MALFORMED_JSON_CASES,
    ids=[case[0] for case in MALFORMED_JSON_CASES],
)
def test_malformed_json_vectors_fail_cleanly(
    firewall_app_factory: Callable[..., Any],
    token_factory: Callable[..., tuple[Any, str]],
    name: str,
    body: bytes,
    expected_status: int,
) -> None:
    del name
    _, token = token_factory(handle="json-admin", actor_type="admin")
    with TestClient(firewall_app_factory()) as client:
        response = client.post(
            "/v1/tasks/demo/complete",
            headers=_auth_headers(token, idempotency_key=str(uuid.uuid4())),
            content=body,
        )

    _assert_structured_error(response, expected_status)


def test_utf16_bom_secret_payload_is_blocked(
    firewall_app_factory: Callable[..., Any],
    token_factory: Callable[..., tuple[Any, str]],
    submission_payload_factory: Callable[[], dict[str, Any]],
) -> None:
    _, token = token_factory(handle="utf16-admin", actor_type="admin")
    payload = submission_payload_factory()
    payload["output"]["learnings"][0]["what_happened"] = _fake_aws_access_key()
    body = json.dumps(payload).encode("utf-16")

    with TestClient(firewall_app_factory(hard_block_secrets=True)) as client:
        response = client.post(
            "/v1/tasks/demo/complete",
            headers=_auth_headers(token, idempotency_key=str(uuid.uuid4())),
            content=body,
        )

    _assert_structured_error(response, 400)
    assert response.headers[SECRET_BLOCKED_HEADER] == "aws_access_key"


def test_redaction_mode_rewrites_secret_payload(
    firewall_app_factory: Callable[..., Any],
    token_factory: Callable[..., tuple[Any, str]],
    submission_payload_factory: Callable[[], dict[str, Any]],
) -> None:
    _, token = token_factory(handle="redaction-admin", actor_type="admin")
    payload = submission_payload_factory()
    payload["output"]["learnings"][0]["what_happened"] = _fake_github_pat()
    payload["output"]["learnings"][0]["what_learned"] = _fake_slack_bot_token()

    with TestClient(firewall_app_factory(hard_block_secrets=False)) as client:
        response = client.post(
            "/v1/tasks/demo/complete",
            headers=_auth_headers(token, idempotency_key=str(uuid.uuid4())),
            json=payload,
        )

    assert response.status_code == 200
    body = response.json()
    assert body["payload"]["output"]["learnings"][0]["what_happened"] == (
        "[REDACTED:github_pat]"
    )
    assert body["payload"]["output"]["learnings"][0]["what_learned"] == (
        "[REDACTED:slack_bot_token]"
    )
    assert body["redaction"]["redaction_count"] == 2


def test_base64_zip_bomb_variant_returns_413(
    firewall_app_factory: Callable[..., Any],
    token_factory: Callable[..., tuple[Any, str]],
    submission_payload_factory: Callable[[], dict[str, Any]],
) -> None:
    _, token = token_factory(handle="oversize-admin", actor_type="admin")
    payload = submission_payload_factory()
    payload["output"]["learnings"][0]["what_happened"] = "A" * (
        DEFAULT_CONTENT_SIZE_LIMIT + 1024
    )

    with TestClient(firewall_app_factory()) as client:
        response = client.post(
            "/v1/tasks/demo/complete",
            headers=_auth_headers(token, idempotency_key=str(uuid.uuid4())),
            json=payload,
        )

    _assert_structured_error(response, 413)


def test_declared_hundred_megabyte_payload_is_rejected_from_content_length() -> None:
    async def downstream(
        _scope: MutableMapping[str, Any],
        _receive: Callable[[], Awaitable[Message]],
        _send: Callable[[Message], Awaitable[None]],
    ) -> None:
        raise AssertionError("payload limit middleware should short-circuit first")

    middleware = ContentSizeLimitMiddleware(downstream)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/tasks/demo/complete",
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(100 * 1024 * 1024).encode("ascii")),
        ],
    }
    sent_messages: list[Message] = []

    async def receive() -> Message:
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def send(message: Message) -> None:
        sent_messages.append(message)

    import asyncio

    asyncio.run(middleware(scope, receive, send))
    assert sent_messages[0]["status"] == 413


def test_chunked_zip_bomb_variant_returns_413() -> None:
    async def downstream(
        _scope: MutableMapping[str, Any],
        _receive: Callable[[], Awaitable[Message]],
        _send: Callable[[Message], Awaitable[None]],
    ) -> None:
        raise AssertionError("chunked payload should be rejected before the handler")

    middleware = ContentSizeLimitMiddleware(downstream)
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/tasks/demo/complete",
        "headers": [(b"content-type", b"application/json")],
    }
    chunk = b"A" * (DEFAULT_CONTENT_SIZE_LIMIT // 2)
    messages = [
        {
            "type": "http.request",
            "body": codecs.BOM_UTF8 + b'{"blob":"',
            "more_body": True,
        },
        {"type": "http.request", "body": chunk, "more_body": True},
        {"type": "http.request", "body": chunk, "more_body": True},
        {"type": "http.request", "body": b'"}', "more_body": False},
    ]
    sent_messages: list[Message] = []

    async def receive() -> Message:
        return messages.pop(0)

    async def send(message: Message) -> None:
        sent_messages.append(message)

    import asyncio

    asyncio.run(middleware(scope, receive, send))
    assert sent_messages[0]["status"] == 413


def test_idempotent_replay_works_for_valid_submission(
    firewall_app_factory: Callable[..., Any],
    token_factory: Callable[..., tuple[Any, str]],
    submission_payload_factory: Callable[[], dict[str, Any]],
) -> None:
    _, token = token_factory(handle="replay-admin", actor_type="admin")
    payload = submission_payload_factory()
    key = str(uuid.uuid4())

    with TestClient(firewall_app_factory()) as client:
        first = client.post(
            "/v1/tasks/demo/complete",
            headers=_auth_headers(token, idempotency_key=key),
            json=payload,
        )
        second = client.post(
            "/v1/tasks/demo/complete",
            headers=_auth_headers(token, idempotency_key=key),
            json=copy.deepcopy(payload),
        )

    assert first.status_code == 200
    assert second.status_code == 200
    assert second.headers[IDEMPOTENCY_REPLAYED_HEADER] == "true"
    assert second.json() == first.json()
