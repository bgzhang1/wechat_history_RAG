"""Settings router - view, update, and reset runtime configuration."""

from __future__ import annotations

import json
import os
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any

import core.agent as agent_module
import core.llm as llm_module
from core.tools import TOOLS_BY_NAME
from fastapi import APIRouter, HTTPException

from ..logging_utils import get_logger
from ..redaction import public_exception_message
from ..schemas import SettingsModel, SettingsResponseModel


router = APIRouter(prefix="/api/settings", tags=["settings"])
logger = get_logger()

_DEFAULT_SYSTEM_PROMPT = agent_module.SYSTEM_PROMPT
_DEFAULT_MAX_ROUNDS = agent_module.MAX_ROUNDS
_DEFAULT_MAX_HISTORY_MESSAGES = agent_module.MAX_HISTORY_MESSAGES
_DEFAULT_CHAT_TEMPERATURE = llm_module.CHAT_TEMPERATURE
_DEFAULT_ENABLED_TOOLS = list(agent_module.ENABLED_TOOLS)
SETTINGS_PATH = Path(os.getenv("BACKEND_SETTINGS_FILE", str(Path("runtime") / "backend_settings.json")))
_settings_lock = threading.RLock()


def _clear_model_cache() -> None:
    llm_module._chat_model_cached.cache_clear()


def _validate_enabled_tools(tool_names: list[str]) -> list[str]:
    normalized = list(dict.fromkeys(name.strip() for name in tool_names if name and name.strip()))
    if not normalized:
        raise HTTPException(status_code=400, detail="至少需要启用一个检索工具。")
    unknown = [name for name in normalized if name not in TOOLS_BY_NAME]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"未知检索工具：{', '.join(unknown)}。",
        )
    return normalized


def _available_tool_names() -> list[str]:
    return list(TOOLS_BY_NAME)


def _runtime_settings_unlocked() -> SettingsModel:
    return SettingsModel(
        system_prompt=agent_module.SYSTEM_PROMPT,
        max_rounds=agent_module.MAX_ROUNDS,
        max_history_messages=agent_module.MAX_HISTORY_MESSAGES,
        chat_model=llm_module.CHAT_MODEL_OVERRIDE or llm_module._env_config_value("CHAT_MODEL"),
        chat_timeout=(
            llm_module.CHAT_TIMEOUT_OVERRIDE
            if llm_module.CHAT_TIMEOUT_OVERRIDE is not None
            else llm_module._env_float("CHAT_TIMEOUT", 300.0, minimum=1.0)
        ),
        chat_temperature=llm_module.CHAT_TEMPERATURE,
        enabled_tools=list(agent_module.ENABLED_TOOLS),
    )


def _current_settings_unlocked() -> SettingsResponseModel:
    return SettingsResponseModel(
        **_runtime_settings_unlocked().model_dump(),
        available_tools=_available_tool_names(),
    )


def _chat_model_override_from_value(value: str | None) -> str | None:
    model = (value or "").strip()
    env_model = llm_module.os.environ.get("CHAT_MODEL", "").strip()
    if not model or model == env_model:
        return None
    return model


def _chat_timeout_override_from_value(value: float | None) -> float | None:
    if value is None:
        return None
    env_timeout = llm_module._env_float("CHAT_TIMEOUT", 300.0, minimum=1.0)
    return None if float(value) == env_timeout else float(value)


def _field_provided(req: SettingsModel, field: str) -> bool:
    return field in req.model_fields_set


def _apply_settings_unlocked(req: SettingsModel) -> bool:
    enabled_tools = _validate_enabled_tools(req.enabled_tools) if req.enabled_tools is not None else None
    cache_needs_clear = False
    if req.system_prompt is not None:
        agent_module.SYSTEM_PROMPT = req.system_prompt
    if req.max_rounds is not None:
        agent_module.MAX_ROUNDS = req.max_rounds
    if req.max_history_messages is not None:
        agent_module.MAX_HISTORY_MESSAGES = req.max_history_messages
    if _field_provided(req, "chat_model"):
        llm_module.CHAT_MODEL_OVERRIDE = _chat_model_override_from_value(req.chat_model)
        cache_needs_clear = True
    if _field_provided(req, "chat_timeout"):
        llm_module.CHAT_TIMEOUT_OVERRIDE = _chat_timeout_override_from_value(req.chat_timeout)
        cache_needs_clear = True
    if req.chat_temperature is not None:
        llm_module.CHAT_TEMPERATURE = req.chat_temperature
        cache_needs_clear = True
    if enabled_tools is not None:
        agent_module.ENABLED_TOOLS = enabled_tools

    if cache_needs_clear:
        _clear_model_cache()
    return cache_needs_clear


