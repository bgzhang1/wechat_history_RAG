"""Stats router - exposes lightweight and paginated database statistics."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query

from core import store
from .params import query_bool, query_int


router = APIRouter(prefix="/api", tags=["stats"])


@router.get("/stats", summary="Database statistics")
def get_stats(
    include_details: bool = Query(default=False),
    thread_limit: int = Query(default=50, ge=1, le=500),
    thread_offset: int = Query(default=0, ge=0),
    sender_limit: int = Query(default=50, ge=1, le=500),
    sender_offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    safe_include_details = query_bool(include_details, False)
    safe_thread_limit = query_int(thread_limit, 50, minimum=1, maximum=500)
    safe_thread_offset = query_int(thread_offset, 0, minimum=0)
    safe_sender_limit = query_int(sender_limit, 50, minimum=1, maximum=500)
    safe_sender_offset = query_int(sender_offset, 0, minimum=0)

    if not safe_include_details:
        return store.stats_summary()
    return store.stats(
        thread_limit=safe_thread_limit,
        thread_offset=safe_thread_offset,
        sender_limit=safe_sender_limit,
        sender_offset=safe_sender_offset,
    )


@router.get("/stats/summary", summary="Database statistics summary")
def get_stats_summary() -> dict[str, Any]:
    return store.stats_summary()


@router.get("/stats/threads", summary="Paginated thread statistics")
def get_thread_stats(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return store.stats_threads(
        limit=query_int(limit, 50, minimum=1, maximum=500),
        offset=query_int(offset, 0, minimum=0),
    )


@router.get("/stats/senders", summary="Paginated sender statistics")
def get_sender_stats(
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    return store.stats_senders(
        limit=query_int(limit, 50, minimum=1, maximum=500),
        offset=query_int(offset, 0, minimum=0),
    )
