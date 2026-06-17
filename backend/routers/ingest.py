"""Ingest router - safe file upload and cancellable background tasks."""

from __future__ import annotations

import asyncio
import os
import re
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field

from ..logging_utils import get_logger


router = APIRouter(prefix="/api/ingest", tags=["ingest"])
ws_router = APIRouter(prefix="/api", tags=["ingest"])
logger = get_logger()

PROJECT_ROOT = Path(__file__).resolve().parents[2]
LOCAL_ROOT = PROJECT_ROOT / "local"
UPLOAD_ROOT = LOCAL_ROOT / "uploads"
MAX_UPLOAD_BYTES = int(os.getenv("INGEST_MAX_UPLOAD_MB", "512")) * 1024 * 1024

_tasks: dict[str, dict[str, Any]] = {}
_tasks_lock = threading.RLock()
_process_lock = threading.Lock()


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


class IngestStartRequest(BaseModel):
    upload_id: str | None = Field(default=None, description="ID returned by /api/ingest/upload")
    file_id: str | None = Field(default=None, description="Relative file ID returned by /api/ingest/files")
    file_path: str | None = Field(default=None, description="Legacy local path under local/")


class TaskStatus(BaseModel):
    task_id: str
    status: str
    logs: str
    created_at: str
    updated_at: str
    file_id: str | None = None
    error: str | None = None
    can_cancel: bool = False


