"""Ingest router - safe file upload and cancellable background tasks."""

from __future__ import annotations

import asyncio
import json
import os
import re
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Body, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field, field_validator

from core import store
from core.llm import embed_configured, summary_config_status
from core.parser import PARSER_VERSION, file_scope_for_path, is_weflow_export, stable_upload_scope

from ..logging_utils import get_logger
from ..redaction import public_exception_message, redact_text
from .params import query_int


router = APIRouter(prefix="/api/ingest", tags=["ingest"])
ws_router = APIRouter(prefix="/api", tags=["ingest"])
logger = get_logger()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_ROOT = PROJECT_ROOT / "local"
UPLOAD_ROOT = LOCAL_ROOT / "uploads"


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    return max(minimum, value)


MAX_UPLOAD_BYTES = _env_int("INGEST_MAX_UPLOAD_MB", 512) * 1024 * 1024
MAX_TASKS = _env_int("INGEST_MAX_TASKS", 100)
MAX_TASK_LOG_LINES = _env_int("INGEST_MAX_TASK_LOG_LINES", 5000)
MAX_TASK_LOG_LINE_CHARS = _env_int("INGEST_MAX_TASK_LOG_LINE_CHARS", 4000)
TASK_LOG_VIEW_CHARS = 30000
TASK_LOG_TAIL_CHARS = 2000
TASK_PROGRESS_LOG_CHARS = 20000
MAX_INGEST_TARGET_CHARS = 2048
ETA_STALE_PROGRESS_SECONDS = _env_int("INGEST_ETA_STALE_SECONDS", 120, minimum=10)
PROGRESS_PREFIX = "__INGEST_PROGRESS__ "

_tasks: dict[str, dict[str, Any]] = {}
_tasks_lock = threading.RLock()
_process_lock = threading.Lock()
IngestMode = Literal["incremental", "full", "rebuild", "fts", "chunks", "summary", "embeddings", "vector"]
INDEX_ONLY_MODES = {"fts", "chunks", "summary", "embeddings", "vector"}
SESSION_CHUNK_REQUIRED_MODES = {"summary", "embeddings", "vector"}
INGEST_MODE_ARGS: dict[str, list[str]] = {
    "incremental": [],
    "full": ["--force-import"],
    "rebuild": ["--force-rebuild"],
    "fts": ["--skip-import", "--force-fts"],
    "chunks": ["--skip-import", "--force-chunks"],
    "summary": ["--skip-import", "--force-summary"],
    "embeddings": ["--skip-import", "--force-embeddings"],
    "vector": ["--skip-import", "--force-embeddings"],
}


class UploadResponse(BaseModel):
    upload_id: str
    filename: str
    size: int
    message: str


class LocalFileItem(BaseModel):
    file_id: str
    filename: str
    size: int
    modified_at: str
    source: str
    upload_id: str | None = None
    ingest_status: str = "never"
    ingest_status_reason: str | None = None
    last_ingested_at: str | None = None
    ingest_total: int | None = None
    ingest_included: int | None = None
    ingest_changed: int | None = None
    ingest_inserted: int | None = None
    session_chunks: int | None = None
    missing_summary_chunks: int | None = None
    missing_vector_chunks: int | None = None
    task_id: str | None = None
    task_status: str | None = None
    task_mode: str | None = None


class IngestStartRequest(BaseModel):
    upload_id: str | None = Field(
        default=None,
        max_length=MAX_INGEST_TARGET_CHARS,
        description="ID returned by /api/ingest/upload",
    )
    file_id: str | None = Field(
        default=None,
        max_length=MAX_INGEST_TARGET_CHARS,
        description="Relative file ID returned by /api/ingest/files",
    )
    file_path: str | None = Field(
        default=None,
        max_length=MAX_INGEST_TARGET_CHARS,
        description="Legacy local path under local/",
    )
    mode: IngestMode = Field(
        default="incremental",
        description="Ingest mode: incremental/full/rebuild/fts/chunks/summary/embeddings/vector",
    )

    @field_validator("upload_id", "file_id", "file_path", mode="before")
    @classmethod
    def normalize_target(cls, value: object) -> object:
        return _normalize_target_value(value)


class IngestFileDeleteRequest(BaseModel):
    upload_id: str | None = Field(
        default=None,
        max_length=MAX_INGEST_TARGET_CHARS,
        description="ID returned by /api/ingest/upload",
    )
    file_id: str | None = Field(
        default=None,
        max_length=MAX_INGEST_TARGET_CHARS,
        description="Relative file ID returned by /api/ingest/files",
    )

    @field_validator("upload_id", "file_id", mode="before")
    @classmethod
    def normalize_target(cls, value: object) -> object:
        return _normalize_target_value(value)


def _normalize_target_value(value: object) -> object:
    if value is None or not isinstance(value, str):
        return value
    return value.strip() or None


class TaskStatus(BaseModel):
    task_id: str
    status: str
    logs: str
    created_at: str
    updated_at: str
    file_id: str | None = None
    mode: str = "incremental"
    error: str | None = None
    error_info: dict[str, Any] | None = None
    can_cancel: bool = False
    progress: int = 0
    stage: str = "starting"
    message: str = ""
    eta: int | None = None
    log_tail: str = ""


