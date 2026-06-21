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
    chat_base_url: str | None = Field(default=None, max_length=500, description="OpenAI 兼容聊天模型 Base URL")
    chat_model: str | None = Field(default=None, max_length=200, description="大语言模型名称")
    chat_reasoning_effort: str | None = Field(default=None, max_length=20, description="对话模型思考强度：low / medium / high，留空则不传")
    chat_max_retries: int | None = Field(default=None, ge=0, le=10, description="聊天模型 SDK 重试次数")
    summary_base_url: str | None = Field(default=None, max_length=500, description="OpenAI 兼容摘要模型 Base URL，留空继承聊天配置")
    summary_model: str | None = Field(default=None, max_length=200, description="摘要生成模型名称")
    summary_reasoning_effort: str | None = Field(default=None, max_length=20, description="摘要模型思考强度，留空继承对话配置")
    summary_workers: int | None = Field(default=None, ge=1, le=32, description="摘要生成并发线程数")
    summary_batch_size: int | None = Field(default=None, ge=1, le=128, description="摘要生成单批会话块数量")
    summary_max_chars: int | None = Field(default=None, ge=100, le=20000, description="摘要单块最大输入字符数")
    summary_fallback_chars: int | None = Field(default=None, ge=0, le=20000, description="摘要失败后回退输入字符数")
    embed_base_url: str | None = Field(default=None, max_length=500, description="OpenAI 兼容 Embedding Base URL")
    embed_model: str | None = Field(default=None, max_length=200, description="Embedding 模型名称")
    embed_timeout: float | None = Field(default=None, ge=1.0, le=1800.0, description="Embedding 请求超时时间(秒)")
    embed_max_retries: int | None = Field(default=None, ge=0, le=10, description="Embedding SDK 重试次数")
    embed_workers: int | None = Field(default=None, ge=1, le=64, description="Embedding 并发线程数")
    embed_batch_size: int | None = Field(default=None, ge=1, le=512, description="Embedding 单批会话块数量")
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

    @field_validator("chat_base_url", "chat_model", "chat_reasoning_effort", "summary_base_url", "summary_model", "summary_reasoning_effort", "embed_base_url", "embed_model", mode="before")
    @classmethod
    def normalize_text_setting(cls, value: object) -> object:
        if value is None:
            return value
        if not isinstance(value, str):
            return value
        return value.strip()

    @field_validator("chat_reasoning_effort", "summary_reasoning_effort")
    @classmethod
    def validate_reasoning_effort(cls, value: str | None) -> str | None:
        if value is None or value == "":
            return value
        normalized = value.lower()
        if normalized not in {"low", "medium", "high"}:
            raise ValueError("reasoning effort must be one of: low, medium, high")
        return normalized

    @field_validator("enabled_tools", mode="before")
    @classmethod
    def normalize_enabled_tools(cls, value: object) -> object:
        if value is None:
            return value
        if not isinstance(value, list):
            return value
        return [item.strip() if isinstance(item, str) else item for item in value]


class SettingsUpdateModel(SettingsModel):
    chat_api_key: str | None = Field(default=None, max_length=1000, description="聊天模型 API Key，只用于更新，不会在响应中返回")
    summary_api_key: str | None = Field(default=None, max_length=1000, description="摘要模型 API Key，留空继承聊天配置")

    @field_validator("chat_api_key", "summary_api_key", mode="before")
    @classmethod
    def normalize_key_setting(cls, value: object) -> object:
        if value is None:
            return value
        if not isinstance(value, str):
            return value
        return value.strip()


class SettingsResponseModel(SettingsModel):
    available_tools: list[ToolName] = Field(
        default_factory=list,
        max_length=100,
        description="后端当前注册的 RAG 工具名称列表，供前端展示可选工具",
    )
    chat_api_key_configured: bool = Field(default=False, description="是否已配置 CHAT_API_KEY，不返回密钥内容")
    summary_api_key_configured: bool = Field(default=False, description="是否已配置 SUMMARY_API_KEY，未配置时摘要会继承聊天 API Key")
    summary_api_key_inherited: bool = Field(default=True, description="摘要 API Key 是否继承聊天配置")
    summary_base_url_inherited: bool = Field(default=True, description="摘要 Base URL 是否继承聊天配置")
    summary_reasoning_effort_inherited: bool = Field(default=True, description="摘要思考强度是否继承对话配置")
    embed_api_key_configured: bool = Field(default=False, description="是否已配置 EMBED_API_KEY，不返回密钥内容")
