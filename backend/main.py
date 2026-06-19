"""FastAPI application entry point."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from starlette.exceptions import HTTPException as StarletteHTTPException

from core import store
from core.llm import chat_config_status, embed_config_status

from . import session_store
from .errors import http_exception_handler, unhandled_exception_handler, validation_exception_handler
from .logging_utils import get_logger
from .redaction import public_exception_message
from .routers import chat, ingest, logs, settings, stats, suggestions

load_dotenv()
logger = get_logger()


def _env_int(name: str, default: int, minimum: int = 1, maximum: int | None = None) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError:
        value = default
    value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialize local databases on startup."""
    store.db()
    session_store.db()
    db_stats = store.stats_summary(include_message_types=False)
    total = db_stats["total_messages"]
    chunks = db_stats["indexed_session_chunks"]
    earliest = (db_stats["time_span"]["earliest"] or "")[:10]
    latest = (db_stats["time_span"]["latest"] or "")[:10]
    print(f"[backend] indexed {total} messages / {chunks} session chunks, time span {earliest} ~ {latest}")
    logger.info(
        "Backend started",
        extra={
            "total_messages": total,
            "indexed_session_chunks": chunks,
            "earliest": earliest,
            "latest": latest,
        },
    )
    try:
        yield
    finally:
        ingest.shutdown_running_tasks()
        store.close_all_connections()
        session_store.close_connection()


app = FastAPI(
    title="WeChat Chat History Retrieval API",
    description="Streaming Agent backend for core.",
    version="0.1.0",
    lifespan=lifespan,
)


_cors_origins_raw = os.getenv("CORS_ORIGINS", "")
_cors_origins = [
    origin.strip()
    for origin in _cors_origins_raw.split(",")
    if origin.strip()
] if _cors_origins_raw else []

