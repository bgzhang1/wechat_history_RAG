from __future__ import annotations

import os
import time
from functools import lru_cache
from typing import Any, Callable, TypeVar
from urllib.parse import urlparse

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI, OpenAIEmbeddings


load_dotenv()

DEFAULT_API_ATTEMPTS = 3
DEFAULT_REQUEST_FAILURE_RETRIES = 3
DEFAULT_REQUEST_FAILURE_RETRY_INTERVAL = 5.0
T = TypeVar("T")


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return max(minimum, value)


def _env_optional_int(name: str, minimum: int = 0) -> int | None:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except ValueError:
        return None
    return max(minimum, value)


def _env_float(name: str, default: float, minimum: float = 0.0) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(minimum, value)


EMBED_DIM = _env_int("EMBED_DIM", 1024, minimum=1)


def _retry_attempts(name: str, default: int = DEFAULT_API_ATTEMPTS) -> int:
    return _env_int(name, default, minimum=1)


def request_failure_retries(default: int = DEFAULT_REQUEST_FAILURE_RETRIES) -> int:
    configured = _env_optional_int("REQUEST_FAILURE_RETRIES", minimum=0)
    if configured is not None:
        return configured

    legacy_values = [
        value
        for value in (
            _env_optional_int("CHAT_MAX_RETRIES", minimum=0),
            _env_optional_int("EMBED_MAX_RETRIES", minimum=0),
            _legacy_local_retry_count("CHAT_LOCAL_RETRIES"),
            _legacy_local_retry_count("SUMMARY_LOCAL_RETRIES"),
            _legacy_local_retry_count("EMBED_LOCAL_RETRIES"),
        )
        if value is not None
    ]
    if legacy_values:
        return max(legacy_values)
    return default


def _env_optional_float(name: str, minimum: float = 0.0) -> float | None:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return max(minimum, value)


def _legacy_local_retry_count(name: str) -> int | None:
    attempts = _env_optional_int(name, minimum=1)
    return None if attempts is None else max(0, attempts - 1)


def request_failure_retry_interval(default: float = DEFAULT_REQUEST_FAILURE_RETRY_INTERVAL) -> float:
    configured = _env_optional_float("REQUEST_FAILURE_RETRY_INTERVAL", minimum=0.0)
    if configured is not None:
        return configured

    legacy_values = [
        value
        for value in (
            _env_optional_float("CHAT_RETRY_SLEEP", minimum=0.0),
            _env_optional_float("SUMMARY_RETRY_SLEEP", minimum=0.0),
            _env_optional_float("EMBED_RETRY_SLEEP", minimum=0.0),
        )
        if value is not None
    ]
    if legacy_values:
        return max(legacy_values)
    return default


