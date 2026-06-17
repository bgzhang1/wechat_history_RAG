"""Ingest router — handle file upload and async ingestion tasks."""

from __future__ import annotations

import io
import os
import sys
import threading
import uuid
from contextlib import redirect_stdout, redirect_stderr
from typing import Any

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from wechat_rag_agent.ingest import main as ingest_main


router = APIRouter(prefix="/api/ingest", tags=["ingest"])


class TaskStatus(BaseModel):
    task_id: str
    status: str
    logs: str


# Global store for ingestion tasks
_tasks: dict[str, dict[str, Any]] = {}


class TeeWriter(io.StringIO):
    """A file-like object that captures output while also printing to original stdout/stderr."""
    def __init__(self, original_stream: Any):
        super().__init__()
        self.original_stream = original_stream

    def write(self, s: str) -> int:
        self.original_stream.write(s)
        return super().write(s)

    def flush(self) -> None:
        self.original_stream.flush()


def _run_ingest_task(task_id: str, file_path: str) -> None:
    """Run the ingest main function in a thread, capturing its output."""
    original_argv = sys.argv.copy()
    
    # We patch sys.argv so ingest.main() picks up the correct arguments.
    # ingest.py uses positional arguments for targets.
    sys.argv = ["ingest.py", file_path]
    
    tee_stdout = TeeWriter(sys.stdout)
    tee_stderr = TeeWriter(sys.stderr)
    
    _tasks[task_id]["tee"] = tee_stdout

    try:
        with redirect_stdout(tee_stdout), redirect_stderr(tee_stderr):
            ingest_main()
        _tasks[task_id]["status"] = "completed"
    except Exception as e:
        _tasks[task_id]["status"] = "error"
        tee_stdout.write(f"\n[Error] {e}\n")
    finally:
        sys.argv = original_argv


@router.post("/upload", summary="上传聊天记录 JSON 文件")
def upload_file(file: UploadFile = File(...)) -> dict[str, str]:
    """上传用于导入的微信聊天记录文件，返回保存后的路径。"""
    if not file.filename.endswith(".json"):
        raise HTTPException(status_code=400, detail="只接受 .json 文件")
    
    local_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "local")
    os.makedirs(local_dir, exist_ok=True)
    
    # Save the file
    target_path = os.path.join(local_dir, file.filename)
    with open(target_path, "wb") as f:
        f.write(file.file.read())
        
    return {"message": "文件上传成功", "file_path": target_path}


@router.post("/start", summary="启动后台导入任务")
def start_ingest(file_path: str) -> dict[str, str]:
    """给定服务器本地的文件路径，启动导入后台线程。"""
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="指定的文件路径不存在")
    
    task_id = str(uuid.uuid4())
    _tasks[task_id] = {
        "status": "running",
        "tee": None,
    }
    
    thread = threading.Thread(target=_run_ingest_task, args=(task_id, file_path), daemon=True)
    thread.start()
    
    return {"task_id": task_id, "message": "后台导入任务已启动"}


@router.get("/status/{task_id}", response_model=TaskStatus, summary="获取导入任务状态与进度")
def get_task_status(task_id: str) -> TaskStatus:
    """获取指定导入任务的状态以及它的实时控制台输出日志。"""
    if task_id not in _tasks:
        raise HTTPException(status_code=404, detail="未找到该任务")
    
    task = _tasks[task_id]
    tee = task.get("tee")
    logs = tee.getvalue() if tee else ""
    
    return TaskStatus(
        task_id=task_id,
        status=task["status"],
        logs=logs
    )
