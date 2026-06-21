"""Settings router - view, update, and reset runtime configuration."""

from __future__ import annotations

import json
import os
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any
from urllib import error as urlerror
from urllib import request as urlrequest

import core.agent as agent_module
import core.llm as llm_module
from core.tools import TOOLS_BY_NAME
from fastapi import APIRouter, HTTPException, Query

from ..logging_utils import get_logger
from ..redaction import public_exception_message
from ..schemas import SettingsResponseModel, SettingsUpdateModel


router = APIRouter(prefix="/api/settings", tags=["settings"])
logger = get_logger()

_DEFAULT_SYSTEM_PROMPT = agent_module.SYSTEM_PROMPT
_DEFAULT_MAX_ROUNDS = agent_module.MAX_ROUNDS
_DEFAULT_MAX_HISTORY_MESSAGES = agent_module.MAX_HISTORY_MESSAGES
_DEFAULT_CHAT_TEMPERATURE = llm_module.CHAT_TEMPERATURE
_DEFAULT_ENABLED_TOOLS = list(agent_module.ENABLED_TOOLS)
_DEFAULT_SUMMARY_MODEL_ENV = os.environ.get("SUMMARY_MODEL", "").strip()
_ENV_RUNTIME_FIELDS: dict[str, str] = {
    "chat_base_url": "CHAT_BASE_URL",
    "chat_api_key": "CHAT_API_KEY",
    "chat_reasoning_effort": "CHAT_REASONING_EFFORT",
    "chat_max_retries": "CHAT_MAX_RETRIES",
    "summary_base_url": "SUMMARY_BASE_URL",
    "summary_api_key": "SUMMARY_API_KEY",
    "summary_reasoning_effort": "SUMMARY_REASONING_EFFORT",
    "summary_workers": "SUMMARY_WORKERS",
    "summary_batch_size": "SUMMARY_BATCH_SIZE",
    "summary_max_chars": "SUMMARY_MAX_CHARS",
    "summary_fallback_chars": "SUMMARY_FALLBACK_CHARS",
    "embed_base_url": "EMBED_BASE_URL",
    "embed_model": "EMBED_MODEL",
    "embed_timeout": "EMBED_TIMEOUT",
    "embed_max_retries": "EMBED_MAX_RETRIES",
    "embed_workers": "EMBED_WORKERS",
    "embed_batch_size": "EMBED_BATCH_SIZE",
}
_DEFAULT_ENV_VALUES = {name: os.environ.get(name) for name in _ENV_RUNTIME_FIELDS.values()}
SETTINGS_PATH = Path(os.getenv("BACKEND_SETTINGS_FILE", str(Path("runtime") / "backend_settings.json")))
_settings_lock = threading.RLock()
_summary_model_override: str | None = None


def _clear_model_cache() -> None:
    llm_module._chat_model_cached.cache_clear()
    llm_module._embed_model_cached.cache_clear()


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


def _effective_summary_model_unlocked() -> str:
    return _summary_model_override or _DEFAULT_SUMMARY_MODEL_ENV


def _summary_model_override_from_value(value: str | None) -> str | None:
    model = (value or "").strip()
    if not model or model == _DEFAULT_SUMMARY_MODEL_ENV:
        return None
    return model


def _set_summary_model_override_unlocked(value: str | None) -> None:
    global _summary_model_override
    _summary_model_override = value
    if value:
        os.environ["SUMMARY_MODEL"] = value
    elif _DEFAULT_SUMMARY_MODEL_ENV:
        os.environ["SUMMARY_MODEL"] = _DEFAULT_SUMMARY_MODEL_ENV
    else:
        os.environ.pop("SUMMARY_MODEL", None)


