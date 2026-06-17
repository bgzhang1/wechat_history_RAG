"""Settings router - view, update, and reset runtime configuration."""

from __future__ import annotations

import core.agent as agent_module
import core.llm as llm_module
from fastapi import APIRouter

from ..schemas import SettingsModel


router = APIRouter(prefix="/api/settings", tags=["settings"])

_DEFAULT_SYSTEM_PROMPT = agent_module.SYSTEM_PROMPT
_DEFAULT_MAX_ROUNDS = agent_module.MAX_ROUNDS
_DEFAULT_MAX_HISTORY_MESSAGES = agent_module.MAX_HISTORY_MESSAGES
_DEFAULT_CHAT_TEMPERATURE = llm_module.CHAT_TEMPERATURE
_DEFAULT_ENABLED_TOOLS = list(agent_module.ENABLED_TOOLS)


def _clear_model_cache() -> None:
    llm_module._chat_model_cached.cache_clear()


@router.get("", response_model=SettingsModel, summary="Get system settings")
def get_settings() -> SettingsModel:
    return SettingsModel(
        system_prompt=agent_module.SYSTEM_PROMPT,
        max_rounds=agent_module.MAX_ROUNDS,
        max_history_messages=agent_module.MAX_HISTORY_MESSAGES,
        chat_model=llm_module.CHAT_MODEL_OVERRIDE or llm_module.os.environ.get("CHAT_MODEL", ""),
        chat_timeout=(
            llm_module.CHAT_TIMEOUT_OVERRIDE
            if llm_module.CHAT_TIMEOUT_OVERRIDE is not None
            else llm_module._env_float("CHAT_TIMEOUT", 300.0, minimum=1.0)
        ),
        chat_temperature=llm_module.CHAT_TEMPERATURE,
        enabled_tools=agent_module.ENABLED_TOOLS,
    )


@router.post("", response_model=SettingsModel, summary="Update system settings")
def update_settings(req: SettingsModel) -> SettingsModel:
    cache_needs_clear = False
    if req.system_prompt is not None:
        agent_module.SYSTEM_PROMPT = req.system_prompt
    if req.max_rounds is not None:
        agent_module.MAX_ROUNDS = req.max_rounds
    if req.max_history_messages is not None:
        agent_module.MAX_HISTORY_MESSAGES = req.max_history_messages
    if req.chat_model is not None:
        llm_module.CHAT_MODEL_OVERRIDE = req.chat_model
        cache_needs_clear = True
    if req.chat_timeout is not None:
        llm_module.CHAT_TIMEOUT_OVERRIDE = req.chat_timeout
        cache_needs_clear = True
    if req.chat_temperature is not None:
        llm_module.CHAT_TEMPERATURE = req.chat_temperature
        cache_needs_clear = True
    if req.enabled_tools is not None:
        agent_module.ENABLED_TOOLS = req.enabled_tools

    if cache_needs_clear:
        _clear_model_cache()

    return get_settings()


@router.post("/reset", response_model=SettingsModel, summary="Reset system settings")
def reset_settings() -> SettingsModel:
    agent_module.SYSTEM_PROMPT = _DEFAULT_SYSTEM_PROMPT
    agent_module.MAX_ROUNDS = _DEFAULT_MAX_ROUNDS
    agent_module.MAX_HISTORY_MESSAGES = _DEFAULT_MAX_HISTORY_MESSAGES
    agent_module.ENABLED_TOOLS = list(_DEFAULT_ENABLED_TOOLS)
    llm_module.CHAT_MODEL_OVERRIDE = None
    llm_module.CHAT_TIMEOUT_OVERRIDE = None
    llm_module.CHAT_TEMPERATURE = _DEFAULT_CHAT_TEMPERATURE
    _clear_model_cache()
    return get_settings()
