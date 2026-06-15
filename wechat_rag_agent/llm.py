from __future__ import annotations

import os
import time
from functools import lru_cache
from typing import Any, Callable, TypeVar

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI, OpenAIEmbeddings


load_dotenv()

DEFAULT_API_ATTEMPTS = 3
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


def _remote_api_call(
    call: Callable[[], T],
    attempts: int,
    retry_sleep: float,
) -> T:
    for attempt in range(attempts):
        try:
            return call()
        except Exception as exc:
            if attempt + 1 >= attempts:
                raise
            if retry_sleep:
                time.sleep(retry_sleep * (attempt + 1))
    raise RuntimeError("remote API retry loop exited unexpectedly")


def chat_configured() -> bool:
    return bool(os.getenv("CHAT_BASE_URL") and os.getenv("CHAT_API_KEY") and os.getenv("CHAT_MODEL"))


def embed_configured() -> bool:
    return bool(os.getenv("EMBED_BASE_URL") and os.getenv("EMBED_API_KEY") and os.getenv("EMBED_MODEL"))


@lru_cache(maxsize=8)
def _chat_model_cached(model: str) -> ChatOpenAI:
    return ChatOpenAI(
        model=model,
        base_url=os.environ["CHAT_BASE_URL"],
        api_key=os.environ["CHAT_API_KEY"],
        temperature=0,
        timeout=_env_float("CHAT_TIMEOUT", 300.0, minimum=1.0),
        max_retries=_env_int("CHAT_MAX_RETRIES", 3, minimum=0),
    )


def chat_model(model: str | None = None) -> ChatOpenAI:
    if not chat_configured():
        raise RuntimeError("未配置主模型：请在 .env 中设置 CHAT_BASE_URL / CHAT_API_KEY / CHAT_MODEL")
    return _chat_model_cached(model or os.environ["CHAT_MODEL"])


def invoke_chat(input: Any, model: str | None = None) -> Any:
    client = chat_model(model)
    attempts = _retry_attempts("CHAT_LOCAL_RETRIES")
    retry_sleep = _env_float("CHAT_RETRY_SLEEP", 1.0, minimum=0.0)
    return _remote_api_call(lambda: client.invoke(input), attempts, retry_sleep)


@lru_cache(maxsize=2)
def _embed_model_cached(model: str) -> OpenAIEmbeddings:
    # check_embedding_ctx_length=False：直接发送原文，避免用 tiktoken（OpenAI 分词器）
    # 给 bge-m3 等非 OpenAI 模型做本地分词切片，更快也更准确。
    return OpenAIEmbeddings(
        model=model,
        base_url=os.environ["EMBED_BASE_URL"],
        api_key=os.environ["EMBED_API_KEY"],
        check_embedding_ctx_length=False,
        timeout=_env_float("EMBED_TIMEOUT", 90.0, minimum=1.0),
        max_retries=_env_int("EMBED_MAX_RETRIES", 0, minimum=0),
    )


def embed_model() -> OpenAIEmbeddings:
    if not embed_configured():
        raise RuntimeError("未配置 Embedding：请在 .env 中设置 EMBED_BASE_URL / EMBED_API_KEY / EMBED_MODEL")
    return _embed_model_cached(os.environ["EMBED_MODEL"])


def embed(texts: list[str], batch_size: int = 32) -> list[list[float]]:
    embeddings = embed_model()
    output: list[list[float]] = []
    local_retries = _retry_attempts("EMBED_LOCAL_RETRIES")
    retry_sleep = _env_float("EMBED_RETRY_SLEEP", 1.0, minimum=0.0)

    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        output.extend(
            _remote_api_call(
                lambda: embeddings.embed_documents(batch),
                local_retries,
                retry_sleep,
            )
        )

    return output