_default_vite_ports = range(5173, 5181)
_default_origins = [
    *[f"http://localhost:{port}" for port in _default_vite_ports],
    *[f"http://127.0.0.1:{port}" for port in _default_vite_ports],
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=[*_default_origins, *_cors_origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _summary_config_status() -> dict:
    summary_model = (os.getenv("SUMMARY_MODEL") or "").strip()
    if not summary_model:
        return {"configured": False, "missing": ["SUMMARY_MODEL"], "model": ""}
    return chat_config_status(summary_model)


def _chat_model_action(chat_status: dict) -> tuple[str, str | None]:
    if chat_status["configured"]:
        return f"已配置模型：{chat_status['model']}", None
    missing = list(chat_status.get("missing") or [])
    detail = f"缺少配置：{', '.join(missing)}"
    if set(missing) == {"CHAT_MODEL"}:
        return detail, "请在设置页填写对话模型，或在 .env / 环境变量中设置 CHAT_MODEL。"
    return detail, "请在 .env 或环境变量中设置 CHAT_BASE_URL、CHAT_API_KEY 和 CHAT_MODEL。"


def _chat_model_action_target(chat_status: dict) -> str | None:
    missing = set(chat_status.get("missing") or [])
    return "settings" if missing == {"CHAT_MODEL"} else None


app.add_exception_handler(StarletteHTTPException, http_exception_handler)
app.add_exception_handler(RequestValidationError, validation_exception_handler)
app.add_exception_handler(Exception, unhandled_exception_handler)


app.include_router(chat.router)
app.include_router(ingest.router)
app.include_router(ingest.ws_router)
app.include_router(logs.router)
app.include_router(settings.router)
app.include_router(stats.router)
app.include_router(suggestions.router)


def _diagnostics() -> dict:
    checks: list[dict] = []

    try:
        db_stats = store.stats_summary(include_message_types=False)
        has_messages = int(db_stats.get("total_messages") or 0) > 0
        checks.append(
            {
                "component": "database",
                "status": "ok" if has_messages else "warning",
                "detail": (
                    f"已索引 {db_stats['total_messages']} 条消息。"
                    if has_messages
                    else "尚未导入聊天记录，当前无法回答基于微信记录的问题。"
                ),
                "recoverable": not has_messages,
                "action": None if has_messages else "请在设置页的数据导入面板上传 WeFlow JSON，或运行 python -m core.ingest local/data。",
                "action_target": None if has_messages else "ingest",
            }
        )
    except Exception as exc:
        db_stats = {
            "total_messages": 0,
            "indexed_session_chunks": 0,
            "thread_count": 0,
            "sender_count": 0,
            "time_span": {"earliest": None, "latest": None},
        }
        checks.append(
            {
                "component": "database",
                "status": "error",
                "detail": public_exception_message("数据库检查失败", exc),
                "recoverable": True,
                "action": "请检查 CHAT_DB 路径和 runtime 目录权限，必要时重新运行导入。",
                "action_target": None,
            }
        )
    has_messages = int(db_stats.get("total_messages") or 0) > 0

    try:
        session_store.db().execute("SELECT 1").fetchone()
        checks.append(
            {
                "component": "chat_sessions",
                "status": "ok",
                "detail": "Web 对话会话存储可用。",
                "recoverable": False,
                "action": None,
                "action_target": None,
            }
        )
    except Exception as exc:
        checks.append(
            {
                "component": "chat_sessions",
                "status": "error",
                "detail": public_exception_message("会话存储检查失败", exc),
                "recoverable": True,
                "action": "请检查 BACKEND_CHAT_DB 路径和 runtime 目录权限。",
                "action_target": "logs",
            }
        )

    chat_status = chat_config_status()
    summary_status = _summary_config_status()
    chat_detail, chat_action = _chat_model_action(chat_status)
    checks.append(
        {
            "component": "chat_model",
            "status": "ok" if chat_status["configured"] else "error",
            "detail": chat_detail,
            "recoverable": not chat_status["configured"],
            "action": chat_action,
            "action_target": _chat_model_action_target(chat_status),
        }
    )

    embed_status = embed_config_status()
    checks.append(
        {
            "component": "embedding_model",
            "status": "ok" if embed_status["configured"] else "warning",
            "detail": (
                f"已配置模型：{embed_status['model']}"
                if embed_status["configured"]
                else f"缺少配置：{', '.join(embed_status['missing'])}"
            ),
            "recoverable": True,
            "action": None if embed_status["configured"] else "请设置 EMBED_BASE_URL、EMBED_API_KEY 和 EMBED_MODEL 以启用向量检索。",
            "action_target": None,
        }
    )

    try:
        vec_available = store.has_vec()
        total_chunks = int(db_stats.get("indexed_session_chunks") or 0)
        missing_vectors = store.count_sessions_without_embedding() if vec_available and total_chunks > 0 else 0
        indexed_vectors = max(0, total_chunks - missing_vectors)
        vector_status = (
            "ok"
            if vec_available and (not has_messages or total_chunks > 0) and missing_vectors == 0
            else "warning"
        )
        no_session_chunks = has_messages and total_chunks == 0
        if no_session_chunks:
            vector_detail = "已导入消息，但尚未构建会话块；语义检索和向量检索不可用。"
        elif vec_available:
            vector_detail = f"sqlite-vec 可用；{indexed_vectors}/{total_chunks} 个会话块已有向量。"
        else:
            vector_detail = "sqlite-vec 不可用；语义检索会退化为全文检索。"

        if vector_status == "ok":
            vector_action = None
            vector_action_target = None
        elif not vec_available:
            vector_action = "请安装或修复 sqlite-vec，重启后端后重新运行导入。"
            vector_action_target = None
        elif no_session_chunks:
            vector_action = "请重新运行完整导入，或在数据导入面板执行仅分块构建。"
            vector_action_target = "ingest"
        elif not embed_status["configured"]:
            vector_action = "请设置 EMBED_BASE_URL、EMBED_API_KEY 和 EMBED_MODEL，然后重新运行导入。"
            vector_action_target = None
        else:
            vector_action = "请在数据导入面板执行仅向量构建，或重新运行完整导入以生成缺失向量。"
            vector_action_target = "ingest"

        checks.append(
            {
                "component": "vector_index",
                "status": vector_status,
                "detail": vector_detail,
                "recoverable": True,
                "action": vector_action,
                "action_target": vector_action_target,
            }
        )
    except Exception as exc:
        vec_available = False
        indexed_vectors = 0
        checks.append(
            {
                "component": "vector_index",
                "status": "error",
                "detail": public_exception_message("向量索引检查失败", exc),
                "recoverable": True,
                "action": "请检查 sqlite-vec 安装状态和向量索引表。",
                "action_target": None,
            }
        )

    statuses = {check["status"] for check in checks}
    overall = "error" if "error" in statuses else "degraded" if "warning" in statuses else "ok"
    return {
        "overall": overall,
        "checks": checks,
        "db_stats": db_stats,
        "chat_status": chat_status,
        "summary_status": summary_status,
        "embed_status": embed_status,
        "vector_index_available": bool(vec_available),
        "vector_search_available": bool(vec_available and embed_status["configured"] and indexed_vectors > 0),
    }


@app.get("/api/health", tags=["health"], summary="Health check")
def health_check() -> dict:
    diagnostics = _diagnostics()
    db_stats = diagnostics["db_stats"]
    chat_status = diagnostics["chat_status"]
    summary_status = diagnostics["summary_status"]
    embed_status = diagnostics["embed_status"]
    return {
        "status": diagnostics["overall"],
        "chat_model_configured": chat_status["configured"],
        "chat_model": chat_status["model"],
        "chat_model_missing": chat_status["missing"],
        "summary_model_configured": summary_status["configured"],
        "summary_model": summary_status["model"],
        "summary_model_missing": summary_status["missing"],
        "embedding_configured": embed_status["configured"],
        "embedding_model": embed_status["model"],
        "embedding_missing": embed_status["missing"],
        "vector_index_available": diagnostics["vector_index_available"],
        "vector_search_available": diagnostics["vector_search_available"],
        "total_messages": db_stats["total_messages"],
        "indexed_session_chunks": db_stats["indexed_session_chunks"],
        "thread_count": db_stats["thread_count"],
        "sender_count": db_stats["sender_count"],
        "has_data": db_stats["total_messages"] > 0,
        "checks": diagnostics["checks"],
    }


@app.get("/api/health/diagnostics", tags=["health"], summary="Detailed health diagnostics")
def health_diagnostics() -> dict:
    return _diagnostics()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=_env_int("PORT", 8000, minimum=1, maximum=65535),
        reload=True,
    )