class TaskProgress(BaseModel):
    task_id: str
    status: str
    progress: int
    stage: str
    message: str = ""
    eta: int | None = None
    updated_at: str
    file_id: str | None = None
    mode: str = "incremental"
    error: str | None = None
    error_info: dict[str, Any] | None = None
    can_cancel: bool = False
    log_tail: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _timestamp(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _task_update(task_id: str, **fields: Any) -> None:
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task is None:
            return
        task.update(fields)
        task["updated_at"] = _now()


def _task_log_prefix(task: dict[str, Any], limit: int) -> str:
    lines = task.get("logs", [])
    parts: list[str] = []
    total = 0
    for line in lines:
        text = str(line)
        remaining = limit - total
        if remaining <= 0:
            break
        if len(text) > remaining:
            parts.append(text[:remaining])
            break
        parts.append(text)
        total += len(text)
    return "".join(parts)


def _task_log_tail(task: dict[str, Any], limit: int) -> str:
    lines = task.get("logs", [])
    parts: list[str] = []
    total = 0
    for line in reversed(lines):
        text = str(line)
        remaining = limit - total
        if remaining <= 0:
            break
        if len(text) > remaining:
            parts.append(text[-remaining:])
            break
        parts.append(text)
        total += len(text)
    return "".join(reversed(parts))


def _task_snapshot(task_id: str, include_logs: bool = True) -> TaskStatus:
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="导入任务不存在。")

        status = task["status"]
        progress_logs = _task_log_tail(task, TASK_PROGRESS_LOG_CHARS)
        progress, stage = _task_progress_state(task, progress_logs, status)
        logs = (
            redact_text(
                _task_log_prefix(task, TASK_LOG_VIEW_CHARS),
                limit=TASK_LOG_VIEW_CHARS,
                collapse_whitespace=False,
            )
            if include_logs
            else ""
        )
        return TaskStatus(
            task_id=task_id,
            status=status,
            logs=logs,
            created_at=task["created_at"],
            updated_at=task["updated_at"],
            file_id=task.get("file_id"),
            mode=str(task.get("mode") or "incremental"),
            error=_task_public_error(task),
            error_info=_task_error_info(task),
            can_cancel=status in {"running", "cancel_requested"},
            progress=progress,
            stage=stage,
            message=_task_progress_message(task),
            eta=_task_eta(task["created_at"], task["updated_at"], status, progress),
            log_tail=(
                redact_text(
                    _task_log_tail(task, TASK_LOG_TAIL_CHARS),
                    limit=TASK_LOG_TAIL_CHARS,
                    collapse_whitespace=False,
                )
                if include_logs
                else ""
            ),
        )


def _parse_progress_from_logs(logs: str, status: str) -> tuple[int, str]:
    if status == "completed":
        return 100, "completed"
    if status == "cancelled":
        return 0, "cancelled"
    if status == "error":
        return 0, "error"

    progress = 5
    stage = "starting"
    if "发现" in logs or "JSON" in logs:
        progress, stage = max(progress, 10), "parsing"
    if "消息入库完成" in logs or "FTS" in logs:
        progress, stage = max(progress, 35), "indexing"
    if "会话分块" in logs or "复用已有会话分块" in logs:
        progress, stage = max(progress, 45), "chunking"
    if "摘要" in logs:
        progress, stage = max(progress, 55), "summary"
    if "embedding" in logs or "向量" in logs:
        progress, stage = max(progress, 75), "embedding"

    summary_match = re.findall(r"摘要\s+(\d+)/(\d+)", logs)
    if summary_match:
        done, total = map(int, summary_match[-1])
        if total:
            progress = max(progress, 55 + int((done / total) * 15))
            stage = "summary"

    embed_match = re.findall(r"embedding\s+(?:完成|等待中：完成)\s+(\d+)/(\d+)", logs)
    if embed_match:
        done, total = map(int, embed_match[-1])
        if total:
            progress = max(progress, 75 + int((done / total) * 20))
            stage = "embedding"

    if "摘要完成" in logs:
        progress, stage = max(progress, 70), "summary"
    if "向量索引完成" in logs or "索引更新完成" in logs or "无需生成摘要或向量" in logs:
        progress, stage = 100, "completed"

    if status == "cancel_requested":
        stage = "cancelling"

    return min(progress, 99 if status in {"running", "cancel_requested"} else 100), stage


def _task_progress_state(task: dict[str, Any], logs: str, status: str) -> tuple[int, str]:
    event = task.get("progress_event")
    if isinstance(event, dict):
        progress = _clamp_progress(event.get("progress", 0))
        stage = str(event.get("stage") or status or "starting")
        if status == "completed":
            return 100, "completed"
        if status == "cancelled":
            return progress, "cancelled"
        if status == "error":
            return progress, "error"
        if status == "cancel_requested":
            return min(progress, 99), "cancelling"
        return min(progress, 99), stage
    return _parse_progress_from_logs(logs, status)


def _task_progress_message(task: dict[str, Any]) -> str:
    event = task.get("progress_event")
    if not isinstance(event, dict):
        return ""
    return str(event.get("message") or "")


def _task_public_error(task: dict[str, Any]) -> str | None:
    error = task.get("error")
    if error is None:
        return None
    return redact_text(error, limit=500)


def _task_error_info(task: dict[str, Any]) -> dict[str, Any] | None:
    error = _task_public_error(task)
    if not error:
        return None
    return_code = task.get("return_code")
    info = _classify_ingest_error(
        error,
        mode=str(task.get("mode") or "incremental"),
        return_code=return_code if isinstance(return_code, int) else None,
    )
    info["message"] = error
    if return_code is not None:
        info["return_code"] = return_code
    return info