def _persisted_settings_after_update_unlocked(req: SettingsModel) -> SettingsModel:
    payload = _runtime_settings_unlocked().model_dump()
    payload["chat_model"] = llm_module.CHAT_MODEL_OVERRIDE
    payload["chat_timeout"] = llm_module.CHAT_TIMEOUT_OVERRIDE
    if req.system_prompt is not None:
        payload["system_prompt"] = req.system_prompt
    if req.max_rounds is not None:
        payload["max_rounds"] = req.max_rounds
    if req.max_history_messages is not None:
        payload["max_history_messages"] = req.max_history_messages
    if _field_provided(req, "chat_model"):
        payload["chat_model"] = _chat_model_override_from_value(req.chat_model)
    if _field_provided(req, "chat_timeout"):
        payload["chat_timeout"] = _chat_timeout_override_from_value(req.chat_timeout)
    if req.chat_temperature is not None:
        payload["chat_temperature"] = req.chat_temperature
    if req.enabled_tools is not None:
        payload["enabled_tools"] = _validate_enabled_tools(req.enabled_tools)
    return SettingsModel.model_validate(payload)


def _save_settings_unlocked(payload: SettingsModel) -> None:
    SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = payload.model_dump()
    temp_path = SETTINGS_PATH.with_suffix(SETTINGS_PATH.suffix + ".tmp")
    try:
        temp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(SETTINGS_PATH)
    except Exception:
        with suppress(OSError):
            temp_path.unlink(missing_ok=True)
        raise


def _load_persisted_settings() -> None:
    if not SETTINGS_PATH.exists():
        return
    try:
        raw: dict[str, Any] = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        req = SettingsModel.model_validate(raw)
        with _settings_lock:
            _apply_settings_unlocked(req)
    except Exception as exc:
        logger.warning(
            "Runtime settings load failed",
            extra={
                "settings_file": str(SETTINGS_PATH),
                "error": public_exception_message("加载持久化设置失败", exc),
            },
        )
        return


def _clear_persisted_settings_unlocked() -> None:
    SETTINGS_PATH.with_suffix(SETTINGS_PATH.suffix + ".tmp").unlink(missing_ok=True)
    SETTINGS_PATH.unlink(missing_ok=True)


_load_persisted_settings()


@router.get("", response_model=SettingsResponseModel, summary="Get system settings")
def get_settings() -> SettingsResponseModel:
    with _settings_lock:
        return _current_settings_unlocked()


@router.post("", response_model=SettingsResponseModel, summary="Update system settings")
def update_settings(req: SettingsModel) -> SettingsResponseModel:
    with _settings_lock:
        payload = _persisted_settings_after_update_unlocked(req)
        _save_settings_unlocked(payload)
        _apply_settings_unlocked(req)
        return _current_settings_unlocked()


@router.post("/reset", response_model=SettingsResponseModel, summary="Reset system settings")
def reset_settings() -> SettingsResponseModel:
    with _settings_lock:
        _clear_persisted_settings_unlocked()
        agent_module.SYSTEM_PROMPT = _DEFAULT_SYSTEM_PROMPT
        agent_module.MAX_ROUNDS = _DEFAULT_MAX_ROUNDS
        agent_module.MAX_HISTORY_MESSAGES = _DEFAULT_MAX_HISTORY_MESSAGES
        agent_module.ENABLED_TOOLS = list(_DEFAULT_ENABLED_TOOLS)
        llm_module.CHAT_MODEL_OVERRIDE = None
        llm_module.CHAT_TIMEOUT_OVERRIDE = None
        llm_module.CHAT_TEMPERATURE = _DEFAULT_CHAT_TEMPERATURE
        _clear_model_cache()
        return _current_settings_unlocked()
