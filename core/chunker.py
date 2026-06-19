from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any


GAP_MINUTES = 30
MAX_CHARS = 800
MAX_MESSAGE_CHARS_IN_CHUNK = 1200
MAX_MSGS = 60
MIN_CHARS = 50
MERGE_GAP_HOURS = 2
OVERLAP_MSGS = 3


@dataclass(frozen=True)
class Chunk:
    thread: str
    start_time: str
    end_time: str
    participants: str
    msg_ids: str
    text: str
    summary: str | None = None


def _ts(message: Any) -> float:
    return datetime.fromisoformat(message["timestamp"]).timestamp()


def _chars(messages: list[dict[str, Any]]) -> int:
    return sum(len(message["content"]) for message in messages)


def _chunk_content(content: Any) -> str:
    text = str(content or "")
    if len(text) <= MAX_MESSAGE_CHARS_IN_CHUNK:
        return text
    omitted = len(text) - MAX_MESSAGE_CHARS_IN_CHUNK
    return f"{text[:MAX_MESSAGE_CHARS_IN_CHUNK]}...[已截断 {omitted} 字，使用 get_context 查看原文]"


def _format_chunk_time_range(start: str, end: str) -> str:
    start_label = start.replace("T", " ")[:16]
    end_label = end.replace("T", " ")[:16]
    if start_label[:10] == end_label[:10]:
        end_label = end_label[11:16]
    return f"{start_label} ~ {end_label}"


def _format_message_lines(message: dict[str, Any]) -> str:
    sender = str(message["sender"])
    content = _chunk_content(message["content"])
    lines = content.splitlines() or [""]
    return "\n".join(f"{sender}: {line}" for line in lines)


def chunk_thread(thread: str, messages: list[dict[str, Any]], thread_type: str = "") -> list[Chunk]:
    if not messages:
        return []
    messages = sorted(messages, key=lambda message: (message["timestamp"], str(message.get("id", ""))))

    raw: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0

    for message in messages:
        length = len(message["content"])
        gap_min = (_ts(message) - _ts(current[-1])) / 60 if current else 0
        should_cut = (
            current
            and (gap_min > GAP_MINUTES or current_chars + length > MAX_CHARS or len(current) >= MAX_MSGS)
        )
        if should_cut:
            raw.append(current)
            current = []
            current_chars = 0
        current.append(message)
        current_chars += length

    if current:
        raw.append(current)

    merged: list[list[dict[str, Any]]] = []
    i = 0
    while i < len(raw):
        block = raw[i]
        next_block = raw[i + 1] if i + 1 < len(raw) else None
        if next_block and _chars(block) < MIN_CHARS and (_ts(next_block[0]) - _ts(block[-1])) / 3600 < MERGE_GAP_HOURS:
            raw[i + 1] = [*block, *next_block]
            i += 1
            continue
        if (
            not next_block
            and merged
            and _chars(block) < MIN_CHARS
            and (_ts(block[0]) - _ts(merged[-1][-1])) / 3600 < MERGE_GAP_HOURS
        ):
            merged[-1] = [*merged[-1], *block]
            i += 1
            continue
        merged.append(block)
        i += 1

    chunks: list[Chunk] = []
    for idx, block in enumerate(merged):
        overlap = merged[idx - 1][-OVERLAP_MSGS:] if idx > 0 else []
        participants = list(dict.fromkeys(message["sender"] for message in block))
        start = block[0]["timestamp"]
        end = block[-1]["timestamp"]
        header = f"[{_format_chunk_time_range(start, end)}] {thread_type}{thread}（{'、'.join(participants)}）"
        lines = [_format_message_lines(message) for message in [*overlap, *block]]
        chunks.append(
            Chunk(
                thread=thread,
                start_time=start,
                end_time=end,
                participants=json.dumps(participants, ensure_ascii=False),
                msg_ids=json.dumps([message["id"] for message in block], ensure_ascii=False),
                text=f"{header}\n" + "\n".join(lines),
            )
        )

    return chunks