def _classify_ingest_error(error: str, *, mode: str = "incremental", return_code: int | None = None) -> dict[str, Any]:
    text = error.lower()
    info: dict[str, Any] = {
        "code": "INGEST_FAILED",
        "type": "导入失败",
        "action": "查看下方日志尾部，确认源文件、模型配置或索引环境后重试。",
    }
    if return_code is not None:
        info["return_code"] = return_code

    rules: list[tuple[tuple[str, ...], str, str, str]] = [
        (
            ("already", "正在运行", "上一", "冲突", "409"),
            "INGEST_CONFLICT",
            "任务冲突",
            "已有导入任务占用队列，请等待完成或取消当前任务后再重试。",
        ),
        (
            ("embed_base_url", "embed_api_key", "embed_model", "embedding model", "sqlite-vec"),
            "EMBED_CONFIG_MISSING",
            "向量配置缺失",
            "在设置的高级设置中检查 Embedding Base URL、模型和线程批次；API Key 仍需在本地环境变量中配置。",
        ),
        (
            ("summary_model", "summary", "摘要模型", "仅摘要"),
            "SUMMARY_CONFIG_MISSING" if mode == "summary" else "SUMMARY_ERROR",
            "摘要配置或生成失败",
            "在设置中检查摘要模型、聊天 Base URL 和摘要线程批次；如果涉及 API Key，请检查本地环境变量。",
        ),
        (
            ("rate limit", "429", "too many requests", "限流", "quota"),
            "MODEL_RATE_LIMIT",
            "模型限流",
            "降低线程数或批次大小，等待额度恢复后重试。",
        ),
        (
            ("timeout", "timed out", "超时", "readtimeout"),
            "MODEL_TIMEOUT",
            "模型请求超时",
            "适当增大超时时间，或降低线程数、批次大小后重试。",
        ),
        (
            ("badrequest", "bad request", "unprocessable", "400", "422", "rejected", "拒绝"),
            "MODEL_REQUEST_REJECTED",
            "模型请求被拒绝",
            "检查 Base URL、模型名称和输入长度限制，必要时调小摘要或向量批次。",
        ),
        (
            ("jsondecodeerror", "invalid json", "不是合法 json", "json decode", "expecting value"),
            "INVALID_JSON",
            "JSON 格式错误",
            "确认文件是完整的 WeFlow JSON 导出，没有被截断或手工修改。",
        ),
        (
            ("weflow", "微信聊天导出"),
            "INVALID_WEFLOW_EXPORT",
            "导出格式不匹配",
            "请使用 WeFlow 导出的微信聊天 JSON 原文件再导入。",
        ),
        (
            ("permission", "access is denied", "permission denied", "权限"),
            "FILE_PERMISSION",
            "文件权限问题",
            "确认后端进程可以读取该 JSON 文件，并且文件没有被其他程序锁定。",
        ),
        (
            ("memory", "out of memory", "no space", "resource", "磁盘", "空间"),
            "RESOURCE_EXHAUSTED",
            "本机资源不足",
            "释放内存或磁盘空间后重试；大文件可先降低并发和批次大小。",
        ),
        (
            ("sqlite", "vector", "向量索引", "vec0", "database"),
            "VECTOR_INDEX_ERROR",
            "索引或数据库错误",
            "检查数据库健康状态和 sqlite-vec 可用性，必要时重建索引。",
        ),
    ]
    for keywords, code, type_label, action in rules:
        if any(keyword in text for keyword in keywords):
            info.update({"code": code, "type": type_label, "action": action})
            return info
    return info


def _task_failure_error(task_id: str, return_code: int) -> str:
    fallback = f"ingest exited with code {return_code}"
    detail = _task_failure_detail(task_id)
    return f"{fallback}: {detail}" if detail else fallback


def _task_failure_detail(task_id: str) -> str:
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task is None:
            return ""

        progress_message = _failure_progress_message(task.get("progress_event"))
        if progress_message:
            return progress_message

        log_tail = _task_log_tail(task, TASK_LOG_TAIL_CHARS)

    for raw_line in reversed(log_tail.splitlines()):
        line = raw_line.strip()
        if not line or line.startswith(PROGRESS_PREFIX) or line == "...[line truncated]":
            continue
        detail = redact_text(line, limit=500)
        if detail:
            return detail
    return ""


def _failure_progress_message(event: Any) -> str:
    if not isinstance(event, dict):
        return ""
    if str(event.get("stage") or "").strip().lower() != "error":
        return ""
    return redact_text(event.get("message") or "", limit=500)


def _clamp_progress(value: Any) -> int:
    try:
        progress = int(value)
    except (TypeError, ValueError):
        return 0
    return max(0, min(progress, 100))


def _parse_progress_event(text: str) -> dict[str, Any] | None:
    if not text.startswith(PROGRESS_PREFIX):
        return None
    try:
        payload = json.loads(text[len(PROGRESS_PREFIX) :])
    except json.JSONDecodeError:
        return None
    if not isinstance(payload, dict):
        return None
    stage = str(payload.get("stage") or "").strip()
    if not stage:
        return None
    return {
        "stage": stage[:80],
        "progress": _clamp_progress(payload.get("progress", 0)),
        "message": redact_text(payload.get("message") or "", limit=500),
    }


def _parse_task_time(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _estimate_eta(created_at: str, progress: int) -> int | None:
    if progress <= 0 or progress >= 100:
        return None
    started = _parse_task_time(created_at)
    if started is None:
        return None
    elapsed = max(0.0, (datetime.now(timezone.utc) - started).total_seconds())
    return int(elapsed * (100 - progress) / progress)


def _task_eta(created_at: str, updated_at: str, status: str, progress: int) -> int | None:
    if status in {"completed", "cancelled", "error"}:
        return None
    updated = _parse_task_time(updated_at)
    if updated is None:
        return None
    if (datetime.now(timezone.utc) - updated).total_seconds() > ETA_STALE_PROGRESS_SECONDS:
        return None
    return _estimate_eta(created_at, progress)


def _task_progress(task_id: str) -> TaskProgress:
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="导入任务不存在。")
        logs = _task_log_tail(task, TASK_PROGRESS_LOG_CHARS)
        status = task["status"]
        progress, stage = _task_progress_state(task, logs, status)
        log_tail = redact_text(_task_log_tail(task, TASK_LOG_TAIL_CHARS), limit=TASK_LOG_TAIL_CHARS, collapse_whitespace=False)
        return TaskProgress(
            task_id=task_id,
            status=status,
            progress=progress,
            stage=stage,
            message=_task_progress_message(task),
            eta=_task_eta(task["created_at"], task["updated_at"], status, progress),
            updated_at=task["updated_at"],
            file_id=task.get("file_id"),
            mode=str(task.get("mode") or "incremental"),
            error=_task_public_error(task),
            error_info=_task_error_info(task),
            can_cancel=status in {"running", "cancel_requested"},
            log_tail=log_tail,
        )


def _create_task_or_raise(resolved_path: Path, mode: str) -> str:
    task_id = str(uuid.uuid4())
    now = _now()
    with _tasks_lock:
        if any(task.get("status") in {"running", "cancel_requested"} for task in _tasks.values()):
            raise HTTPException(status_code=409, detail="已有导入任务正在运行，请等待完成或取消后再试。")
        if _process_lock.locked():
            raise HTTPException(status_code=409, detail="上一个导入任务正在收尾，请稍后再试。")
        _tasks[task_id] = {
            "status": "running",
            "logs": [],
            "created_at": now,
            "updated_at": now,
            "error": None,
            "file_id": _file_id_for(resolved_path),
            "mode": mode,
            "process": None,
        }
    _prune_tasks()
    return task_id


def _live_task_for_file_id(file_id: str) -> str | None:
    with _tasks_lock:
        for task_id, task in _tasks.items():
            if task.get("file_id") == file_id and task.get("status") in {"running", "cancel_requested"}:
                return task_id
    return None


