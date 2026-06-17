"""Structured API error helpers."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .logging_utils import get_logger

logger = get_logger()


def _default_action(status_code: int) -> str:
    if status_code == 400:
        return "Check the request and try again."
    if status_code == 401:
        return "Sign in or refresh credentials."
    if status_code == 403:
        return "Use an account with permission for this action."
    if status_code == 404:
        return "Refresh the page or select another item."
    if status_code == 409:
        return "Wait for the current operation to finish, then retry."
    if status_code == 413:
        return "Upload a smaller file."
    if status_code == 422:
        return "Fix the highlighted fields and try again."
    if status_code == 503:
        return "Check service configuration or retry later."
    return "Retry later or check backend logs."


def error_payload(
    *,
    status_code: int,
    code: str,
    message: str,
    error_type: str,
    recoverable: bool,
    action: str | None = None,
    details: Any = None,
    path: str | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "error": {
            "code": code,
            "type": error_type,
            "message": message,
            "recoverable": recoverable,
            "action": action or _default_action(status_code),
        }
    }
    if details is not None:
        payload["error"]["details"] = details
    if path is not None:
        payload["error"]["path"] = path
    return payload


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict):
        code = str(detail.get("code") or f"HTTP_{exc.status_code}")
        message = str(detail.get("message") or detail.get("detail") or "Request failed.")
        recoverable = bool(detail.get("recoverable", exc.status_code < 500))
        action = detail.get("action")
        details = detail.get("details")
    else:
        code = f"HTTP_{exc.status_code}"
        message = str(detail)
        recoverable = exc.status_code < 500 or exc.status_code == 503
        action = None
        details = None

    log_level = "error" if exc.status_code >= 500 else "info"
    getattr(logger, log_level)(
        "HTTP exception",
        extra={
            "path": request.url.path,
            "status_code": exc.status_code,
            "error_code": code,
            "recoverable": recoverable,
        },
    )

    return JSONResponse(
        status_code=exc.status_code,
        content=error_payload(
            status_code=exc.status_code,
            code=code,
            message=message,
            error_type="http_error",
            recoverable=recoverable,
            action=action,
            details=details,
            path=request.url.path,
        ),
        headers=getattr(exc, "headers", None),
    )


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    logger.info(
        "Request validation failed",
        extra={"path": request.url.path, "status_code": 422, "details": exc.errors()},
    )
    return JSONResponse(
        status_code=422,
        content=error_payload(
            status_code=422,
            code="VALIDATION_ERROR",
            message="Request validation failed.",
            error_type="validation_error",
            recoverable=True,
            details=exc.errors(),
            path=request.url.path,
        ),
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.error(
        "Unhandled exception",
        exc_info=(type(exc), exc, exc.__traceback__),
        extra={"path": request.url.path, "status_code": 500},
    )
    return JSONResponse(
        status_code=500,
        content=error_payload(
            status_code=500,
            code="INTERNAL_ERROR",
            message=f"{type(exc).__name__}: {exc}",
            error_type="internal_error",
            recoverable=False,
            path=request.url.path,
        ),
    )
