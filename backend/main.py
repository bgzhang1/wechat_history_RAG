"""FastAPI application entry point."""

from __future__ import annotations

import os
from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from core import store
from core.llm import chat_configured, embed_configured

from .routers import chat, settings, stats

load_dotenv()

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Initialize the database connection on startup."""
    store.db()
    db_stats = store.stats()
    total = db_stats["total_messages"]
    chunks = db_stats["indexed_session_chunks"]
    earliest = (db_stats["time_span"]["earliest"] or "")[:10]
    latest = (db_stats["time_span"]["latest"] or "")[:10]
    print(f"[backend] 数据库就绪：{total} 条消息 / {chunks} 个会话块，时间跨度 {earliest} ~ {latest}")
    yield


app = FastAPI(
    title="微信聊天记录检索 API",
    description="基于 core 的流式对话后端。",
    version="0.1.0",
    lifespan=lifespan,
)


# ── CORS ──────────────────────────────────────────────────────────────────────

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


# ── Exception handler ────────────────────────────────────────────────────────

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=500,
        content={"detail": f"内部错误：{type(exc).__name__}: {exc}"},
    )


# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(chat.router)
app.include_router(settings.router)
app.include_router(stats.router)


@app.get("/api/health", tags=["health"], summary="健康检查")
def health_check() -> dict:
    """返回系统状态和能力标识，前端可据此自适应展示。"""
    db_stats = store.stats()
    return {
        "status": "ok",
        "chat_model_configured": chat_configured(),
        "embedding_configured": embed_configured(),
        "vector_search_available": store.has_vec() and embed_configured(),
        "total_messages": db_stats["total_messages"],
        "has_data": db_stats["total_messages"] > 0,
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "backend.main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=True,
    )
