"""Realtime suggestions for frontend inputs."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool

from core import store
from .params import query_int, query_str


router = APIRouter(prefix="/api", tags=["suggestions"])
MAX_SUGGESTION_QUERY_CHARS = 120
MAX_SUGGESTION_HTTP_QUERY_CHARS = 4096
MAX_SUGGESTION_WS_MESSAGE_CHARS = 4096


def _clean_query(value: Any) -> str:
    return " ".join(str(value or "").split())[:MAX_SUGGESTION_QUERY_CHARS].strip()


def _parse_suggestion_message(raw: str) -> tuple[str, int]:
    limit = 10
    if len(raw) > MAX_SUGGESTION_WS_MESSAGE_CHARS:
        return "", 0
    query = _clean_query(raw)
    try:
        payload = json.loads(raw)
        if isinstance(payload, dict):
            query = _clean_query(payload.get("query", ""))
            try:
                limit = max(1, min(int(payload.get("limit", 10)), 50))
            except (TypeError, ValueError):
                limit = 10
    except json.JSONDecodeError:
        pass
    return query, limit


@router.get("/suggestions", summary="Get sender/thread suggestions")
def get_suggestions(
    query: str = Query(default=""),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    safe_query = query_str(query, "")
    safe_limit = query_int(limit, 10, minimum=1, maximum=50)
    if len(safe_query) > MAX_SUGGESTION_HTTP_QUERY_CHARS:
        return {"query": "", "items": []}
    return store.suggest_entities(query=_clean_query(safe_query), limit=safe_limit)


@router.websocket("/ws/suggestions")
async def suggestions_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            raw = await websocket.receive_text()
            query, limit = _parse_suggestion_message(raw)
            if limit <= 0:
                await websocket.send_json({"query": "", "items": []})
                continue
            # SQLite 聚合查询必须放线程池，否则会阻塞事件循环（拖慢 SSE 等所有并发请求）
            result = await run_in_threadpool(store.suggest_entities, query=query, limit=limit)
            await websocket.send_json(result)
    except WebSocketDisconnect:
        return
