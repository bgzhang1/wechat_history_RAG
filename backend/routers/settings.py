"""Settings router — to view and modify runtime configuration."""

from __future__ import annotations

import wechat_rag_agent.agent as agent_module
import wechat_rag_agent.llm as llm_module
from fastapi import APIRouter

from ..schemas import SettingsModel

router = APIRouter(prefix="/api/settings", tags=["settings"])


@router.get("", response_model=SettingsModel, summary="获取系统设置")
def get_settings() -> SettingsModel:
    return SettingsModel(
        system_prompt=agent_module.SYSTEM_PROMPT,
        max_rounds=agent_module.MAX_ROUNDS,
        max_history_messages=agent_module.MAX_HISTORY_MESSAGES,
        chat_model=llm_module.CHAT_MODEL_OVERRIDE or llm_module.os.environ.get("CHAT_MODEL", ""),
        chat_timeout=llm_module.CHAT_TIMEOUT_OVERRIDE if llm_module.CHAT_TIMEOUT_OVERRIDE is not None else llm_module._env_float("CHAT_TIMEOUT", 300.0, minimum=1.0),
        chat_temperature=llm_module.CHAT_TEMPERATURE,
        enabled_tools=agent_module.ENABLED_TOOLS,
    )


@router.post("", response_model=SettingsModel, summary="更新系统设置")
def update_settings(req: SettingsModel) -> SettingsModel:
    if req.system_prompt is not None:
        agent_module.SYSTEM_PROMPT = req.system_prompt
    if req.max_rounds is not None:
        agent_module.MAX_ROUNDS = req.max_rounds
    if req.max_history_messages is not None:
        agent_module.MAX_HISTORY_MESSAGES = req.max_history_messages
    if req.chat_model is not None:
        llm_module.CHAT_MODEL_OVERRIDE = req.chat_model
    if req.chat_timeout is not None:
        llm_module.CHAT_TIMEOUT_OVERRIDE = req.chat_timeout
    if req.chat_temperature is not None:
        llm_module.CHAT_TEMPERATURE = req.chat_temperature
    if req.enabled_tools is not None:
        agent_module.ENABLED_TOOLS = req.enabled_tools
        
    return get_settings()
