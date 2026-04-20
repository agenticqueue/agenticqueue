"""Payload size limiting middleware for mutating routes."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any, Final, cast

from fastapi.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from agenticqueue_api.errors import error_payload

DEFAULT_CONTENT_SIZE_LIMIT: Final = 256 * 1024
UPLOAD_CONTENT_SIZE_LIMIT: Final = 10 * 1024 * 1024
MAX_PAYLOAD_DEPTH: Final = 10
_MUTATING_METHODS = frozenset({"POST", "PATCH"})


def resolve_content_size_limit(
    scope: Scope,
    *,
    default_limit: int = DEFAULT_CONTENT_SIZE_LIMIT,
    upload_limit: int = UPLOAD_CONTENT_SIZE_LIMIT,
) -> int | None:
    """Return the byte limit for one request scope."""

    if scope["type"] != "http":
        return None

    method = cast(str, scope["method"]).upper()
    path = cast(str, scope["path"])
    if method not in _MUTATING_METHODS:
        return None
    if path.endswith("/upload"):
        return upload_limit
    if path.startswith("/v1/") or path == "/task-types":
        return default_limit
    return None


def parse_content_length(scope: Scope) -> int | None:
    """Parse the Content-Length header when present."""

    for key, value in cast(list[tuple[bytes, bytes]], scope.get("headers", [])):
        if key.lower() != b"content-length":
            continue
        try:
            return int(value.decode("latin-1"))
        except ValueError:
            return None
    return None


def payload_depth(value: Any) -> int:
    """Return the maximum nested container depth for one JSON-like value."""

    if isinstance(value, Mapping):
        if not value:
            return 1
        return 1 + max(payload_depth(item) for item in value.values())
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if not value:
            return 1
        return 1 + max(payload_depth(item) for item in value)
    return 0


def parse_json_payload_depth(body: bytes) -> int | None:
    """Return the nesting depth for a JSON body, or None when not JSON."""

    if not body:
        return None
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return None
    return payload_depth(payload)


class ContentSizeLimitMiddleware:
    """Reject oversized or deeply nested JSON request bodies."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        default_limit: int = DEFAULT_CONTENT_SIZE_LIMIT,
        upload_limit: int = UPLOAD_CONTENT_SIZE_LIMIT,
        max_payload_depth: int = MAX_PAYLOAD_DEPTH,
    ) -> None:
        self.app = app
        self.default_limit = default_limit
        self.upload_limit = upload_limit
        self.max_payload_depth = max_payload_depth

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limit = resolve_content_size_limit(
            scope,
            default_limit=self.default_limit,
            upload_limit=self.upload_limit,
        )
        if limit is None:
            await self.app(scope, receive, send)
            return

        content_length = parse_content_length(scope)
        if content_length is not None and content_length > limit:
            await self._send_payload_too_large(
                scope,
                send,
                limit=limit,
                actual_size=content_length,
            )
            return

        buffered_body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return

            chunk = cast(bytes, message.get("body", b""))
            buffered_body.extend(chunk)
            if len(buffered_body) > limit:
                await self._send_payload_too_large(
                    scope,
                    send,
                    limit=limit,
                    actual_size=len(buffered_body),
                )
                return

            if not cast(bool, message.get("more_body", False)):
                break

        body = bytes(buffered_body)
        body_depth = parse_json_payload_depth(body)
        if body_depth is not None and body_depth > self.max_payload_depth:
            await self._send_payload_too_deep(
                scope,
                send,
                max_depth=self.max_payload_depth,
                actual_depth=body_depth,
            )
            return

        delivered = False

        async def replay_receive() -> Message:
            nonlocal delivered
            if delivered:
                return {"type": "http.request", "body": b"", "more_body": False}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay_receive, send)

    async def _send_payload_too_large(
        self,
        scope: Scope,
        send: Send,
        *,
        limit: int,
        actual_size: int,
    ) -> None:
        response = JSONResponse(
            status_code=413,
            content=error_payload(
                status_code=413,
                message="Payload exceeds request body limit",
                details={"actual_bytes": actual_size, "limit_bytes": limit},
            ),
        )
        await response(scope, self._empty_receive, send)

    async def _send_payload_too_deep(
        self,
        scope: Scope,
        send: Send,
        *,
        max_depth: int,
        actual_depth: int,
    ) -> None:
        response = JSONResponse(
            status_code=422,
            content=error_payload(
                status_code=422,
                message="Request payload exceeds maximum nesting depth",
                details={"actual_depth": actual_depth, "max_depth": max_depth},
            ),
        )
        await response(scope, self._empty_receive, send)

    async def _empty_receive(self) -> Message:
        return {"type": "http.request", "body": b"", "more_body": False}
