"""Realtime suggestions for frontend inputs."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from core import store


router = APIRouter(prefix="/api", tags=["suggestions"])


@router.get("/suggestions", summary="Get sender/thread suggestions")
def get_suggestions(
    query: str = Query(default=""),
    limit: int = Query(default=10, ge=1, le=50),
) -> dict[str, Any]:
    return store.suggest_entities(query=query, limit=limit)


@router.websocket("/ws/suggestions")
async def suggestions_socket(websocket: WebSocket) -> None:
    await websocket.accept()
    try:
        while True:
            raw = await websocket.receive_text()
            query = raw
            limit = 10
            try:
                payload = json.loads(raw)
                if isinstance(payload, dict):
                    query = str(payload.get("query", ""))
                    limit = int(payload.get("limit", 10))
            except json.JSONDecodeError:
                pass

            await websocket.send_json(store.suggest_entities(query=query, limit=limit))
    except WebSocketDisconnect:
        return