def request_failure_retry_delay(retry_number: int, base_interval: float | None = None) -> float:
    try:
        retry = int(retry_number)
    except (TypeError, ValueError, OverflowError):
        retry = 1
    retry = max(1, retry)
    interval = request_failure_retry_interval() if base_interval is None else max(0.0, float(base_interval))
    return interval * (((retry - 1) // 3) + 1)


def _positive_int_value(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return parsed if parsed >= 1 else default


def _remote_api_call(
    call: Callable[[], T],
    retries: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> T:
    max_retries = request_failure_retries() if retries is None else max(0, int(retries))
    for attempt in range(max_retries + 1):
        try:
            return call()
        except Exception:
            if attempt >= max_retries:
                raise
            delay = request_failure_retry_delay(attempt + 1)
            if delay:
                sleep(delay)
    raise RuntimeError("remote API retry loop exited unexpectedly")


def _remote_api_stream(
    call: Callable[[], Any],
    retries: int | None = None,
    sleep: Callable[[float], None] = time.sleep,
) -> Any:
    max_retries = request_failure_retries() if retries is None else max(0, int(retries))
    for attempt in range(max_retries + 1):
        emitted = False
        try:
            for item in call():
                emitted = True
                yield item
            return
        except Exception:
            if emitted or attempt >= max_retries:
                raise
            delay = request_failure_retry_delay(attempt + 1)
            if delay:
                sleep(delay)
    raise RuntimeError("remote API retry loop exited unexpectedly")


CHAT_MODEL_OVERRIDE: str | None = None
CHAT_TIMEOUT_OVERRIDE: float | None = None
CHAT_TEMPERATURE: float = 0.0
REASONING_EFFORT_VALUES = {"low", "medium", "high"}


PLACEHOLDER_VALUES = {
    "https://example.com/v1",
    "http://example.com/v1",
    "https://example.com/v1/",
    "http://example.com/v1/",
    "sk-...",
    "...",
    "changeme",
    "change-me",
    "your-api-key",
    "your-key",
    "your-chat-model",
    "your-embedding-model",
}


def _configured_value(value: str | None) -> bool:
    if not value or not value.strip():
        return False
    normalized = value.strip().lower()
    if normalized in PLACEHOLDER_VALUES:
        return False
    if _uses_example_domain(normalized):
        return False
    return not normalized.startswith("your-")


def _uses_example_domain(value: str) -> bool:
    if not value.startswith(("http://", "https://", "//")):
        return False
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").rstrip(".")
    return hostname == "example.com" or hostname.endswith(".example.com")


def _env_config_value(name: str) -> str:
    return (os.getenv(name) or "").strip()


def _reasoning_effort_value(name: str) -> str:
    value = _env_config_value(name).lower()
    return value if value in REASONING_EFFORT_VALUES else ""


def _chat_reasoning_effort() -> str:
    return _reasoning_effort_value("CHAT_REASONING_EFFORT")


def _summary_reasoning_effort() -> str:
    return _reasoning_effort_value("SUMMARY_REASONING_EFFORT") or _chat_reasoning_effort()


def chat_config_status(model: str | None = None) -> dict[str, Any]:
    explicit_model = model.strip() if isinstance(model, str) else None
    resolved_model = explicit_model or CHAT_MODEL_OVERRIDE or _env_config_value("CHAT_MODEL")
    required = {
        "CHAT_BASE_URL": _env_config_value("CHAT_BASE_URL"),
        "CHAT_API_KEY": _env_config_value("CHAT_API_KEY"),
        "CHAT_MODEL": resolved_model,
    }
    missing = [name for name, value in required.items() if not _configured_value(value)]
    return {
        "configured": not missing,
        "missing": missing,
        "model": resolved_model or "",
        "reasoning_effort": _chat_reasoning_effort(),
        "using_model_override": bool(CHAT_MODEL_OVERRIDE and not explicit_model),
        "using_explicit_model": bool(explicit_model),
    }


def _summary_base_url() -> str:
    return _env_config_value("SUMMARY_BASE_URL") or _env_config_value("CHAT_BASE_URL")


def _summary_api_key() -> str:
    return _env_config_value("SUMMARY_API_KEY") or _env_config_value("CHAT_API_KEY")


def summary_config_status(model: str | None = None) -> dict[str, Any]:
    explicit_model = model.strip() if isinstance(model, str) else None
    resolved_model = explicit_model or _env_config_value("SUMMARY_MODEL")
    summary_base_url = _env_config_value("SUMMARY_BASE_URL")
    summary_api_key = _env_config_value("SUMMARY_API_KEY")
    base_url = summary_base_url or _env_config_value("CHAT_BASE_URL")
    api_key = summary_api_key or _env_config_value("CHAT_API_KEY")
    required = {
        "SUMMARY_BASE_URL/CHAT_BASE_URL": base_url,
        "SUMMARY_API_KEY/CHAT_API_KEY": api_key,
        "SUMMARY_MODEL": resolved_model,
    }
    missing = [name for name, value in required.items() if not _configured_value(value)]
    return {
        "configured": not missing,
        "missing": missing,
        "model": resolved_model or "",
        "base_url": base_url,
        "reasoning_effort": _summary_reasoning_effort(),
        "using_explicit_model": bool(explicit_model),
        "using_chat_base_url": not bool(summary_base_url),
        "using_chat_api_key": not bool(summary_api_key),
        "using_chat_reasoning_effort": not bool(_reasoning_effort_value("SUMMARY_REASONING_EFFORT")),
    }


def chat_configured() -> bool:
    return bool(chat_config_status()["configured"])


def embed_config_status() -> dict[str, Any]:
    required = {
        "EMBED_BASE_URL": _env_config_value("EMBED_BASE_URL"),
        "EMBED_API_KEY": _env_config_value("EMBED_API_KEY"),
        "EMBED_MODEL": _env_config_value("EMBED_MODEL"),
    }
    missing = [name for name, value in required.items() if not _configured_value(value)]
    return {
        "configured": not missing,
        "missing": missing,
        "model": _env_config_value("EMBED_MODEL"),
    }


def embed_configured() -> bool:
    return bool(embed_config_status()["configured"])


@lru_cache(maxsize=8)
def _chat_model_cached(
    model: str,
    base_url: str,
    api_key: str,
    timeout: float,
    temperature: float,
    max_retries: int,
    reasoning_effort: str,
) -> ChatOpenAI:
    model_kwargs = {"reasoning_effort": reasoning_effort} if reasoning_effort else {}
    return ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=temperature,
        timeout=timeout,
        max_retries=max_retries,
        model_kwargs=model_kwargs,
    )


def chat_model(model: str | None = None) -> ChatOpenAI:
    status = chat_config_status(model)
    if not status["configured"]:
        missing = ", ".join(status["missing"])
        raise RuntimeError(f"对话模型尚未配置，缺少：{missing}")
    actual_model = status["model"]
    actual_timeout = (
        CHAT_TIMEOUT_OVERRIDE
        if CHAT_TIMEOUT_OVERRIDE is not None
        else _env_float("CHAT_TIMEOUT", 300.0, minimum=1.0)
    )
    return _chat_model_cached(
        actual_model,
        _env_config_value("CHAT_BASE_URL"),
        _env_config_value("CHAT_API_KEY"),
        actual_timeout,
        CHAT_TEMPERATURE,
        0,
        _chat_reasoning_effort(),
    )


def invoke_chat(input: Any, model: str | None = None, tools: list[Any] | None = None) -> Any:
    client = chat_model(model)
    if tools is not None:
        client = client.bind_tools(tools)
    return _remote_api_call(lambda: client.invoke(input))


def stream_chat(input: Any, model: str | None = None) -> Any:
    client = chat_model(model)
    return _remote_api_stream(lambda: client.stream(input))


def summary_model(model: str | None = None) -> ChatOpenAI:
    status = summary_config_status(model)
    if not status["configured"]:
        missing = ", ".join(status["missing"])
        raise RuntimeError(f"摘要模型尚未配置，缺少：{missing}")
    actual_timeout = (
        CHAT_TIMEOUT_OVERRIDE
        if CHAT_TIMEOUT_OVERRIDE is not None
        else _env_float("CHAT_TIMEOUT", 300.0, minimum=1.0)
    )
    return _chat_model_cached(
        status["model"],
        _summary_base_url(),
        _summary_api_key(),
        actual_timeout,
        CHAT_TEMPERATURE,
        0,
        _summary_reasoning_effort(),
    )


def invoke_summary(input: Any, model: str | None = None) -> Any:
    client = summary_model(model)
    return _remote_api_call(lambda: client.invoke(input))


@lru_cache(maxsize=2)
def _embed_model_cached(
    model: str,
    base_url: str,
    api_key: str,
    timeout: float,
    max_retries: int,
) -> OpenAIEmbeddings:
    return OpenAIEmbeddings(
        model=model,
        base_url=base_url,
        api_key=api_key,
        check_embedding_ctx_length=False,
        timeout=timeout,
        max_retries=max_retries,
    )


def embed_model() -> OpenAIEmbeddings:
    if not embed_configured():
        missing = ", ".join(embed_config_status()["missing"])
        raise RuntimeError(f"Embedding 模型尚未配置，缺少：{missing}")
    return _embed_model_cached(
        _env_config_value("EMBED_MODEL"),
        _env_config_value("EMBED_BASE_URL"),
        _env_config_value("EMBED_API_KEY"),
        _env_float("EMBED_TIMEOUT", 90.0, minimum=1.0),
        0,
    )


def embed(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    if not texts:
        return []
    safe_batch_size = _positive_int_value(batch_size, 32)
    embeddings = embed_model()
    output: list[list[float]] = []

    for start in range(0, len(texts), safe_batch_size):
        batch = texts[start : start + safe_batch_size]
        output.extend(
            _remote_api_call(
                lambda batch=batch: embeddings.embed_documents(batch),
            )
        )

    return output
