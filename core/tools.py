from __future__ import annotations

import json
import re
from datetime import datetime
from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator

from . import retrieval, store


ISO_RE = re.compile(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2}(\.\d{1,6})?)?([Zz]|[+-]\d{2}:\d{2})?)?$")
TZ_SUFFIX_RE = re.compile(r"([Zz]|[+-]\d{2}:\d{2})$")
MAX_TOOL_QUERY_CHARS = 500
MAX_SEMANTIC_QUERY_CHARS = 2000
MAX_FILTER_CHARS = 200
MAX_MESSAGE_ID_CHARS = store.MAX_MESSAGE_ID_CHARS


def _json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False)


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def _clean_optional_text(value: Any) -> str | None:
    cleaned = _clean_text(value)
    return cleaned or None


class TimeMixin(BaseModel):
    @staticmethod
    def _normalize_time(value: str, *, is_before: bool = False) -> str:
        normalized = value.strip().replace(" ", "T")
        normalized = TZ_SUFFIX_RE.sub("", normalized)
        if len(normalized) == 10:
            return f"{normalized}T23:59:59" if is_before else f"{normalized}T00:00:00"
        return datetime.fromisoformat(normalized).isoformat(timespec="seconds")

    @classmethod
    def _parse_time(cls, value: str, *, is_before: bool = False) -> datetime:
        normalized = cls._normalize_time(value, is_before=is_before)
        return datetime.fromisoformat(normalized)

    @field_validator("after", "before", check_fields=False)
    @classmethod
    def validate_iso(cls, value: str | None, info: ValidationInfo) -> str | None:
        if value is not None and not ISO_RE.match(value):
            raise ValueError("时间格式无效，请用 ISO 8601，如 2026-06-12 或 2026-06-12 20:30")
        if value is not None:
            is_before = info.field_name == "before"
            try:
                cls._parse_time(value, is_before=is_before)
            except ValueError as exc:
                raise ValueError("时间值无效，请检查年月日和时分秒") from exc
            return cls._normalize_time(value, is_before=is_before)
        return value

    @model_validator(mode="after")
    def validate_time_range(self) -> "TimeMixin":
        after = getattr(self, "after", None)
        before = getattr(self, "before", None)
        if after and before and self._parse_time(after) > self._parse_time(before, is_before=True):
            raise ValueError("起始时间不能晚于结束时间")
        return self


class SearchArgs(TimeMixin):
    query: str = Field(..., min_length=1, max_length=MAX_TOOL_QUERY_CHARS, description="搜索关键词，多个词用空格分隔")
    sender: str | None = Field(default=None, max_length=MAX_FILTER_CHARS, description='只搜此发送人，可选。传 "我" 表示只搜自己发的消息')
    thread: str | None = Field(default=None, max_length=MAX_FILTER_CHARS, description="只搜此群聊/会话，可选")
    after: str | None = Field(default=None, description="起始时间 ISO 格式，可选")
    before: str | None = Field(default=None, description="结束时间 ISO 格式，可选")
    limit: int | None = Field(default=None, gt=0, le=100, description="返回条数，默认 20，最多 100")
    offset: int | None = Field(default=None, ge=0, description="分页偏移，默认 0")

    @field_validator("query", mode="before")
    @classmethod
    def normalize_query(cls, value: Any) -> str:
        return _clean_text(value)

    @field_validator("after", "before", mode="before")
    @classmethod
    def normalize_optional_time(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("sender", "thread", mode="before")
    @classmethod
    def normalize_optional_filter(cls, value: Any) -> str | None:
        return _clean_optional_text(value)


class SemanticArgs(TimeMixin):
    query: str = Field(..., min_length=1, max_length=MAX_SEMANTIC_QUERY_CHARS, description="用完整自然语言描述要找的内容，不要只给关键词")
    thread: str | None = Field(default=None, max_length=MAX_FILTER_CHARS, description="限定群聊/会话，可选")
    after: str | None = Field(default=None, description="起始时间 ISO 格式，可选")
    before: str | None = Field(default=None, description="结束时间 ISO 格式，可选")
    limit: int | None = Field(default=None, gt=0, le=20, description="返回会话块数，默认 8，最多 20")

    @field_validator("query", mode="before")
    @classmethod
    def normalize_query(cls, value: Any) -> str:
        return _clean_text(value)

    @field_validator("after", "before", mode="before")
    @classmethod
    def normalize_optional_time(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("thread", mode="before")
    @classmethod
    def normalize_optional_filter(cls, value: Any) -> str | None:
        return _clean_optional_text(value)


class ContextArgs(BaseModel):
    message_id: str = Field(..., min_length=1, max_length=MAX_MESSAGE_ID_CHARS)
    before: int | None = Field(default=None, ge=0, le=50, description="向前取几条，默认 15，最多 50")
    after: int | None = Field(default=None, ge=0, le=50, description="向后取几条，默认 15，最多 50")

    @field_validator("message_id", mode="before")
    @classmethod
    def normalize_message_id(cls, value: Any) -> str:
        return str(value or "").strip()


class BrowseArgs(TimeMixin):
    after: str = Field(..., description="起始时间 ISO 格式")
    before: str = Field(..., description="结束时间 ISO 格式")
    thread: str | None = Field(default=None, max_length=MAX_FILTER_CHARS, description="限定会话，可选")
    sender: str | None = Field(default=None, max_length=MAX_FILTER_CHARS, description="限定发送人，可选")
    limit: int | None = Field(default=None, gt=0, le=200, description="默认 50，最多 200")
    offset: int | None = Field(default=None, ge=0)

    @field_validator("sender", "thread", mode="before")
    @classmethod
    def normalize_optional_filter(cls, value: Any) -> str | None:
        return _clean_optional_text(value)


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
