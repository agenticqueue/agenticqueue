"""Request ID propagation middleware for the REST surface."""

from __future__ import annotations

import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

REQUEST_ID_HEADER = "X-Request-Id"
TRACE_ID_HEADER = "X-Trace-Id"


def resolve_request_id(request: Request) -> str:
    """Return the request ID for one inbound request."""

    for header_name in (REQUEST_ID_HEADER, TRACE_ID_HEADER):
        header_value = request.headers.get(header_name)
        if header_value is not None and header_value.strip():
            return header_value.strip()
    return str(uuid.uuid4())


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Attach a stable request ID to request state and response headers."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = resolve_request_id(request)
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers[REQUEST_ID_HEADER] = request_id
        return response