def _prune_tasks() -> None:
    with _tasks_lock:
        if len(_tasks) <= MAX_TASKS:
            return
        finished = [
            (task_id, task)
            for task_id, task in _tasks.items()
            if task.get("status") not in {"running", "cancel_requested"}
        ]
        finished.sort(key=lambda item: str(item[1].get("updated_at", "")))
        for task_id, _task in finished[: max(0, len(_tasks) - MAX_TASKS)]:
            _tasks.pop(task_id, None)


def _task_sort_key(task_id: str) -> tuple[int, str, str]:
    task = _tasks[task_id]
    live_rank = 1 if task.get("status") in {"running", "cancel_requested"} else 0
    return live_rank, str(task.get("updated_at", "")), str(task.get("created_at", ""))


def _safe_upload_id(upload_id: str) -> str:
    try:
        return str(uuid.UUID(upload_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="upload_id 无效。") from exc


def _file_id_for(path: Path) -> str:
    return path.resolve().relative_to(LOCAL_ROOT.resolve()).as_posix()


def _upload_meta_path(upload_path: Path) -> Path:
    return upload_path.with_suffix(upload_path.suffix + ".meta")


def _clean_display_filename(filename: str) -> str:
    cleaned = " ".join(Path(filename or "upload.json").name.split())
    if not cleaned:
        cleaned = "upload.json"
    if len(cleaned) <= 240:
        return cleaned

    path = Path(cleaned)
    suffix = path.suffix
    if not suffix:
        return cleaned[:240]

    max_stem_length = max(1, 240 - len(suffix))
    stem = path.stem[:max_stem_length].rstrip() or "upload"
    return f"{stem}{suffix}"


def _write_upload_meta(upload_path: Path, filename: str, scope: str | None = None) -> None:
    meta = {"filename": _clean_display_filename(filename)}
    if scope:
        meta["scope"] = scope
    _upload_meta_path(upload_path).write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def _load_json_file(path: Path) -> Any:
    with path.open("rb") as probe:
        encoding = json.detect_encoding(probe.read(4))
    with path.open("r", encoding=encoding) as source:
        return json.load(source)


def _upload_display_name(upload_path: Path) -> str:
    meta_path = _upload_meta_path(upload_path)
    if not meta_path.exists():
        return upload_path.name
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return upload_path.name
    filename = _clean_display_filename(str(meta.get("filename") or ""))
    return filename or upload_path.name


def _resolve_upload(upload_id: str) -> Path:
    safe_id = _safe_upload_id(upload_id)
    try:
        path = (UPLOAD_ROOT / f"{safe_id}.json").resolve()
        path.relative_to(LOCAL_ROOT.resolve())
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="upload_id 指向的上传文件无效。") from exc
    if not path.exists():
        raise HTTPException(status_code=404, detail="上传文件不存在。")
    if not path.is_file():
        raise HTTPException(status_code=400, detail="upload_id 必须指向 .json 文件。")
    return path


def _resolve_local_file_id(file_id: str) -> Path:
    try:
        path = (LOCAL_ROOT / file_id).resolve()
        path.relative_to(LOCAL_ROOT.resolve())
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="file_id 无效。") from exc

    if not path.exists():
        raise HTTPException(status_code=404, detail="文件不存在。")
    if not path.is_file():
        raise HTTPException(status_code=400, detail="file_id 必须指向 .json 文件。")
    if path.suffix.lower() != ".json":
        raise HTTPException(status_code=400, detail="只能导入 .json 文件。")
    return path


def _resolve_local_path(file_path: str) -> Path:
    raw = Path(file_path).expanduser()
    candidate = raw if raw.is_absolute() else LOCAL_ROOT / raw
    try:
        path = candidate.resolve()
        path.relative_to(LOCAL_ROOT.resolve())
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="file_path 必须位于项目 local/ 目录下。") from exc

    if not path.exists():
        raise HTTPException(status_code=404, detail="指定的 file_path 不存在。")
    if not path.is_dir() and path.suffix.lower() != ".json":
        raise HTTPException(status_code=400, detail="只能导入 .json 文件或 local/ 下的目录。")
    return path


def _directory_json_targets(path: Path) -> list[Path]:
    if not path.is_dir():
        return [path] if path.is_file() and path.suffix.lower() == ".json" else []
    try:
        root = path.resolve()
        local_root = LOCAL_ROOT.resolve()
        targets: list[Path] = []
        candidates = path.rglob("*")
        for candidate in candidates:
            try:
                if not candidate.is_file() or candidate.suffix.lower() != ".json":
                    continue
                resolved = candidate.resolve()
                resolved.relative_to(root)
                resolved.relative_to(local_root)
                targets.append(resolved)
            except (OSError, ValueError):
                continue
    except OSError:
        return []
    return sorted(dict.fromkeys(targets), key=lambda item: item.as_posix().lower())


def _index_scope_for_paths(paths: list[Path]) -> tuple[list[str], list[int]]:
    file_keys = [_file_record_key(path) for path in paths]
    prefixes = [f"{file_scope_for_path(path)}:" for path in paths]
    threads = {
        *store.get_threads_for_ingest_file_paths(file_keys),
        *store.get_threads_for_message_id_prefixes(prefixes),
    }
    session_ids = {
        *store.get_session_ids_for_ingest_file_paths(file_keys),
        *store.get_session_ids_for_message_id_prefixes(prefixes),
    }
    return sorted(threads), sorted(session_ids)


def _validate_directory_index_scope(paths: list[Path], mode: str) -> None:
    threads, session_ids = _index_scope_for_paths(paths)
    if mode in SESSION_CHUNK_REQUIRED_MODES:
        index_status = store.get_session_index_status(session_ids)
        total_chunks = index_status.get("total")
        if not isinstance(total_chunks, int) or total_chunks <= 0:
            raise HTTPException(
                status_code=400,
                detail="仅摘要或仅向量构建要求目标目录已有会话分块；请先执行仅分块、全流程导入或强制重建。",
            )
        return
    if not threads:
        raise HTTPException(
            status_code=400,
            detail="目录单项索引构建要求至少一个 JSON 已有可定位的入库消息范围；请先执行增量导入、全流程导入或强制重建。",
        )


