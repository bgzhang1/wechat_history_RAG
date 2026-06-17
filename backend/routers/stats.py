"""Stats router - exposes lightweight and paginated database statistics."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from core import store


router = APIRouter(prefix="/api", tags=["stats"])


@router.get("/stats", summary="Database statistics")
def get_stats(
    include_details: bool = Query(default=False),
    thread_limit: int = Query(default=50, ge=1, le=500),
    thread_offset: int = Query(default=0, ge=0),
    sender_limit: int = Query(default=50, ge=1, le=500),
    sender_offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    if not include_details:
        return store.stats_summary()
    return store.stats(
        thread_limit=thread_limit,
        thread_offset=thread_offset,
        sender_limit=sender_limit,
        sender_offset=sender_offset,
    )


@router.get("/stats/summary", summary="Database statistics summary")
def get_stats_summary() -> dict[str, Any]:
    return store.stats_summary()


@router.get("/stats/threads", summary="Paginated thread statistics")
def get_thread_stats(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return store.stats_threads(limit=limit, offset=offset)


@router.get("/stats/senders", summary="Paginated sender statistics")
def get_sender_stats(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return store.stats_senders(limit=limit, offset=offset)
