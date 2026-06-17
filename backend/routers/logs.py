"""Log inspection endpoints."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query

from ..logging_utils import read_recent_logs


router = APIRouter(prefix="/api", tags=["logs"])


@router.get("/logs", summary="Get recent backend logs")
def get_recent_logs(
    level: str = Query(default="error", pattern="^(debug|info|error)$"),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict[str, Any]]:
    try:
        return read_recent_logs(level=level, limit=limit)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
