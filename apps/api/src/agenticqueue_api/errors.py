"""Structured API error helpers and exception handlers."""

from __future__ import annotations

from typing import Any, NoReturn, cast

from fastapi import FastAPI, HTTPException, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from agenticqueue_api.db import StatementTimeoutError

HTTP_413_STATUS = getattr(
    status,
    "HTTP_413_CONTENT_TOO_LARGE",
    status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
)
HTTP_422_STATUS = getattr(
    status,
    "HTTP_422_UNPROCESSABLE_CONTENT",
    status.HTTP_422_UNPROCESSABLE_ENTITY,
)

ERROR_CODE_BY_STATUS = {
    status.HTTP_400_BAD_REQUEST: "validation_failed",
    status.HTTP_401_UNAUTHORIZED: "auth_failed",
    status.HTTP_403_FORBIDDEN: "forbidden",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_409_CONFLICT: "conflict",
    HTTP_413_STATUS: "validation_failed",
    HTTP_422_STATUS: "validation_failed",
    status.HTTP_429_TOO_MANY_REQUESTS: "rate_limited",
    status.HTTP_504_GATEWAY_TIMEOUT: "server_error",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "server_error",
}


def error_payload(
    *,
    status_code: int,
    message: str,
    error_code: str | None = None,
    details: Any = None,
) -> dict[str, Any]:
    """Build the standard API error payload."""

    code = error_code or ERROR_CODE_BY_STATUS.get(status_code, "server_error")
    return {
        "error": {
            "code": code,
            "message": message,
            "details": details,
        },
        # Compatibility aliases for pre-hardening tests and clients.
        "error_code": code,
        "message": message,
        "details": details,
    }


def raise_api_error(
    status_code: int,
    message: str,
    *,
    error_code: str | None = None,
    details: Any = None,
) -> NoReturn:
    """Raise a structured HTTPException."""

    raise HTTPException(
        status_code=status_code,
        detail=error_payload(
            status_code=status_code,
            message=message,
            error_code=error_code,
            details=details,
        ),
    )


def _normalize_http_exception(exc: HTTPException) -> dict[str, Any]:
    detail = exc.detail
    if isinstance(detail, dict) and {
        "error",
        "error_code",
        "message",
        "details",
    }.issubset(detail.keys()):
        return detail
    if isinstance(detail, dict) and "error" in detail:
        error = detail["error"]
        if isinstance(error, dict):
            return error_payload(
                status_code=exc.status_code,
                message=str(error.get("message", "Request failed")),
                error_code=(
                    None if error.get("code") is None else str(error.get("code"))
                ),
                details=error.get("details"),
            )
    if isinstance(detail, dict) and {
        "error_code",
        "message",
        "details",
    }.issubset(detail.keys()):
        return detail
    if isinstance(detail, str):
        return error_payload(status_code=exc.status_code, message=detail)
    return error_payload(
        status_code=exc.status_code,
        message="Request failed",
        details=detail,
    )


async def handle_http_exception(request: Request, exc: Exception) -> JSONResponse:
    """Render HTTPException values in the standard error shape."""

    del request
    http_exc = cast(HTTPException, exc)
    return JSONResponse(
        status_code=http_exc.status_code,
        content=_normalize_http_exception(http_exc),
        headers=http_exc.headers,
    )


async def handle_validation_exception(request: Request, exc: Exception) -> JSONResponse:
    """Render request validation failures in the standard error shape."""

    del request
    validation_exc = cast(RequestValidationError, exc)
    return JSONResponse(
        status_code=HTTP_422_STATUS,
        content=error_payload(
            status_code=HTTP_422_STATUS,
            message="Request validation failed",
            details=validation_exc.errors(),
        ),
    )


async def handle_statement_timeout_exception(
    request: Request,
    exc: Exception,
) -> JSONResponse:
    """Render statement timeout failures as HTTP 504s."""

    del request
    timeout_exc = cast(StatementTimeoutError, exc)
    return JSONResponse(
        status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        content=error_payload(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            message="Database statement timed out",
            details={
                "endpoint": timeout_exc.endpoint,
                "sql_fingerprint": timeout_exc.sql_fingerprint,
                "elapsed_ms": timeout_exc.elapsed_ms,
                "timeout_ms": timeout_exc.timeout_ms,
            },
        ),
    )


def install_exception_handlers(app: FastAPI) -> None:
    """Install the API exception handlers on a FastAPI app."""

    app.add_exception_handler(HTTPException, handle_http_exception)
    app.add_exception_handler(StarletteHTTPException, handle_http_exception)
    app.add_exception_handler(RequestValidationError, handle_validation_exception)
    app.add_exception_handler(
        StatementTimeoutError,
        handle_statement_timeout_exception,
    )
