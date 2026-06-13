from __future__ import annotations

import os
import time
from functools import lru_cache

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI, OpenAIEmbeddings


load_dotenv()

EMBED_DIM = int(os.getenv("EMBED_DIM", "1024"))


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
        timeout=_env_float("CHAT_TIMEOUT", 90.0, minimum=1.0),
        max_retries=_env_int("CHAT_MAX_RETRIES", 0, minimum=0),
    )


def chat_model(model: str | None = None) -> ChatOpenAI:
    if not chat_configured():
        raise RuntimeError("未配置主模型：请在 .env 中设置 CHAT_BASE_URL / CHAT_API_KEY / CHAT_MODEL")
    return _chat_model_cached(model or os.environ["CHAT_MODEL"])


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
    local_retries = _env_int("EMBED_LOCAL_RETRIES", 1, minimum=1)
    retry_sleep = _env_float("EMBED_RETRY_SLEEP", 1.0, minimum=0.0)

    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        last_error: Exception | None = None
        for attempt in range(local_retries):
            try:
                output.extend(embeddings.embed_documents(batch))
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                if attempt + 1 < local_retries and retry_sleep:
                    time.sleep(retry_sleep * (attempt + 1))
        if last_error:
            raise last_error

    return output
