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
from .routers import chat, ingest, logs, settings, stats, suggestions

load_dotenv()
logger = get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialize local databases on startup."""
    store.db()
    session_store.db()
    db_stats = store.stats_summary()
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
    yield


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

_default_origins = [
    "http://localhost:5173",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:3000",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=[*_default_origins, *_cors_origins],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


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
        db_stats = store.stats_summary()
        checks.append(
            {
                "component": "database",
                "status": "ok",
                "detail": f"{db_stats['total_messages']} messages indexed.",
                "recoverable": False,
                "action": None,
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
                "detail": f"{type(exc).__name__}: {exc}",
                "recoverable": True,
                "action": "Check CHAT_DB and run ingest again if needed.",
            }
        )

    try:
        session_store.db().execute("SELECT 1").fetchone()
        checks.append(
            {
                "component": "chat_sessions",
                "status": "ok",
                "detail": "Persistent chat session store is available.",
                "recoverable": False,
                "action": None,
            }
        )
    except Exception as exc:
        checks.append(
            {
                "component": "chat_sessions",
                "status": "error",
                "detail": f"{type(exc).__name__}: {exc}",
                "recoverable": True,
                "action": "Check BACKEND_CHAT_DB and runtime directory permissions.",
            }
        )

    chat_status = chat_config_status()
    checks.append(
        {
            "component": "chat_model",
            "status": "ok" if chat_status["configured"] else "error",
            "detail": (
                f"Configured model: {chat_status['model']}"
                if chat_status["configured"]
                else f"Missing: {', '.join(chat_status['missing'])}"
            ),
            "recoverable": not chat_status["configured"],
            "action": None if chat_status["configured"] else "Set CHAT_BASE_URL, CHAT_API_KEY, and CHAT_MODEL.",
        }
    )

    embed_status = embed_config_status()
    checks.append(
        {
            "component": "embedding_model",
            "status": "ok" if embed_status["configured"] else "warning",
            "detail": (
                f"Configured model: {embed_status['model']}"
                if embed_status["configured"]
                else f"Missing: {', '.join(embed_status['missing'])}"
            ),
            "recoverable": True,
            "action": None if embed_status["configured"] else "Set EMBED_BASE_URL, EMBED_API_KEY, and EMBED_MODEL to enable vector search.",
        }
    )

    try:
        vec_available = store.has_vec()
        missing_vectors = len(store.get_all_session_ids_without_embedding()) if vec_available else 0
        vector_status = "ok" if vec_available and missing_vectors == 0 else "warning"
        checks.append(
            {
                "component": "vector_index",
                "status": vector_status,
                "detail": (
                    f"sqlite-vec available; {missing_vectors} session chunks missing vectors."
                    if vec_available
                    else "sqlite-vec is unavailable; semantic search will fall back to full-text search."
                ),
                "recoverable": True,
                "action": None if vector_status == "ok" else "Re-run ingest after embedding configuration is fixed.",
            }
        )
    except Exception as exc:
        vec_available = False
        checks.append(
            {
                "component": "vector_index",
                "status": "error",
                "detail": f"{type(exc).__name__}: {exc}",
                "recoverable": True,
                "action": "Check sqlite-vec installation and embedding index state.",
            }
        )

    statuses = {check["status"] for check in checks}
    overall = "error" if "error" in statuses else "degraded" if "warning" in statuses else "ok"
    return {
        "overall": overall,
        "checks": checks,
        "db_stats": db_stats,
        "chat_status": chat_status,
        "embed_status": embed_status,
        "vector_search_available": bool(vec_available and embed_status["configured"]),
    }


@app.get("/api/health", tags=["health"], summary="Health check")
def health_check() -> dict:
    diagnostics = _diagnostics()
    db_stats = diagnostics["db_stats"]
    chat_status = diagnostics["chat_status"]
    embed_status = diagnostics["embed_status"]
    return {
        "status": diagnostics["overall"],
        "chat_model_configured": chat_status["configured"],
        "chat_model": chat_status["model"],
        "chat_model_missing": chat_status["missing"],
        "embedding_configured": embed_status["configured"],
        "embedding_model": embed_status["model"],
        "embedding_missing": embed_status["missing"],
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
        port=int(os.getenv("PORT", "8000")),
        reload=True,
    )