def _mode_args(mode: str) -> list[str]:
    return INGEST_MODE_ARGS.get(mode, [])


def _summary_available() -> bool:
    summary_model = (os.getenv("SUMMARY_MODEL") or "").strip()
    if not summary_model:
        return False
    try:
        return bool(summary_config_status(summary_model).get("configured"))
    except Exception:
        return False


def _embedding_available() -> bool:
    try:
        return bool(embed_configured() and store.has_vec())
    except Exception:
        return False


def _public_index_status(
    index_status: dict[str, int | None],
    *,
    summary_available: bool,
    embedding_available: bool,
) -> dict[str, int | None]:
    return {
        "total": index_status["total"],
        "missing_summary": index_status["missing_summary"] if summary_available else None,
        "missing_embedding": index_status["missing_embedding"] if embedding_available else None,
    }


def _start_message(mode: str, resolved_path: Path) -> str:
    target_label = "目标目录" if resolved_path.is_dir() else "该 JSON"
    message_scope_label = "目标目录关联消息" if resolved_path.is_dir() else "该 JSON 关联消息"
    messages = {
        "incremental": f"增量导入任务已启动：将检查{target_label}，按需解析并补齐缺失索引。",
        "full": f"全流程导入任务已启动：将重新解析{target_label}，并补齐必要索引、摘要和向量。",
        "rebuild": f"强制重建任务已启动：将重新解析{target_label}，并重建其关联索引、会话分块、摘要和向量。",
        "fts": f"仅 FTS 任务已启动：将重建{message_scope_label}的全文索引，不会调用模型或 embedding。",
        "chunks": f"仅分块任务已启动：将重建{target_label}关联会话的会话块。",
        "summary": f"仅摘要任务已启动：将处理{target_label}已入库会话块，可能调用摘要模型。",
        "embeddings": f"仅向量任务已启动：将处理{target_label}已入库会话块，可能调用 embedding API。",
        "vector": f"仅向量任务已启动：将处理{target_label}已入库会话块，可能调用 embedding API。",
    }
    return messages.get(mode, "导入任务已启动。")


def _latest_task_for_file(file_id: str) -> tuple[str, dict[str, Any]] | None:
    with _tasks_lock:
        matching = [
            (task_id, task)
            for task_id, task in _tasks.items()
            if task.get("file_id") == file_id
        ]
        if not matching:
            return None
        return max(
            matching,
            key=lambda item: (
                str(item[1].get("created_at", "")),
                str(item[1].get("updated_at", "")),
                item[0],
            ),
        )


def _file_ingest_status(stat: os.stat_result, record: dict[str, Any] | None, latest_task: dict[str, Any] | None) -> str:
    task_status = str(latest_task.get("status")) if latest_task else ""
    if task_status in {"running", "cancel_requested"}:
        return task_status
    if record is None:
        return "never"
    if (
        int(record["size"]) == stat.st_size
        and int(record["mtime_ns"]) == stat.st_mtime_ns
        and int(record.get("parser_version") or 0) >= PARSER_VERSION
    ):
        return "up_to_date"
    return "changed"


def _file_ingest_status_reason(stat: os.stat_result, record: dict[str, Any] | None, latest_task: dict[str, Any] | None) -> str | None:
    task_status = str(latest_task.get("status")) if latest_task else ""
    if task_status in {"running", "cancel_requested"} or record is None:
        return None
    file_current = int(record["size"]) == stat.st_size and int(record["mtime_ns"]) == stat.st_mtime_ns
    parser_current = int(record.get("parser_version") or 0) >= PARSER_VERSION
    if not file_current:
        return "file_changed"
    if not parser_current:
        return "parser_version_stale"
    return None


def _is_file_record_current(path: Path) -> bool:
    stat = path.stat()
    file_key = str(path.resolve())
    record = store.get_ingest_file_records([file_key]).get(file_key)
    return bool(
        record
        and int(record["size"]) == stat.st_size
        and int(record["mtime_ns"]) == stat.st_mtime_ns
        and int(record.get("parser_version") or 0) >= PARSER_VERSION
    )


def _has_file_index_scope(path: Path) -> bool:
    file_key = _file_record_key(path)
    if store.ingest_file_message_mapping_exists(file_key):
        return True
    prefix = f"{file_scope_for_path(path)}:"
    return bool(store.get_threads_for_message_id_prefixes([prefix]))


def _append_log(task_id: str, text: str) -> None:
    progress_event = _parse_progress_event(text.strip())
    if len(text) > MAX_TASK_LOG_LINE_CHARS:
        text = text[:MAX_TASK_LOG_LINE_CHARS] + "\n...[line truncated]\n"
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task is None:
            return
        if progress_event is not None:
            task["progress_event"] = progress_event
            task["updated_at"] = _now()
            return
        task.setdefault("logs", []).append(text)
        if len(task["logs"]) > MAX_TASK_LOG_LINES:
            task["logs"] = task["logs"][-MAX_TASK_LOG_LINES:]
        task["updated_at"] = _now()


def _requested_cancel(task_id: str) -> bool:
    with _tasks_lock:
        task = _tasks.get(task_id)
        return bool(task and task.get("status") in {"cancel_requested", "cancelled"})


