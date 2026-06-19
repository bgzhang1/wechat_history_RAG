"""Pydantic schemas for the FastAPI backend."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, field_validator

ToolName = Annotated[str, Field(min_length=1, max_length=64)]


class ChatMessage(BaseModel):
    role: str = Field(..., description="角色：user 或 assistant")
    content: str = Field(..., description="消息内容")


class ChatRequest(BaseModel):
    question: str = Field(..., min_length=1, max_length=8000, description="用户问题")
    session_id: str | None = Field(default=None, max_length=120, description="会话 ID，不传则创建新会话")

    @field_validator("question", mode="before")
    @classmethod
    def normalize_question(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("question cannot be empty")
        return normalized

    @field_validator("session_id", mode="before")
    @classmethod
    def normalize_session_id(cls, value: object) -> object:
        if value is None:
            return value
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("session_id cannot be empty")
        return normalized


class ChatResponse(BaseModel):
    answer: str
    session_id: str


class SettingsModel(BaseModel):
    system_prompt: str | None = Field(default=None, max_length=20000, description="系统提示词")
    max_rounds: int | None = Field(default=None, ge=1, le=200, description="最大工具调用轮数")
    max_history_messages: int | None = Field(default=None, ge=0, le=200, description="最大携带历史消息数")
    chat_model: str | None = Field(default=None, max_length=200, description="大语言模型名称")
    chat_timeout: float | None = Field(default=None, ge=1.0, le=1800.0, description="单次请求超时时间(秒)")
    chat_temperature: float | None = Field(default=None, ge=0.0, le=2.0, description="生成温度/思考程度")
    enabled_tools: list[ToolName] | None = Field(default=None, min_length=1, max_length=20, description="启用的 RAG 工具列表")

    @field_validator("system_prompt", mode="before")
    @classmethod
    def normalize_system_prompt(cls, value: object) -> object:
        if value is None:
            return value
        if not isinstance(value, str):
            return value
        normalized = value.strip()
        if not normalized:
            raise ValueError("system_prompt cannot be empty")
        return normalized

    @field_validator("chat_model", mode="before")
    @classmethod
    def normalize_chat_model(cls, value: object) -> object:
        if value is None:
            return value
        if not isinstance(value, str):
            return value
        return value.strip()

    @field_validator("enabled_tools", mode="before")
    @classmethod
    def normalize_enabled_tools(cls, value: object) -> object:
        if value is None:
            return value
        if not isinstance(value, list):
            return value
        return [item.strip() if isinstance(item, str) else item for item in value]


class SettingsResponseModel(SettingsModel):
    available_tools: list[ToolName] = Field(
        default_factory=list,
        max_length=100,
        description="后端当前注册的 RAG 工具名称列表，供前端展示可选工具",
    )
