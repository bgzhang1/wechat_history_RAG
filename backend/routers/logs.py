"""Log inspection endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..logging_utils import read_recent_logs
from .params import query_int, query_str


router = APIRouter(prefix="/api", tags=["logs"])


@router.get("/logs", summary="Get recent backend logs")
def get_recent_logs(
    level: str = Query(default="error", pattern="^(debug|info|warning|error)$"),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    try:
        return read_recent_logs(
            level=query_str(level, "error"),
            limit=query_int(limit, 100, minimum=1, maximum=1000),
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