def _run_ingest_task(task_id: str, file_path: Path, mode: str) -> None:
    if _requested_cancel(task_id):
        _task_update(task_id, status="cancelled", error=None)
        logger.info("Ingest task cancelled before worker acquired process lock", extra={"task_id": task_id})
        return

    if not _process_lock.acquire(blocking=False):
        _task_update(task_id, status="error", error="已有导入任务正在运行。", return_code=None)
        logger.error(
            "Failed to start ingest task because another task is running",
            extra={"task_id": task_id, "file_id": _file_id_for(file_path), "mode": mode},
        )
        return

    process: subprocess.Popen[str] | None = None
    try:
        if _requested_cancel(task_id):
            _task_update(task_id, status="cancelled", error=None)
            logger.info("Ingest task cancelled before start", extra={"task_id": task_id})
            return

        command = [sys.executable, "-m", "core.ingest", str(file_path), *_mode_args(mode)]
        env = os.environ.copy()
        env["INGEST_PROGRESS_JSON"] = "true"
        process = subprocess.Popen(
            command,
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        _task_update(task_id, process=process)
        if _requested_cancel(task_id):
            _terminate_process(process)
            _task_update(task_id, status="cancelled", error=None)
            logger.info("Ingest task cancelled immediately after subprocess start", extra={"task_id": task_id})
            return
        logger.info(
            "Ingest subprocess started",
            extra={"task_id": task_id, "file_id": _file_id_for(file_path), "mode": mode},
        )

        assert process.stdout is not None
        for line in process.stdout:
            _append_log(task_id, line)

        return_code = process.wait()
        if _requested_cancel(task_id):
            _task_update(task_id, status="cancelled", error=None)
            logger.info("Ingest task cancelled", extra={"task_id": task_id})
        elif return_code == 0:
            _task_update(task_id, status="completed", error=None, return_code=0)
            logger.info("Ingest task completed", extra={"task_id": task_id})
        else:
            _task_update(task_id, status="error", error=_task_failure_error(task_id, return_code), return_code=return_code)
            logger.error("Ingest task failed", extra={"task_id": task_id, "return_code": return_code})
    except BaseException as exc:
        _task_update(task_id, status="error", error=public_exception_message("Ingest task failed", exc), return_code=None)
        logger.error(
            "Ingest task crashed",
            exc_info=(type(exc), exc, exc.__traceback__),
            extra={"task_id": task_id},
        )
    finally:
        if process is not None:
            _task_update(task_id, process=None)
        _process_lock.release()


def _terminate_process(process: subprocess.Popen[str]) -> bool:
    if process.poll() is not None:
        return True
    process.terminate()
    try:
        process.wait(timeout=5)
        return True
    except subprocess.TimeoutExpired:
        process.kill()
    try:
        process.wait(timeout=5)
        return True
    except subprocess.TimeoutExpired:
        logger.error("Ingest subprocess did not exit after kill", extra={"pid": process.pid})
        return False


def shutdown_running_tasks() -> None:
    processes: list[tuple[str, subprocess.Popen[str]]] = []
    task_ids_without_process: list[str] = []
    with _tasks_lock:
        for task_id, task in _tasks.items():
            if task.get("status") not in {"running", "cancel_requested"}:
                continue
            task["status"] = "cancel_requested"
            task["updated_at"] = _now()
            process = task.get("process")
            if process is not None:
                processes.append((task_id, process))
            else:
                task_ids_without_process.append(task_id)

    for task_id, process in processes:
        _terminate_process(process)
        _task_update(task_id, status="cancelled", error="后端关闭，导入任务已终止。", process=None)
        logger.info("Ingest task cancelled during backend shutdown", extra={"task_id": task_id})
    for task_id in task_ids_without_process:
        _task_update(task_id, status="cancelled", error="后端关闭，导入任务已取消。", process=None)
        logger.info("Queued ingest task cancelled during backend shutdown", extra={"task_id": task_id})


def _json_file_snapshots() -> list[tuple[Path, os.stat_result, str]]:
    snapshots: list[tuple[Path, os.stat_result, str]] = []
    local_root = LOCAL_ROOT.resolve()
    try:
        candidates = LOCAL_ROOT.rglob("*")
        for path in candidates:
            try:
                if not path.is_file() or path.suffix.lower() != ".json":
                    continue
                resolved = path.resolve()
                resolved.relative_to(local_root)
                stat = path.stat()
                snapshots.append((path, stat, str(resolved)))
            except ValueError:
                logger.warning(
                    "Skipped ingest file outside local root",
                    extra={"file_id": path.name},
                )
                continue
            except OSError as exc:
                logger.warning(
                    "Skipped unreadable ingest file during listing",
                    extra={"file_id": _safe_file_id_for_log(path), "error": public_exception_message("文件状态读取失败", exc)},
                )
                continue
    except OSError as exc:
        logger.warning(
            "Stopped ingest file listing after directory scan error",
            extra={"error": public_exception_message("文件列表扫描失败", exc)},
        )
    return sorted(snapshots, key=lambda item: item[1].st_mtime, reverse=True)


def _safe_file_id_for_log(path: Path) -> str:
    try:
        return _file_id_for(path)
    except Exception:
        return path.name


def _file_record_key(path: Path) -> str:
    return str(path.resolve())


def _unknown_index_status() -> dict[str, int | None]:
    return {"total": None, "missing_summary": None, "missing_embedding": None}


def _index_status_for_file(path: Path) -> dict[str, int | None]:
    return _index_statuses_for_files([path]).get(_file_record_key(path), _unknown_index_status())


def _index_statuses_for_files(paths: list[Path]) -> dict[str, dict[str, int | None]]:
    file_keys_by_path = {_file_record_key(path): path for path in paths}
    if not file_keys_by_path:
        return {}

    file_keys = list(file_keys_by_path)
    mapped_paths = store.get_ingest_file_message_mapping_paths(file_keys)
    mapped_session_ids = store.get_session_ids_by_ingest_file_paths(file_keys)
    session_ids_by_key: dict[str, list[int]] = {}
    prefix_by_key: dict[str, str] = {}
    mapped_empty_keys: set[str] = set()
    statuses: dict[str, dict[str, int | None]] = {}

    for file_key, path in file_keys_by_path.items():
        if file_key in mapped_paths:
            session_ids = mapped_session_ids.get(file_key, [])
            if session_ids:
                session_ids_by_key[file_key] = session_ids
            else:
                mapped_empty_keys.add(file_key)
                prefix_by_key[file_key] = f"{file_scope_for_path(path)}:"
        else:
            prefix_by_key[file_key] = f"{file_scope_for_path(path)}:"

    prefix_session_ids = store.get_session_ids_by_message_id_prefixes(list(prefix_by_key.values()))
    for file_key, prefix in prefix_by_key.items():
        session_ids = prefix_session_ids.get(prefix, [])
        if session_ids:
            session_ids_by_key[file_key] = session_ids
        elif file_key in mapped_empty_keys:
            session_ids_by_key[file_key] = []
        else:
            statuses[file_key] = _unknown_index_status()

    statuses.update(store.get_session_index_statuses(session_ids_by_key))
    return statuses


@router.get("/files", summary="List local ingest files")
def list_files(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    LOCAL_ROOT.mkdir(parents=True, exist_ok=True)
    safe_limit = query_int(limit, 100, minimum=1, maximum=500)
    safe_offset = query_int(offset, 0, minimum=0)
    files = _json_file_snapshots()
    page = files[safe_offset : safe_offset + safe_limit]
    records = store.get_ingest_file_records([resolved_key for _path, _stat, resolved_key in page])
    indexed_paths = [path for path, _stat, resolved_key in page if resolved_key in records]
    index_statuses = _index_statuses_for_files(indexed_paths)
    summary_available = _summary_available()
    embedding_available = _embedding_available()
    items: list[LocalFileItem] = []
    for path, stat, resolved_key in page:
        upload_id: str | None = None
        source = "local"
        if path.parent.resolve() == UPLOAD_ROOT.resolve():
            try:
                upload_id = str(uuid.UUID(path.stem))
                source = "upload"
                filename = _upload_display_name(path)
            except ValueError:
                filename = path.name
        else:
            filename = path.name
        file_id = _file_id_for(path)
        latest = _latest_task_for_file(file_id)
        latest_task_id = latest[0] if latest else None
        latest_task = latest[1] if latest else None
        record = records.get(resolved_key)
        changed = record.get("changed", record.get("inserted")) if record else None
        raw_index_status = index_statuses.get(resolved_key, _unknown_index_status()) if record else _unknown_index_status()
        index_status = _public_index_status(
            raw_index_status,
            summary_available=summary_available,
            embedding_available=embedding_available,
        )
        items.append(
            LocalFileItem(
                file_id=file_id,
                filename=filename,
                size=stat.st_size,
                modified_at=_timestamp(stat.st_mtime),
                source=source,
                upload_id=upload_id,
                ingest_status=_file_ingest_status(stat, record, latest_task),
                ingest_status_reason=_file_ingest_status_reason(stat, record, latest_task),
                last_ingested_at=record.get("updated_at") if record else None,
                ingest_total=record.get("total") if record else None,
                ingest_included=record.get("included") if record else None,
                ingest_changed=changed,
                ingest_inserted=record.get("inserted") if record else None,
                session_chunks=index_status["total"],
                missing_summary_chunks=index_status["missing_summary"],
                missing_vector_chunks=index_status["missing_embedding"],
                task_id=latest_task_id,
                task_status=latest_task.get("status") if latest_task else None,
                task_mode=latest_task.get("mode") if latest_task else None,
            )
        )
    return {"total_count": len(files), "returned": len(items), "offset": safe_offset, "items": items}


@router.post("/files/delete", summary="Delete a local ingest source JSON file")
def delete_file(req: IngestFileDeleteRequest) -> dict[str, Any]:
    provided_targets = [
        name
        for name, value in (
            ("upload_id", req.upload_id),
            ("file_id", req.file_id),
        )
        if value
    ]
    if len(provided_targets) != 1:
        raise HTTPException(status_code=400, detail="请且只提供 upload_id 或 file_id 中的一项。")

    if req.upload_id is not None:
        resolved_path = _resolve_upload(req.upload_id)
    elif req.file_id is not None:
        resolved_path = _resolve_local_file_id(req.file_id)
    else:
        raise HTTPException(status_code=400, detail="请且只提供 upload_id 或 file_id 中的一项。")

    file_id = _file_id_for(resolved_path)
    live_task_id = _live_task_for_file_id(file_id)
    if live_task_id:
        raise HTTPException(status_code=409, detail="该文件正在导入，请等待完成或取消任务后再删除。")

    try:
        meta_path = _upload_meta_path(resolved_path)
        resolved_path.unlink()
        if resolved_path.parent.resolve() == UPLOAD_ROOT.resolve():
            meta_path.unlink(missing_ok=True)
    except OSError as exc:
        raise HTTPException(status_code=500, detail="删除文件失败，请检查文件是否被占用或目录权限。") from exc

    logger.info("Ingest source file deleted", extra={"file_id": file_id})
    return {
        "file_id": file_id,
        "message": "源 JSON 文件已删除；已入库的聊天记录不会自动删除。",
    }


@router.post("/upload", response_model=UploadResponse, summary="Upload chat history JSON")
def upload_file(file: UploadFile = File(...)) -> UploadResponse:
    filename = _clean_display_filename(file.filename or "upload.json")
    if Path(filename).suffix.lower() != ".json":
        raise HTTPException(status_code=400, detail="只接受 .json 文件。")

    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    upload_id = str(uuid.uuid4())
    target_path = UPLOAD_ROOT / f"{upload_id}.json"
    temp_path = UPLOAD_ROOT / f"{upload_id}.json.uploading"

    size = 0
    try:
        with temp_path.open("xb") as target:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="上传文件过大。")
                target.write(chunk)
        if size == 0:
            raise HTTPException(status_code=400, detail="上传文件为空。")
        try:
            data = _load_json_file(temp_path)
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail="上传文件不是合法 JSON。") from exc
        if not is_weflow_export(data):
            raise HTTPException(status_code=400, detail="上传文件不是 WeFlow 微信聊天导出 JSON。")

        _write_upload_meta(target_path, filename, scope=stable_upload_scope(data, filename))
        temp_path.replace(target_path)
    except OSError as exc:
        temp_path.unlink(missing_ok=True)
        target_path.unlink(missing_ok=True)
        _upload_meta_path(target_path).unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="上传文件保存失败。") from exc
    except Exception:
        temp_path.unlink(missing_ok=True)
        target_path.unlink(missing_ok=True)
        _upload_meta_path(target_path).unlink(missing_ok=True)
        raise

    logger.info("Ingest file uploaded", extra={"upload_id": upload_id, "upload_filename": filename, "size": size})
    return UploadResponse(
        upload_id=upload_id,
        filename=filename,
        size=size,
        message="文件上传成功。",
    )


