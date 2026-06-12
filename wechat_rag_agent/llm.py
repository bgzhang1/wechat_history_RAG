from __future__ import annotations

import os
import time

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI, OpenAIEmbeddings


load_dotenv()

EMBED_DIM = int(os.getenv("EMBED_DIM", "1024"))


def chat_configured() -> bool:
    return bool(os.getenv("CHAT_BASE_URL") and os.getenv("CHAT_API_KEY") and os.getenv("CHAT_MODEL"))


def embed_configured() -> bool:
    return bool(os.getenv("EMBED_BASE_URL") and os.getenv("EMBED_API_KEY") and os.getenv("EMBED_MODEL"))


def chat_model(model: str | None = None) -> ChatOpenAI:
    if not chat_configured():
        raise RuntimeError("未配置主模型：请在 .env 中设置 CHAT_BASE_URL / CHAT_API_KEY / CHAT_MODEL")
    return ChatOpenAI(
        model=model or os.environ["CHAT_MODEL"],
        base_url=os.environ["CHAT_BASE_URL"],
        api_key=os.environ["CHAT_API_KEY"],
        temperature=0,
        timeout=90,
        max_retries=1,
    )


def embed_model() -> OpenAIEmbeddings:
    if not embed_configured():
        raise RuntimeError("未配置 Embedding：请在 .env 中设置 EMBED_BASE_URL / EMBED_API_KEY / EMBED_MODEL")
    return OpenAIEmbeddings(
        model=os.environ["EMBED_MODEL"],
        base_url=os.environ["EMBED_BASE_URL"],
        api_key=os.environ["EMBED_API_KEY"],
    )


def embed(texts: list[str]) -> list[list[float]]:
    embeddings = embed_model()
    batch_size = 32
    output: list[list[float]] = []

    for start in range(0, len(texts), batch_size):
        batch = texts[start : start + batch_size]
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                output.extend(embeddings.embed_documents(batch))
                last_error = None
                break
            except Exception as exc:
                last_error = exc
                time.sleep(attempt + 1)
        if last_error:
            raise last_error

    return output
