from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


INCLUDE_TYPES = {"文本消息", "引用消息"}


@dataclass(frozen=True)
class NormMessage:
    id: str
    sender: str
    is_self: int
    timestamp: str
    content: str
    msg_type: str
    thread: str
    reply_to: str | None


@dataclass
class ParseResult:
    thread: str
    total: int
    included: int
    skipped_by_type: Counter[str]
    messages: list[NormMessage]


def to_local_iso(unix_sec: int | float) -> str:
    return datetime.fromtimestamp(unix_sec).strftime("%Y-%m-%dT%H:%M:%S")


def is_weflow_export(data: Any) -> bool:
    return isinstance(data, dict) and "weflow" in data and isinstance(data.get("messages"), list)


def parse_weflow(data: dict[str, Any], file_path: str | Path) -> ParseResult:
    session = data["session"]
    peer_name = (session.get("remark") or "").strip() or session.get("displayName") or session.get("nickname")
    thread = peer_name
    file_base = Path(file_path).name

    messages: list[NormMessage] = []
    skipped: Counter[str] = Counter()

    for msg in data["messages"]:
        msg_type = msg.get("type", "")
        content = msg.get("content")
        if msg_type not in INCLUDE_TYPES:
            skipped[msg_type] += 1
            continue
        if content is None or content == "":
            skipped[f"{msg_type}(空内容)"] += 1
            continue

        if msg.get("isSend") == 1:
            sender = msg.get("senderDisplayName") or "我"
            is_self = 1
        else:
            sender = (
                peer_name
                if msg.get("senderUsername") == session.get("wxid")
                else msg.get("senderDisplayName")
            ) or peer_name
            is_self = 0

        messages.append(
            NormMessage(
                id=msg.get("platformMessageId") or f"{file_base}:{msg.get('localId')}",
                sender=sender,
                is_self=is_self,
                timestamp=to_local_iso(msg["createTime"]),
                content=content,
                msg_type=msg_type,
                thread=thread,
                reply_to=msg.get("replyToMessageId") or None,
            )
        )

    return ParseResult(
        thread=thread,
        total=len(data["messages"]),
        included=len(messages),
        skipped_by_type=skipped,
        messages=messages,
    )
