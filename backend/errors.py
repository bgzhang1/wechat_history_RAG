"""Structured API error helpers."""

from __future__ import annotations

from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .logging_utils import get_logger
from .redaction import redact_data, redact_text

logger = get_logger()


def _default_action(status_code: int) -> str:
    if status_code == 400:
        return "请检查请求内容后重试。"
    if status_code == 401:
        return "请重新登录或刷新凭据。"
    if status_code == 403:
        return "请使用有权限的账号执行此操作。"
    if status_code == 404:
        return "请刷新页面或选择其他项目。"
    if status_code == 409:
        return "请等待当前操作结束后重试。"
    if status_code == 413:
        return "请上传更小的文件。"
    if status_code == 422:
        return "请修正字段后重试。"
    if status_code == 503:
        return "请检查服务配置，或稍后重试。"
    return "请稍后重试，或查看后端日志。"


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
    safe_action = redact_text(action) if action else _default_action(status_code)
    payload: dict[str, Any] = {
        "error": {
            "code": code,
            "type": error_type,
            "message": redact_text(message),
            "recoverable": recoverable,
            "action": safe_action,
        }
    }
    if details is not None:
        payload["error"]["details"] = redact_data(details)
    if path is not None:
        payload["error"]["path"] = path
    return payload


def _public_validation_details(errors: list[dict[str, Any]]) -> Any:
    public = redact_data(errors)
    if not isinstance(public, list):
        return public
    sanitized: list[Any] = []
    for item in public:
        if isinstance(item, dict):
            cleaned = dict(item)
            if "input" in cleaned:
                cleaned["input"] = "[omitted]"
            sanitized.append(cleaned)
        else:
            sanitized.append(item)
    return sanitized


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail
    if isinstance(detail, dict):
        code = str(detail.get("code") or f"HTTP_{exc.status_code}")
        message = redact_text(detail.get("message") or detail.get("detail") or "请求失败。")
        recoverable = bool(detail.get("recoverable", exc.status_code < 500))
        action = detail.get("action")
        details = redact_data(detail.get("details")) if detail.get("details") is not None else None
    else:
        code = f"HTTP_{exc.status_code}"
        message = redact_text(detail)
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
    public_details = _public_validation_details(exc.errors())
    logger.info(
        "Request validation failed",
        extra={"path": request.url.path, "status_code": 422, "details": public_details},
    )
    return JSONResponse(
        status_code=422,
        content=error_payload(
            status_code=422,
            code="VALIDATION_ERROR",
            message="请求字段校验失败。",
            error_type="validation_error",
            recoverable=True,
            details=public_details,
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
            message="后端服务发生内部错误，请查看错误日志获取详情。",
            error_type="internal_error",
            recoverable=False,
            path=request.url.path,
        ),
    )
