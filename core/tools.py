from __future__ import annotations

import json
import re
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field, field_validator

from . import retrieval, store


ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$")


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


class TimeMixin(BaseModel):
    @field_validator("after", "before", check_fields=False)
    @classmethod
    def validate_iso(cls, value: str | None) -> str | None:
        if value is not None and not ISO_RE.match(value):
            raise ValueError("时间格式无效，请用 ISO 8601，如 2026-06-12 或 2026-06-12 20:30")
        return value


class SearchArgs(TimeMixin):
    query: str = Field(..., min_length=1, description="搜索关键词，多个词用空格分隔")
    sender: str | None = Field(default=None, description='只搜此发送人，可选。传 "我" 表示只搜自己发的消息')
    thread: str | None = Field(default=None, description="只搜此群聊/会话，可选")
    after: str | None = Field(default=None, description="起始时间 ISO 格式，可选")
    before: str | None = Field(default=None, description="结束时间 ISO 格式，可选")
    limit: int | None = Field(default=None, gt=0, description="返回条数，默认 20")
    offset: int | None = Field(default=None, ge=0, description="分页偏移，默认 0")


class SemanticArgs(TimeMixin):
    query: str = Field(..., min_length=1, description="用完整自然语言描述要找的内容，不要只给关键词")
    thread: str | None = Field(default=None, description="限定群聊/会话，可选")
    after: str | None = Field(default=None, description="起始时间 ISO 格式，可选")
    before: str | None = Field(default=None, description="结束时间 ISO 格式，可选")
    limit: int | None = Field(default=None, gt=0, description="返回会话块数，默认 8")


class ContextArgs(BaseModel):
    message_id: str = Field(..., min_length=1)
    before: int | None = Field(default=None, ge=0, description="向前取几条，默认 15")
    after: int | None = Field(default=None, ge=0, description="向后取几条，默认 15")


class BrowseArgs(TimeMixin):
    after: str = Field(..., description="起始时间 ISO 格式")
    before: str = Field(..., description="结束时间 ISO 格式")
    thread: str | None = Field(default=None, description="限定会话，可选")
    sender: str | None = Field(default=None, description="限定发送人，可选")
    limit: int | None = Field(default=None, gt=0, description="默认 50")
    offset: int | None = Field(default=None, ge=0)


@tool("search_messages", args_schema=SearchArgs)
def search_messages(**kwargs: Any) -> str:
    """按关键词精确全文检索聊天消息。问题包含人名、店名、物品、专有名词或原话片段时优先使用。"""
    return _json(store.search_messages({key: value for key, value in kwargs.items() if value is not None}))


@tool("semantic_search", args_schema=SemanticArgs)
def semantic_search(**kwargs: Any) -> str:
    """语义检索。问题模糊、主题性强、用户不记得原话，或关键词搜索无结果时使用。"""
    return _json(retrieval.semantic_search({key: value for key, value in kwargs.items() if value is not None}))


@tool("get_context", args_schema=ContextArgs)
def get_context(**kwargs: Any) -> str:
    """获取某条消息前后的完整上下文。回答关键命中前用它确认前因后果，避免断章取义。"""
    return _json(store.get_context({key: value for key, value in kwargs.items() if value is not None}))


@tool("browse_by_time", args_schema=BrowseArgs)
def browse_by_time(**kwargs: Any) -> str:
    """按时间范围顺序浏览消息。回答某天、某晚、某段时间聊了什么这类问题时使用。"""
    return _json(store.browse({key: value for key, value in kwargs.items() if value is not None}))


@tool("get_stats")
def get_stats() -> str:
    """获取数据全貌，包括会话列表、参与者、时间跨度、消息总量、各人消息数。"""
    return _json(store.stats())


TOOLS = [search_messages, semantic_search, get_context, browse_by_time, get_stats]
TOOLS_BY_NAME = {item.name: item for item in TOOLS}
