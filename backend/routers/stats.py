"""Stats router — exposes store.stats()."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from wechat_rag_agent import store


router = APIRouter(prefix="/api", tags=["stats"])


@router.get("/stats", summary="数据库统计概况")
def get_stats() -> Any:
    """获取数据库全貌。"""
    return store.stats()