def _env_display_value(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


def _env_int_display(name: str, default: int, minimum: int = 0) -> int:
    return llm_module._env_int(name, default, minimum=minimum)


def _env_float_display(name: str, default: float, minimum: float = 0.0) -> float:
    return llm_module._env_float(name, default, minimum=minimum)


def _env_override_from_value(env_name: str, value: object) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip()
    default_value = (_DEFAULT_ENV_VALUES.get(env_name) or "").strip()
    if not normalized or normalized == default_value:
        return None
    return normalized


def _set_env_override_unlocked(env_name: str, value: str | None) -> None:
    if value is not None:
        os.environ[env_name] = value
        return
    default = _DEFAULT_ENV_VALUES.get(env_name)
    if default is None:
        os.environ.pop(env_name, None)
    else:
        os.environ[env_name] = default


def _runtime_settings_unlocked() -> SettingsUpdateModel:
    return SettingsUpdateModel(
        system_prompt=agent_module.SYSTEM_PROMPT,
        max_rounds=agent_module.MAX_ROUNDS,
        max_history_messages=agent_module.MAX_HISTORY_MESSAGES,
        chat_base_url=_env_display_value("CHAT_BASE_URL"),
        chat_api_key=_env_display_value("CHAT_API_KEY"),
        chat_model=llm_module.CHAT_MODEL_OVERRIDE or llm_module._env_config_value("CHAT_MODEL"),
        chat_reasoning_effort=llm_module._chat_reasoning_effort(),
        chat_max_retries=_env_int_display("CHAT_MAX_RETRIES", 3, minimum=0),
        summary_base_url=_env_display_value("SUMMARY_BASE_URL"),
        summary_api_key=_env_display_value("SUMMARY_API_KEY"),
        summary_model=_effective_summary_model_unlocked(),
        summary_reasoning_effort=llm_module._reasoning_effort_value("SUMMARY_REASONING_EFFORT"),
        summary_workers=_env_int_display("SUMMARY_WORKERS", 2, minimum=1),
        summary_batch_size=_env_int_display("SUMMARY_BATCH_SIZE", 4, minimum=1),
        summary_max_chars=_env_int_display("SUMMARY_MAX_CHARS", 3000, minimum=1),
        summary_fallback_chars=_env_int_display("SUMMARY_FALLBACK_CHARS", 1200, minimum=0),
        embed_base_url=_env_display_value("EMBED_BASE_URL"),
        embed_model=_env_display_value("EMBED_MODEL"),
        embed_timeout=_env_float_display("EMBED_TIMEOUT", 90.0, minimum=1.0),
        embed_max_retries=_env_int_display("EMBED_MAX_RETRIES", 0, minimum=0),
        embed_workers=_env_int_display("EMBED_WORKERS", 4, minimum=1),
        embed_batch_size=_env_int_display("EMBED_BATCH_SIZE", 32, minimum=1),
        chat_timeout=(
            llm_module.CHAT_TIMEOUT_OVERRIDE
            if llm_module.CHAT_TIMEOUT_OVERRIDE is not None
            else llm_module._env_float("CHAT_TIMEOUT", 300.0, minimum=1.0)
        ),
        chat_temperature=llm_module.CHAT_TEMPERATURE,
        enabled_tools=list(agent_module.ENABLED_TOOLS),
    )


def _current_settings_unlocked() -> SettingsResponseModel:
    runtime = _runtime_settings_unlocked()
    direct_summary_base_url = llm_module._configured_value(os.environ.get("SUMMARY_BASE_URL"))
    direct_summary_api_key = llm_module._configured_value(os.environ.get("SUMMARY_API_KEY"))
    direct_summary_reasoning_effort = bool(llm_module._reasoning_effort_value("SUMMARY_REASONING_EFFORT"))
    return SettingsResponseModel(
        **runtime.model_dump(exclude={"chat_api_key", "summary_api_key"}),
        available_tools=_available_tool_names(),
        chat_api_key_configured=llm_module._configured_value(os.environ.get("CHAT_API_KEY")),
        summary_api_key_configured=llm_module._configured_value(
            os.environ.get("SUMMARY_API_KEY") or os.environ.get("CHAT_API_KEY")
        ),
        summary_api_key_inherited=not direct_summary_api_key,
        summary_base_url_inherited=not direct_summary_base_url,
        summary_reasoning_effort_inherited=not direct_summary_reasoning_effort,
        embed_api_key_configured=llm_module._configured_value(os.environ.get("EMBED_API_KEY")),
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


def _field_provided(req: SettingsUpdateModel, field: str) -> bool:
    return field in req.model_fields_set


def _apply_settings_unlocked(req: SettingsUpdateModel) -> bool:
    enabled_tools = _validate_enabled_tools(req.enabled_tools) if req.enabled_tools is not None else None
    cache_needs_clear = False
    if req.system_prompt is not None:
        agent_module.SYSTEM_PROMPT = req.system_prompt
    if req.max_rounds is not None:
        agent_module.MAX_ROUNDS = req.max_rounds
    if req.max_history_messages is not None:
        agent_module.MAX_HISTORY_MESSAGES = req.max_history_messages
    for field, env_name in _ENV_RUNTIME_FIELDS.items():
        if _field_provided(req, field):
            _set_env_override_unlocked(env_name, _env_override_from_value(env_name, getattr(req, field)))
            cache_needs_clear = True
    if _field_provided(req, "chat_model"):
        llm_module.CHAT_MODEL_OVERRIDE = _chat_model_override_from_value(req.chat_model)
        cache_needs_clear = True
    if _field_provided(req, "summary_model"):
        _set_summary_model_override_unlocked(_summary_model_override_from_value(req.summary_model))
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


def _persisted_settings_after_update_unlocked(req: SettingsUpdateModel) -> SettingsUpdateModel:
    payload = _runtime_settings_unlocked().model_dump()
    payload["chat_model"] = llm_module.CHAT_MODEL_OVERRIDE
    payload["summary_model"] = _summary_model_override
    payload["chat_timeout"] = llm_module.CHAT_TIMEOUT_OVERRIDE
    for field, env_name in _ENV_RUNTIME_FIELDS.items():
        payload[field] = _env_override_from_value(env_name, getattr(_runtime_settings_unlocked(), field))
    if req.system_prompt is not None:
        payload["system_prompt"] = req.system_prompt
    if req.max_rounds is not None:
        payload["max_rounds"] = req.max_rounds
    if req.max_history_messages is not None:
        payload["max_history_messages"] = req.max_history_messages
    if _field_provided(req, "chat_model"):
        payload["chat_model"] = _chat_model_override_from_value(req.chat_model)
    for field, env_name in _ENV_RUNTIME_FIELDS.items():
        if _field_provided(req, field):
            payload[field] = _env_override_from_value(env_name, getattr(req, field))
    if _field_provided(req, "summary_model"):
        payload["summary_model"] = _summary_model_override_from_value(req.summary_model)
    if _field_provided(req, "chat_timeout"):
        payload["chat_timeout"] = _chat_timeout_override_from_value(req.chat_timeout)
    if req.chat_temperature is not None:
        payload["chat_temperature"] = req.chat_temperature
    if req.enabled_tools is not None:
        payload["enabled_tools"] = _validate_enabled_tools(req.enabled_tools)
    return SettingsUpdateModel.model_validate(payload)


def _save_settings_unlocked(payload: SettingsUpdateModel) -> None:
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
        req = SettingsUpdateModel.model_validate(raw)
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


def _model_list_config(target: str) -> tuple[str, str, list[str]]:
    if target == "summary":
        base_url = llm_module._env_config_value("SUMMARY_BASE_URL") or llm_module._env_config_value("CHAT_BASE_URL")
        api_key = llm_module._env_config_value("SUMMARY_API_KEY") or llm_module._env_config_value("CHAT_API_KEY")
        missing = []
        if not llm_module._configured_value(base_url):
            missing.append("SUMMARY_BASE_URL/CHAT_BASE_URL")
        if not llm_module._configured_value(api_key):
            missing.append("SUMMARY_API_KEY/CHAT_API_KEY")
        return base_url, api_key, missing

    base_url = llm_module._env_config_value("CHAT_BASE_URL")
    api_key = llm_module._env_config_value("CHAT_API_KEY")
    missing = []
    if not llm_module._configured_value(base_url):
        missing.append("CHAT_BASE_URL")
    if not llm_module._configured_value(api_key):
        missing.append("CHAT_API_KEY")
    return base_url, api_key, missing


@router.get("/models", summary="List available OpenAI-compatible models")
def list_models(target: str = Query(default="chat", pattern="^(chat|summary)$")) -> dict[str, list[str]]:
    with _settings_lock:
        base_url, api_key, missing = _model_list_config(target)
    if missing:
        raise HTTPException(status_code=400, detail=f"模型列表加载失败，缺少配置：{', '.join(missing)}")

    url = f"{base_url.rstrip('/')}/models"
    req = urlrequest.Request(url, headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"})
    try:
        with urlrequest.urlopen(req, timeout=15) as response:
            raw = response.read()
    except urlerror.HTTPError as exc:
        detail = public_exception_message("模型列表加载失败", exc)
        raise HTTPException(status_code=exc.code or 502, detail=detail) from exc
    except Exception as exc:
        detail = public_exception_message("模型列表加载失败", exc)
        raise HTTPException(status_code=502, detail=detail) from exc

    try:
        payload = json.loads(raw.decode("utf-8"))
        data = payload.get("data") if isinstance(payload, dict) else []
        items = sorted(
            {
                str(item.get("id")).strip()
                for item in data
                if isinstance(item, dict) and str(item.get("id", "")).strip()
            }
        )
    except Exception as exc:
        detail = public_exception_message("模型列表解析失败", exc)
        raise HTTPException(status_code=502, detail=detail) from exc
    return {"items": items}


@router.get("", response_model=SettingsResponseModel, summary="Get system settings")
def get_settings() -> SettingsResponseModel:
    with _settings_lock:
        return _current_settings_unlocked()


@router.post("", response_model=SettingsResponseModel, summary="Update system settings")
def update_settings(req: SettingsUpdateModel) -> SettingsResponseModel:
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
        _set_summary_model_override_unlocked(None)
        for env_name in _ENV_RUNTIME_FIELDS.values():
            _set_env_override_unlocked(env_name, None)
        _clear_model_cache()
        return _current_settings_unlocked()
