"""Small JSONL logging utility for frontend diagnostics."""

from __future__ import annotations

import json
import logging
import os
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


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
                "message": record.getMessage(),
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
                payload["details"] = extras

            if record.exc_info:
                payload["traceback"] = "".join(traceback.format_exception(*record.exc_info))

            with _lock, self.path.open("a", encoding="utf-8") as handle:
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
    min_level = levels.get(level.lower())
    if min_level is None:
        raise ValueError("level must be one of: debug, info, error")

    safe_limit = max(1, min(int(limit), 1000))
    if not LOG_PATH.exists():
        return []

    records: list[dict[str, Any]] = []
    with _lock, LOG_PATH.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                continue
            record_level = levels.get(str(record.get("level", "")).lower(), 0)
            if record_level >= min_level:
                records.append(record)

    return records[-safe_limit:]