@router.get("/tasks", summary="List ingest tasks")
def list_tasks(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    safe_limit = query_int(limit, 50, minimum=1, maximum=200)
    safe_offset = query_int(offset, 0, minimum=0)
    _prune_tasks()
    with _tasks_lock:
        ordered = sorted(_tasks, key=_task_sort_key, reverse=True)
    page = ordered[safe_offset : safe_offset + safe_limit]
    return {
        "total_count": len(ordered),
        "returned": len(page),
        "offset": safe_offset,
        "items": [_task_snapshot(task_id, include_logs=False) for task_id in page],
    }


@router.post("/start", summary="Start background ingestion")
def start_ingest(
    req: IngestStartRequest | None = Body(default=None),
    file_path: str | None = Query(default=None, max_length=MAX_INGEST_TARGET_CHARS),
) -> dict[str, str]:
    req = req if isinstance(req, IngestStartRequest) else None
    normalized_query_file_path = _normalize_target_value(file_path)
    query_file_path = normalized_query_file_path if isinstance(normalized_query_file_path, str) else None
    upload_id = req.upload_id if req else None
    file_id = req.file_id if req else None
    legacy_file_path = (req.file_path if req else None) or query_file_path
    mode = req.mode if req else "incremental"
    provided_targets = [
        name
        for name, value in (
            ("upload_id", upload_id),
            ("file_id", file_id),
            ("file_path", legacy_file_path),
        )
        if value
    ]

    if len(provided_targets) != 1:
        raise HTTPException(status_code=400, detail="请且只提供 upload_id、file_id 或 file_path 中的一项。")

    if upload_id is not None:
        resolved_path = _resolve_upload(upload_id)
    elif file_id is not None:
        resolved_path = _resolve_local_file_id(file_id)
    elif legacy_file_path is not None:
        resolved_path = _resolve_local_path(legacy_file_path)
    else:
        raise HTTPException(status_code=400, detail="请且只提供 upload_id、file_id 或 file_path 中的一项。")

    directory_targets: list[Path] | None = None
    if resolved_path.is_dir():
        directory_targets = _directory_json_targets(resolved_path)
        if not directory_targets:
            raise HTTPException(status_code=400, detail="目标目录没有可导入的 .json 文件。")

    if mode in INDEX_ONLY_MODES:
        if resolved_path.is_file():
            if not _is_file_record_current(resolved_path):
                raise HTTPException(
                    status_code=400,
                    detail="单项索引构建要求文件已完成导入且当前状态为已同步。",
                )
            if not _has_file_index_scope(resolved_path):
                raise HTTPException(
                    status_code=400,
                    detail="单项索引构建要求文件已有可定位的来源映射；请先执行增量导入、全流程导入或强制重建。",
                )
            if mode in SESSION_CHUNK_REQUIRED_MODES:
                index_status = _index_status_for_file(resolved_path)
                total_chunks = index_status.get("total")
                if not isinstance(total_chunks, int) or total_chunks <= 0:
                    raise HTTPException(
                        status_code=400,
                        detail="仅摘要或仅向量构建要求该 JSON 已有会话分块；请先执行仅分块、全流程导入或强制重建。",
                    )
        elif directory_targets is not None:
            _validate_directory_index_scope(directory_targets, mode)
        if mode == "summary" and not _summary_available():
            raise HTTPException(
                status_code=400,
                detail="仅摘要构建要求已配置 SUMMARY_MODEL，并配置可用的摘要 Base URL/API Key；未单独设置时会继承对话配置。",
            )
        if mode in {"embeddings", "vector"} and not _embedding_available():
            raise HTTPException(
                status_code=400,
                detail="仅向量构建要求已配置 EMBED_BASE_URL、EMBED_API_KEY、EMBED_MODEL，且 sqlite-vec 可用。",
            )

    task_id = _create_task_or_raise(resolved_path, mode)
    thread = threading.Thread(target=_run_ingest_task, args=(task_id, resolved_path, mode), daemon=True)
    try:
        thread.start()
    except RuntimeError as exc:
        detail = public_exception_message("导入任务启动失败", exc)
        _task_update(task_id, status="error", error=detail, process=None)
        logger.error(
            "Failed to start ingest worker thread",
            exc_info=(type(exc), exc, exc.__traceback__),
            extra={"task_id": task_id, "file_id": _file_id_for(resolved_path), "mode": mode},
        )
        raise HTTPException(status_code=500, detail=detail) from exc
    logger.info("Ingest task queued", extra={"task_id": task_id, "file_id": _file_id_for(resolved_path), "mode": mode})

    return {"task_id": task_id, "mode": mode, "message": _start_message(mode, resolved_path)}


@router.get("/status/{task_id}", response_model=TaskStatus, summary="Get ingest task status")
def get_task_status(task_id: str) -> TaskStatus:
    return _task_snapshot(task_id, include_logs=True)


@router.post("/tasks/{task_id}/cancel", response_model=TaskStatus, summary="Cancel an ingest task")
def cancel_task(task_id: str) -> TaskStatus:
    process: subprocess.Popen[str] | None = None
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="导入任务不存在。")
        if task["status"] not in {"running", "cancel_requested"}:
            return _task_snapshot(task_id, include_logs=True)
        task["status"] = "cancel_requested"
        task["updated_at"] = _now()
        process = task.get("process")
        logger.info("Ingest cancel requested", extra={"task_id": task_id})

    if process is None:
        _task_update(task_id, status="cancelled", error=None, process=None)
    else:
        return_code = process.poll()
        if return_code is not None:
            if return_code == 0:
                _task_update(task_id, status="completed", error=None, return_code=0, process=None)
            else:
                _task_update(task_id, status="error", error=_task_failure_error(task_id, return_code), return_code=return_code, process=None)
        elif _terminate_process(process):
            _task_update(task_id, status="cancelled", error=None, process=None)

    return _task_snapshot(task_id, include_logs=True)


@ws_router.websocket("/ws/ingest/{task_id}")
async def ingest_progress_stream(websocket: WebSocket, task_id: str) -> None:
    await websocket.accept()
    try:
        while True:
            try:
                progress = _task_progress(task_id)
            except HTTPException:
                await websocket.send_json(
                    {
                        "task_id": task_id,
                        "status": "error",
                        "progress": 0,
                        "stage": "missing",
                        "eta": None,
                        "error": "导入任务不存在。",
                    }
                )
                await websocket.close(code=1008)
                return

            await websocket.send_json(progress.model_dump())
            if progress.status in {"completed", "error", "cancelled"}:
                await websocket.close(code=1000)
                return
            await asyncio.sleep(1.0)
    except WebSocketDisconnect:
        return
