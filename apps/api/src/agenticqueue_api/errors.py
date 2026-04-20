"""Structured API error helpers and exception handlers."""

from __future__ import annotations

from typing import Any, NoReturn, cast

from fastapi import FastAPI, HTTPException, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.requests import Request

ERROR_CODE_BY_STATUS = {
    status.HTTP_400_BAD_REQUEST: "bad_request",
    status.HTTP_401_UNAUTHORIZED: "unauthorized",
    status.HTTP_403_FORBIDDEN: "forbidden",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_409_CONFLICT: "conflict",
    status.HTTP_422_UNPROCESSABLE_CONTENT: "validation_error",
    status.HTTP_500_INTERNAL_SERVER_ERROR: "internal_server_error",
}


def error_payload(
    *,
    status_code: int,
    message: str,
    error_code: str | None = None,
    details: Any = None,
) -> dict[str, Any]:
    """Build the standard API error payload."""

    return {
        "error_code": error_code or ERROR_CODE_BY_STATUS.get(status_code, "error"),
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


async def handle_validation_exception(
    request: Request, exc: Exception
) -> JSONResponse:
    """Render request validation failures in the standard error shape."""

    del request
    validation_exc = cast(RequestValidationError, exc)
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content=error_payload(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            message="Request validation failed",
            details=validation_exc.errors(),
        ),
    )


def install_exception_handlers(app: FastAPI) -> None:
    """Install the API exception handlers on a FastAPI app."""

    app.add_exception_handler(HTTPException, handle_http_exception)
    app.add_exception_handler(RequestValidationError, handle_validation_exception)