class TaskProgress(BaseModel):
    task_id: str
    status: str
    progress: int
    stage: str
    eta: int | None = None
    updated_at: str
    file_id: str | None = None
    error: str | None = None
    can_cancel: bool = False
    log_tail: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _timestamp(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _task_update(task_id: str, **fields: Any) -> None:
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task is None:
            return
        task.update(fields)
        task["updated_at"] = _now()


def _task_snapshot(task_id: str, include_logs: bool = True) -> TaskStatus:
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found.")

        logs = "".join(task.get("logs", [])) if include_logs else ""
        status = task["status"]
        return TaskStatus(
            task_id=task_id,
            status=status,
            logs=logs,
            created_at=task["created_at"],
            updated_at=task["updated_at"],
            file_id=task.get("file_id"),
            error=task.get("error"),
            can_cancel=status in {"running", "cancel_requested"},
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


def _estimate_eta(created_at: str, progress: int) -> int | None:
    if progress <= 0 or progress >= 100:
        return None
    try:
        started = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
    except ValueError:
        return None
    elapsed = max(0.0, (datetime.now(timezone.utc) - started).total_seconds())
    return int(elapsed * (100 - progress) / progress)


def _task_progress(task_id: str) -> TaskProgress:
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        logs = "".join(task.get("logs", []))
        status = task["status"]
        progress, stage = _parse_progress_from_logs(logs, status)
        log_tail = logs[-2000:]
        return TaskProgress(
            task_id=task_id,
            status=status,
            progress=progress,
            stage=stage,
            eta=_estimate_eta(task["created_at"], progress),
            updated_at=task["updated_at"],
            file_id=task.get("file_id"),
            error=task.get("error"),
            can_cancel=status in {"running", "cancel_requested"},
            log_tail=log_tail,
        )


def _has_running_task() -> bool:
    with _tasks_lock:
        return any(task.get("status") in {"running", "cancel_requested"} for task in _tasks.values())


def _safe_upload_id(upload_id: str) -> str:
    try:
        return str(uuid.UUID(upload_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid upload_id.") from exc


def _file_id_for(path: Path) -> str:
    return path.resolve().relative_to(LOCAL_ROOT.resolve()).as_posix()


def _resolve_upload(upload_id: str) -> Path:
    safe_id = _safe_upload_id(upload_id)
    path = (UPLOAD_ROOT / f"{safe_id}.json").resolve()
    if not path.exists():
        raise HTTPException(status_code=404, detail="Uploaded file not found.")
    return path


def _resolve_local_file_id(file_id: str) -> Path:
    try:
        path = (LOCAL_ROOT / file_id).resolve()
        path.relative_to(LOCAL_ROOT.resolve())
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="Invalid file_id.") from exc

    if not path.exists():
        raise HTTPException(status_code=404, detail="File not found.")
    if path.suffix.lower() != ".json":
        raise HTTPException(status_code=400, detail="Only .json files can be ingested.")
    return path


def _resolve_local_path(file_path: str) -> Path:
    raw = Path(file_path).expanduser()
    candidate = raw if raw.is_absolute() else LOCAL_ROOT / raw
    try:
        path = candidate.resolve()
        path.relative_to(LOCAL_ROOT.resolve())
    except (OSError, ValueError) as exc:
        raise HTTPException(status_code=400, detail="file_path must be under the project local/ directory.") from exc

    if not path.exists():
        raise HTTPException(status_code=404, detail="Specified file_path does not exist.")
    if path.suffix.lower() != ".json":
        raise HTTPException(status_code=400, detail="Only .json files can be ingested.")
    return path


def _append_log(task_id: str, text: str) -> None:
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task is None:
            return
        task.setdefault("logs", []).append(text)
        if len(task["logs"]) > 5000:
            task["logs"] = task["logs"][-5000:]
        task["updated_at"] = _now()


def _requested_cancel(task_id: str) -> bool:
    with _tasks_lock:
        task = _tasks.get(task_id)
        return bool(task and task.get("status") == "cancel_requested")


def _run_ingest_task(task_id: str, file_path: Path) -> None:
    if not _process_lock.acquire(blocking=False):
        _task_update(task_id, status="error", error="Another ingest task is already running.")
        logger.error(
            "Failed to start ingest task because another task is running",
            extra={"task_id": task_id, "file_id": _file_id_for(file_path)},
        )
        return

    process: subprocess.Popen[str] | None = None
    try:
        if _requested_cancel(task_id):
            _task_update(task_id, status="cancelled", error=None)
            logger.info("Ingest task cancelled before start", extra={"task_id": task_id})
            return

        process = subprocess.Popen(
            [sys.executable, "-m", "core.ingest", str(file_path)],
            cwd=str(PROJECT_ROOT),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        _task_update(task_id, process=process)
        logger.info("Ingest subprocess started", extra={"task_id": task_id, "file_id": _file_id_for(file_path)})

        assert process.stdout is not None
        for line in process.stdout:
            _append_log(task_id, line)

        return_code = process.wait()
        if _requested_cancel(task_id):
            _task_update(task_id, status="cancelled", error=None)
            logger.info("Ingest task cancelled", extra={"task_id": task_id})
        elif return_code == 0:
            _task_update(task_id, status="completed", error=None)
            logger.info("Ingest task completed", extra={"task_id": task_id})
        else:
            _task_update(task_id, status="error", error=f"ingest exited with code {return_code}")
            logger.error("Ingest task failed", extra={"task_id": task_id, "return_code": return_code})
    except BaseException as exc:
        _task_update(task_id, status="error", error=f"{type(exc).__name__}: {exc}")
        logger.error(
            "Ingest task crashed",
            exc_info=(type(exc), exc, exc.__traceback__),
            extra={"task_id": task_id},
        )
    finally:
        if process is not None:
            _task_update(task_id, process=None)
        _process_lock.release()


@router.get("/files", summary="List local ingest files")
def list_files(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    LOCAL_ROOT.mkdir(parents=True, exist_ok=True)
    files = sorted(
        (path for path in LOCAL_ROOT.rglob("*.json") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    page = files[offset : offset + limit]
    items: list[LocalFileItem] = []
    for path in page:
        stat = path.stat()
        upload_id: str | None = None
        source = "local"
        if path.parent.resolve() == UPLOAD_ROOT.resolve():
            try:
                upload_id = str(uuid.UUID(path.stem))
                source = "upload"
            except ValueError:
                pass
        items.append(
            LocalFileItem(
                file_id=_file_id_for(path),
                filename=path.name,
                size=stat.st_size,
                modified_at=_timestamp(stat.st_mtime),
                source=source,
                upload_id=upload_id,
            )
        )
    return {"total_count": len(files), "returned": len(items), "offset": offset, "items": items}


@router.post("/upload", response_model=UploadResponse, summary="Upload chat history JSON")
def upload_file(file: UploadFile = File(...)) -> UploadResponse:
    filename = Path(file.filename or "upload.json").name
    if Path(filename).suffix.lower() != ".json":
        raise HTTPException(status_code=400, detail="Only .json files are accepted.")

    UPLOAD_ROOT.mkdir(parents=True, exist_ok=True)
    upload_id = str(uuid.uuid4())
    target_path = UPLOAD_ROOT / f"{upload_id}.json"

    size = 0
    try:
        with target_path.open("xb") as target:
            while chunk := file.file.read(1024 * 1024):
                size += len(chunk)
                if size > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Uploaded file is too large.")
                target.write(chunk)
    except Exception:
        target_path.unlink(missing_ok=True)
        raise

    logger.info("Ingest file uploaded", extra={"upload_id": upload_id, "filename": filename, "size": size})
    return UploadResponse(
        upload_id=upload_id,
        filename=filename,
        size=size,
        message="File uploaded successfully.",
    )


@router.get("/tasks", summary="List ingest tasks")
def list_tasks(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    with _tasks_lock:
        ordered = sorted(_tasks, key=lambda task_id: _tasks[task_id]["created_at"], reverse=True)
    page = ordered[offset : offset + limit]
    return {
        "total_count": len(ordered),
        "returned": len(page),
        "offset": offset,
        "items": [_task_snapshot(task_id, include_logs=False) for task_id in page],
    }


@router.post("/start", summary="Start background ingestion")
def start_ingest(
    req: IngestStartRequest | None = Body(default=None),
    file_path: str | None = Query(default=None),
) -> dict[str, str]:
    if _has_running_task():
        raise HTTPException(status_code=409, detail="Another ingest task is already running.")

    upload_id = req.upload_id if req else None
    file_id = req.file_id if req else None
    legacy_file_path = (req.file_path if req else None) or file_path

    if upload_id:
        resolved_path = _resolve_upload(upload_id)
    elif file_id:
        resolved_path = _resolve_local_file_id(file_id)
    elif legacy_file_path:
        resolved_path = _resolve_local_path(legacy_file_path)
    else:
        raise HTTPException(status_code=400, detail="Provide upload_id, file_id, or file_path.")

    task_id = str(uuid.uuid4())
    now = _now()
    with _tasks_lock:
        _tasks[task_id] = {
            "status": "running",
            "logs": [],
            "created_at": now,
            "updated_at": now,
            "error": None,
            "file_id": _file_id_for(resolved_path),
            "process": None,
        }

    thread = threading.Thread(target=_run_ingest_task, args=(task_id, resolved_path), daemon=True)
    thread.start()
    logger.info("Ingest task queued", extra={"task_id": task_id, "file_id": _file_id_for(resolved_path)})

    return {"task_id": task_id, "message": "Background ingest task started."}


@router.get("/status/{task_id}", response_model=TaskStatus, summary="Get ingest task status")
def get_task_status(task_id: str) -> TaskStatus:
    return _task_snapshot(task_id, include_logs=True)


@router.post("/tasks/{task_id}/cancel", response_model=TaskStatus, summary="Cancel an ingest task")
def cancel_task(task_id: str) -> TaskStatus:
    process: subprocess.Popen[str] | None = None
    with _tasks_lock:
        task = _tasks.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="Task not found.")
        if task["status"] not in {"running", "cancel_requested"}:
            return _task_snapshot(task_id, include_logs=True)
        task["status"] = "cancel_requested"
        task["updated_at"] = _now()
        process = task.get("process")
        logger.info("Ingest cancel requested", extra={"task_id": task_id})

    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()

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
                        "error": "Task not found.",
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
