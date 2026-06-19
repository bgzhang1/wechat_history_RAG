"""Small JSONL logging utility for frontend diagnostics."""

from __future__ import annotations

import json
import logging
import os
import threading
import traceback
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .redaction import redact_data, redact_text


load_dotenv()
LOG_PATH = Path(os.getenv("BACKEND_LOG_FILE", str(Path("runtime") / "backend.log.jsonl")))
LOGGER_NAME = "wechat_backend"

_configured = False
_lock = threading.RLock()

_STANDARD_RECORD_KEYS = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "module",
    "msecs",
    "message",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
}

_PUBLIC_MESSAGE_LABELS = {
    "Backend started": "后端已启动",
    "HTTP exception": "HTTP 异常",
    "Request validation failed": "请求字段校验失败",
    "Unhandled exception": "未处理异常",
    "Ingest file uploaded": "导入文件已上传",
    "Ingest task queued": "导入任务已排队",
    "Ingest subprocess started": "导入子进程已启动",
    "Ingest task completed": "导入任务已完成",
    "Ingest task failed": "导入任务失败",
    "Ingest task crashed": "导入任务异常退出",
    "Ingest cancel requested": "已请求取消导入",
    "Ingest task cancelled": "导入任务已取消",
    "Runtime settings load failed": "运行时设置加载失败",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _log_max_bytes() -> int:
    try:
        mb = int(os.getenv("BACKEND_LOG_MAX_MB", "10"))
    except ValueError:
        mb = 10
    return max(1, mb) * 1024 * 1024


def _rotate_if_needed(path: Path) -> None:
    if not path.exists() or path.stat().st_size < _log_max_bytes():
        return
    rotated = path.with_suffix(path.suffix + ".1")
    rotated.unlink(missing_ok=True)
    path.replace(rotated)


class JsonLineHandler(logging.Handler):
    def __init__(self, path: Path):
        super().__init__(level=logging.DEBUG)
        self.path = path

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            payload: dict[str, Any] = {
                "timestamp": _now(),
                "level": record.levelname.lower(),
                "logger": record.name,
                "message": redact_text(record.getMessage(), limit=4000),
                "module": record.module,
                "function": record.funcName,
                "line": record.lineno,
            }

            extras = {
                key: value
                for key, value in record.__dict__.items()
                if key not in _STANDARD_RECORD_KEYS and not key.startswith("_")
            }
            if extras:
                payload["details"] = redact_data(extras, string_limit=4000)

            if record.exc_info:
                payload["traceback"] = redact_text(
                    "".join(traceback.format_exception(*record.exc_info)),
                    limit=20000,
                    collapse_whitespace=False,
                )

            with _lock:
                _rotate_if_needed(self.path)
                handle = self.path.open("a", encoding="utf-8")
                with handle:
                    handle.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
        except Exception:
            self.handleError(record)


def configure_logging() -> logging.Logger:
    global _configured
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = True
    if _configured:
        return logger

    with _lock:
        if not any(isinstance(handler, JsonLineHandler) for handler in logger.handlers):
            logger.addHandler(JsonLineHandler(LOG_PATH))
        _configured = True
    return logger


def get_logger() -> logging.Logger:
    return configure_logging()


def read_recent_logs(level: str = "error", limit: int = 100) -> list[dict[str, Any]]:
    levels = {"debug": 10, "info": 20, "warning": 30, "error": 40}
    min_level = levels.get(str(level or "").lower())
    if min_level is None:
        raise ValueError("level must be one of: debug, info, warning, error")

    try:
        safe_limit = int(limit)
    except (TypeError, ValueError, OverflowError):
        safe_limit = 100
    safe_limit = max(1, min(safe_limit, 1000))
    log_paths = [LOG_PATH, LOG_PATH.with_suffix(LOG_PATH.suffix + ".1")]
    if not any(path.exists() for path in log_paths):
        return []

    records: deque[dict[str, Any]] = deque(maxlen=safe_limit)
    with _lock:
        for path in log_paths:
            if not path.exists():
                continue
            for line in _iter_log_lines_newest_first(path):
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                record_level = levels.get(str(record.get("level", "")).lower(), 0)
                if record_level >= min_level:
                    records.append(record)
                    if len(records) >= safe_limit:
                        break
            if len(records) >= safe_limit:
                break

    return [public_log_record(record) for record in records]


def _iter_log_lines_newest_first(path: Path, chunk_size: int = 64 * 1024):
    def decode_line(raw_line: bytes) -> str:
        return raw_line.rstrip(b"\r").decode("utf-8", errors="replace")

    chunk_size = max(1, int(chunk_size))
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        position = handle.tell()
        buffer = b""
        while position > 0:
            read_size = min(chunk_size, position)
            position -= read_size
            handle.seek(position)
            data = handle.read(read_size) + buffer
            lines = data.split(b"\n")
            buffer = lines[0]
            for raw_line in reversed(lines[1:]):
                if raw_line:
                    yield decode_line(raw_line)
        if buffer:
            yield decode_line(buffer)


def public_log_record(record: dict[str, Any]) -> dict[str, Any]:
    safe = dict(record)
    if "message" in safe:
        message = redact_text(safe["message"], limit=1000)
        safe["message"] = _PUBLIC_MESSAGE_LABELS.get(message, message)
    if "details" in safe:
        details = redact_data(safe["details"], string_limit=1000)
        if isinstance(details, dict):
            details.pop("taskName", None)
        safe["details"] = details
    if "traceback" in safe:
        safe["traceback"] = redact_text(safe["traceback"], limit=6000, collapse_whitespace=False)
    return safe
