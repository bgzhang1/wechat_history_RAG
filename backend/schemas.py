"""Pydantic schemas for the FastAPI backend."""

from __future__ import annotations

from typing import Any
from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: str = Field(..., description="角色：user 或 assistant")
    content: str = Field(..., description="消息内容")


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, description="用户问题")
    session_id: str | None = Field(default=None, description="会话 ID，不传则创建新会话")


class ChatResponse(BaseModel):
    answer: str
    session_id: str


class SettingsModel(BaseModel):
    system_prompt: str | None = Field(default=None, description="系统提示词")
    max_rounds: int | None = Field(default=None, ge=1, description="最大工具调用轮数")
    max_history_messages: int | None = Field(default=None, ge=0, description="最大携带历史消息数")
    chat_model: str | None = Field(default=None, description="大语言模型名称")
    chat_timeout: float | None = Field(default=None, ge=1.0, description="单次请求超时时间(秒)")
    chat_temperature: float | None = Field(default=None, ge=0.0, le=2.0, description="生成温度/思考程度")
    enabled_tools: list[str] | None = Field(default=None, description="启用的 RAG 工具列表")
