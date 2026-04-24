"""Double-submit CSRF middleware for cookie-authenticated requests."""

from __future__ import annotations

from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from agenticqueue_api.auth import SESSION_COOKIE_NAME, verify_session_csrf_token
from agenticqueue_api.errors import error_payload

CSRF_COOKIE_NAME = "csrf-token"
CSRF_HEADER_NAME = "X-CSRF-Token"
MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
CSRF_EXEMPT_PATHS = frozenset({"/v1/auth/login"})


class CsrfDoubleSubmitMiddleware(BaseHTTPMiddleware):
    """Require a CSRF header matching the non-HttpOnly CSRF cookie."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if request.method not in MUTATING_METHODS:
            return await call_next(request)
        if request.url.path in CSRF_EXEMPT_PATHS:
            return await call_next(request)
        authorization = request.headers.get("Authorization", "")
        if authorization.lower().startswith("bearer "):
            return await call_next(request)
        if SESSION_COOKIE_NAME not in request.cookies:
            return await call_next(request)

        cookie_token = request.cookies.get(CSRF_COOKIE_NAME)
        header_token = request.headers.get(CSRF_HEADER_NAME)
        if not cookie_token or not header_token or cookie_token != header_token:
            return JSONResponse(
                status_code=403,
                content=error_payload(
                    status_code=403,
                    message="CSRF token mismatch",
                    error_code="forbidden",
                ),
            )

        session_factory = request.app.state.session_factory
        session = session_factory()
        try:
            if not verify_session_csrf_token(
                session,
                session_token=request.cookies.get(SESSION_COOKIE_NAME),
                csrf_token=header_token,
            ):
                return JSONResponse(
                    status_code=403,
                    content=error_payload(
                        status_code=403,
                        message="CSRF token mismatch",
                        error_code="forbidden",
                    ),
                )
        finally:
            session.rollback()
            session.close()

        return await call_next(request)
