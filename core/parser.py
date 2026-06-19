from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any


PARSER_VERSION = 12

INCLUDE_TYPES = {
    "文本消息",
    "引用消息",
    "系统消息",
    "聊天记录",
    "链接消息",
    "转账消息",
    "位置消息",
    "小程序消息",
    "文件消息",
    "图片消息",
    "语音消息",
    "视频消息",
    "通话消息",
    "名片消息",
    "动画表情",
    "其他消息",
}
TYPE_KEYS = (
    "type",
    "msgType",
    "messageType",
    "msg_type",
    "message_type",
    "typeName",
    "type_name",
    "msgTypeName",
    "msg_type_name",
    "messageTypeName",
    "message_type_name",
)
MESSAGE_LIST_KEYS = (
    "messages",
    "messageList",
    "message_list",
    "msgList",
    "msg_list",
    "chatRecords",
    "chat_records",
    "records",
)
NUMERIC_MESSAGE_TYPE_ALIASES = {
    1: "文本消息",
    3: "图片消息",
    34: "语音消息",
    42: "名片消息",
    43: "视频消息",
    47: "动画表情",
    48: "位置消息",
    49: "链接消息",
    50: "通话消息",
    10000: "系统消息",
    10002: "系统消息",
}
STRING_MESSAGE_TYPE_ALIASES = {
    "text": "文本消息",
    "image": "图片消息",
    "voice": "语音消息",
    "audio": "语音消息",
    "video": "视频消息",
    "emoji": "动画表情",
    "location": "位置消息",
    "link": "链接消息",
    "file": "文件消息",
    "system": "系统消息",
    "文本": "文本消息",
    "图片": "图片消息",
    "语音": "语音消息",
    "视频": "视频消息",
    "位置": "位置消息",
    "链接": "链接消息",
    "文件": "文件消息",
    "系统": "系统消息",
}
TIME_KEYS = (
    "createTime",
    "create_time",
    "timestamp",
    "createdAt",
    "created_at",
    "formattedTime",
    "formatted_time",
    "msgCreateTime",
    "msg_create_time",
    "sentAt",
    "sent_at",
    "time",
)
CONTENT_KEYS = (
    "content",
    "text",
    "message",
    "body",
    "msgContent",
    "msg_content",
    "plainText",
    "plain_text",
    "messageText",
    "message_text",
)
MEDIA_TEXT_KEYS = (
    "ocrText",
    "ocr_text",
    "ocr",
    "imageText",
    "image_text",
    "caption",
    "captionText",
    "caption_text",
    "altText",
    "alt_text",
    "transcription",
    "transcript",
    "asrText",
    "asr_text",
    "asr",
    "voiceText",
    "voice_text",
    "speechText",
    "speech_text",
    "recognizedText",
    "recognized_text",
    "recognitionText",
    "recognition_text",
    "videoCaption",
    "video_caption",
    "videoText",
    "video_text",
    "mediaText",
    "media_text",
    "plainText",
    "plain_text",
    "messageText",
    "message_text",
    "text",
)
MEDIA_CONTAINER_KEYS = (*CONTENT_KEYS, "media", "image", "voice", "audio", "video")
MEDIA_TEXT_TYPES = {"图片消息", "语音消息", "视频消息"}
EXTRA_TEXT_KEYS = (
    *MEDIA_TEXT_KEYS,
    "emojiCaption",
    "emoji_caption",
    "linkTitle",
    "link_title",
    "linkUrl",
    "link_url",
    "url",
    "href",
    "appMsgSourceName",
    "app_msg_source_name",
    "sourceName",
    "source_name",
    "appName",
    "app_name",
    "fileName",
    "file_name",
    "fileSize",
    "file_size",
    "pagePath",
    "page_path",
    "locationName",
    "location_name",
    "poiName",
    "poi_name",
    "amount",
    "memo",
    "title",
    "summary",
    "description",
    "desc",
    "address",
    "latitude",
    "longitude",
)
QUOTED_CONTENT_KEYS = ("quotedContent", "quoted_content", "quoteContent", "quote_content")
QUOTED_SENDER_KEYS = ("quotedSender", "quoted_sender", "quoteSender", "quote_sender")
QUOTED_MESSAGE_KEYS = (
    "quote",
    "quotedMessage",
    "quoted_message",
    "reference",
    "refer",
    "referMsg",
    "refer_msg",
    "reply",
    "replyMessage",
    "reply_message",
)
PLATFORM_ID_KEYS = (
    "platformMessageId",
    "platform_message_id",
    "serverId",
    "server_id",
    "svrId",
    "svr_id",
    "newMsgId",
    "new_msg_id",
    "msgSvrId",
    "msg_svr_id",
)
MESSAGE_ID_KEYS = ("msgId", "msg_id", "messageId", "message_id", "clientMsgId", "client_msg_id", "id")
LOCAL_ID_KEYS = ("localId", "local_id", "localMsgId", "local_msg_id")
REPLY_TO_KEYS = (
    "replyToMessageId",
    "reply_to_message_id",
    "replyMsgId",
    "reply_msg_id",
    "replyMessageId",
    "reply_message_id",
    "quoteMsgId",
    "quote_msg_id",
    "referMsgId",
    "refer_msg_id",
)
SESSION_NAME_KEYS = ("remark", "remarkName", "remark_name", "displayName", "display_name", "nickname", "name", "title")
SESSION_WXID_KEYS = ("wxid", "userName", "user_name", "username")
SENDER_USERNAME_KEYS = ("senderUsername", "sender_username", "fromUserName", "from_user_name", "senderWxid", "sender_wxid")
SENDER_DISPLAY_KEYS = (
    "senderDisplayName",
    "sender_display_name",
    "senderName",
    "sender_name",
    "fromDisplayName",
    "from_display_name",
)
SENDER_REMARK_KEYS = ("senderRemark", "sender_remark", "fromRemark", "from_remark")
SENDER_NICKNAME_KEYS = ("senderNickname", "sender_nickname", "fromNickname", "from_nickname")
SELF_FLAG_KEYS = ("isSend", "is_send", "isSelf", "is_self", "fromMe", "from_me")
QUOTED_NESTED_CONTENT_KEYS = (
    *QUOTED_CONTENT_KEYS,
    *CONTENT_KEYS,
    "title",
    "summary",
    "description",
    "desc",
)
QUOTED_NESTED_SENDER_KEYS = (
    *QUOTED_SENDER_KEYS,
    "sender",
    "senderName",
    "sender_name",
    "fromName",
    "from_name",
    "fromDisplayName",
    "from_display_name",
    "displayName",
    "display_name",
    "nickname",
    "name",
)
PLACEHOLDER_CONTENT_BY_TYPE = {
    "图片消息": {"[图片]", "[图片消息]", "图片", "image", "[image]"},
    "语音消息": {"[语音]", "[语音消息]", "语音", "voice", "[voice]"},
    "视频消息": {"[视频]", "[视频消息]", "视频", "video", "[video]"},
    "动画表情": {"[表情包]", "[引用 [消息]]"},
    "其他消息": {"当前版本不支持展示该内容，请升级至最新版本。"},
}


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
    return (
        isinstance(data, dict)
        and "weflow" in data
        and isinstance(data.get("session"), dict)
        and _message_list(data) is not None
    )


def _message_list(data: dict[str, Any]) -> list[Any] | None:
    for key in MESSAGE_LIST_KEYS:
        value = data.get(key)
        if isinstance(value, list):
            return value
    return None


def _safe_time(value: Any) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        try:
            parsed = datetime.fromisoformat(stripped.replace("Z", "+00:00"))
            if parsed.tzinfo is not None:
                parsed = parsed.astimezone().replace(tzinfo=None)
            return parsed.replace(tzinfo=None).strftime("%Y-%m-%dT%H:%M:%S")
        except ValueError:
            value = stripped

    try:
        timestamp = float(value)
    except (TypeError, ValueError, OSError, OverflowError):
        return None

    # Some exporters use milliseconds or microseconds. Normalize to seconds.
    if timestamp > 10_000_000_000_000:
        timestamp /= 1_000_000
    elif timestamp > 10_000_000_000:
        timestamp /= 1_000
    if timestamp <= 0:
        return None

    try:
        return to_local_iso(timestamp)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _first_present(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _first_meaningful_present(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key not in mapping:
            continue
        value = mapping[key]
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None


def _first_valid_time(mapping: dict[str, Any]) -> str | None:
    for key in TIME_KEYS:
        if key not in mapping:
            continue
        timestamp = _safe_time(mapping[key])
        if timestamp is not None:
            return timestamp
    return None


def _message_type(msg: dict[str, Any]) -> tuple[str | None, bool]:
    saw_invalid = False
    for key in TYPE_KEYS:
        if key not in msg:
            continue
        value = msg[key]
        if value is None:
            continue
        if isinstance(value, bool):
            saw_invalid = True
            continue
        if isinstance(value, (int, float)):
            if float(value).is_integer():
                code = int(value)
                return NUMERIC_MESSAGE_TYPE_ALIASES.get(code, str(code)), False
            saw_invalid = True
            continue
        if isinstance(value, str):
            msg_type = value.strip()
            if not msg_type:
                continue
            normalized = STRING_MESSAGE_TYPE_ALIASES.get(msg_type.lower(), STRING_MESSAGE_TYPE_ALIASES.get(msg_type))
            if normalized:
                return normalized, False
            try:
                numeric = float(msg_type)
            except ValueError:
                return msg_type, False
            if numeric.is_integer():
                code = int(numeric)
                return NUMERIC_MESSAGE_TYPE_ALIASES.get(code, msg_type), False
            return msg_type, False
        saw_invalid = True
    return None, saw_invalid


def _content_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "\n".join(part for part in (_content_text(item) for item in value) if part).strip()
    if isinstance(value, dict):
        preferred_keys = (
            "content",
            "text",
            "message",
            "body",
            "msgContent",
            "msg_content",
            "plainText",
            "plain_text",
            "messageText",
            "message_text",
            *MEDIA_TEXT_KEYS,
            "title",
            "summary",
            "description",
            "desc",
            "url",
            "href",
            "linkUrl",
            "link_url",
            "appName",
            "app_name",
            "sourceName",
            "source_name",
            "fileName",
            "file_name",
            "fileSize",
            "file_size",
            "pagePath",
            "page_path",
            "locationName",
            "location_name",
            "poiName",
            "poi_name",
            "address",
            "latitude",
            "longitude",
            "amount",
            "memo",
            "displayName",
            "nickname",
            "name",
        )
        parts = [_content_text(value.get(key)) for key in preferred_keys if key in value]
        parts = [part for part in parts if part]
        if parts:
            return "\n".join(dict.fromkeys(parts)).strip()
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip()


def _first_content_text(msg: dict[str, Any]) -> str:
    for key in CONTENT_KEYS:
        if key in msg:
            content = _content_text(msg.get(key))
            if content:
                return content
    return ""


def _first_content_from_keys(mapping: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        if key in mapping:
            content = _content_text(mapping.get(key))
            if content:
                return content
    return ""


def _is_placeholder_content(msg_type: str, content: str) -> bool:
    return content.strip() in PLACEHOLDER_CONTENT_BY_TYPE.get(msg_type, set())


def _unique_non_placeholder_parts(msg_type: str, parts: list[str]) -> list[str]:
    return list(dict.fromkeys(part for part in parts if part and not _is_placeholder_content(msg_type, part)))


def _media_text_parts(value: Any, depth: int = 0) -> list[str]:
    if depth > 2:
        return []
    if isinstance(value, dict):
        parts = [_content_text(value.get(key)) for key in MEDIA_TEXT_KEYS if key in value]
        for key in MEDIA_CONTAINER_KEYS:
            nested = value.get(key)
            if isinstance(nested, (dict, list)):
                parts.extend(_media_text_parts(nested, depth + 1))
        return [part for part in parts if part]
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            parts.extend(_media_text_parts(item, depth + 1))
        return parts
    return []


def _mapping_values_from_keys(msg: dict[str, Any], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    mappings: list[dict[str, Any]] = []
    for key in keys:
        value = msg.get(key)
        if isinstance(value, dict):
            mappings.append(value)
    return mappings


def _quoted_parts(msg: dict[str, Any]) -> tuple[str, str]:
    direct_content = _first_content_from_keys(msg, QUOTED_CONTENT_KEYS)
    direct_sender = _first_content_from_keys(msg, QUOTED_SENDER_KEYS)
    if direct_content:
        return direct_content, direct_sender

    for nested in _mapping_values_from_keys(msg, QUOTED_MESSAGE_KEYS):
        nested_content = _first_content_from_keys(nested, QUOTED_NESTED_CONTENT_KEYS)
        if nested_content:
            return nested_content, _first_content_from_keys(nested, QUOTED_NESTED_SENDER_KEYS)
    return "", ""


def _reply_to_id(msg: dict[str, Any]) -> str | None:
    direct = _first_clean_from_keys(msg, REPLY_TO_KEYS)
    if direct:
        return direct

    nested_id_keys = (*REPLY_TO_KEYS, *PLATFORM_ID_KEYS, *MESSAGE_ID_KEYS, *LOCAL_ID_KEYS)
    for nested in _mapping_values_from_keys(msg, QUOTED_MESSAGE_KEYS):
        nested_id = _first_clean_from_keys(nested, nested_id_keys)
        if nested_id:
            return nested_id
    return None


def _message_content_text(msg: dict[str, Any], msg_type: str) -> str:
    if msg_type in MEDIA_TEXT_TYPES:
        content = "\n".join(_unique_non_placeholder_parts(msg_type, _media_text_parts(msg))).strip()
    else:
        primary_content = _first_content_text(msg)
        if _is_placeholder_content(msg_type, primary_content):
            primary_content = ""
        parts = [primary_content]
        for key in EXTRA_TEXT_KEYS:
            if key in msg:
                extra = _content_text(msg.get(key))
                if extra:
                    parts.append(extra)
        content = "\n".join(_unique_non_placeholder_parts(msg_type, parts)).strip()

    quoted_content, quoted_sender = _quoted_parts(msg)
    if msg_type != "引用消息" and not (quoted_content and msg_type in {"聊天记录", "动画表情"}):
        return content
    if not quoted_content or quoted_content in content:
        return content

    quote = f"[引用 {quoted_sender}：{quoted_content}]" if quoted_sender else f"[引用：{quoted_content}]"
    return "\n".join(part for part in (content, quote) if part).strip()


def _clean_identifier(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _first_clean(*values: Any) -> str:
    for value in values:
        cleaned = _clean_identifier(value)
        if cleaned:
            return cleaned
    return ""


def _first_clean_from_keys(mapping: dict[str, Any], keys: tuple[str, ...]) -> str:
    return _first_clean(*(mapping.get(key) for key in keys if key in mapping))


def _is_self_message(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value == 1
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return False


def _unique_message_id(base_id: str, used_ids: set[str]) -> str:
    candidate = base_id
    duplicate_index = 2
    while candidate in used_ids:
        candidate = f"{base_id}:dup-{duplicate_index}"
        duplicate_index += 1
    used_ids.add(candidate)
    return candidate


def _normalize_file_scope(value: Any) -> str:
    raw = str(value or "").replace("\\", "/").replace(":", "_")
    parts = [part.strip() for part in raw.split("/") if part.strip() and part.strip() not in {".", ".."}]
    return "/".join(parts)[:240]


def _file_scope_from_meta(path: Path) -> str:
    meta_path = path.with_suffix(path.suffix + ".meta")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    if not isinstance(meta, dict):
        return ""
    return _normalize_file_scope(meta.get("scope"))


def _file_scope(file_path: str | Path) -> str:
    path = Path(file_path)
    meta_scope = _file_scope_from_meta(path)
    if meta_scope:
        return meta_scope

    try:
        resolved = path.resolve()
        cwd = Path.cwd().resolve()
        try:
            scoped = resolved.relative_to(cwd)
        except ValueError:
            parent_hash = hashlib.sha256(resolved.parent.as_posix().encode("utf-8")).hexdigest()[:10]
            scoped = Path(parent_hash) / resolved.name
    except OSError:
        scoped = path

    scope = scoped.as_posix().strip("/")
    return scope or path.name or "chat.json"


def file_scope_for_path(file_path: str | Path) -> str:
    return _file_scope(file_path)


def stable_upload_scope(data: dict[str, Any], filename: str = "upload.json") -> str:
    session = data.get("session") if isinstance(data.get("session"), dict) else {}
    messages = _message_list(data) or []
    session_wxid = _first_clean_from_keys(session, SESSION_WXID_KEYS)
    session_name = _first_clean_from_keys(session, SESSION_NAME_KEYS)
    filename_stem = Path(filename or "upload.json").stem

    if session_wxid:
        identity = f"wxid:{session_wxid}"
    elif session_name:
        identity = f"name:{session_name}"
    else:
        first_message_signature = _first_message_scope_signature(messages)
        label = _first_clean(session_name, filename_stem, "upload")
        identity = f"name:{label}|first:{first_message_signature}"

    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:24]
    return f"uploads/{digest}"


def _first_message_scope_signature(messages: list[Any]) -> str:
    for message in messages:
        if not isinstance(message, dict):
            continue
        message_id = (
            _first_clean_from_keys(message, PLATFORM_ID_KEYS)
            or _first_clean_from_keys(message, MESSAGE_ID_KEYS)
            or _first_clean_from_keys(message, LOCAL_ID_KEYS)
        )
        timestamp = _first_clean_from_keys(message, TIME_KEYS)
        sender = _first_clean_from_keys(message, SENDER_USERNAME_KEYS)
        if message_id or timestamp or sender:
            return f"{message_id}|{timestamp}|{sender}"
    return f"count:{len(messages)}"


def _scoped_message_id(file_scope: str, raw_id: str) -> str:
    return f"{file_scope}:{raw_id}"


def parse_weflow(data: dict[str, Any], file_path: str | Path) -> ParseResult:
    session = data["session"]
    raw_messages = _message_list(data)
    if raw_messages is None:
        raise ValueError("WeFlow export missing messages list")
    file_scope = _file_scope(file_path)
    file_stem = Path(file_path).stem
    peer_name = _first_clean(_first_clean_from_keys(session, SESSION_NAME_KEYS), file_stem)
    thread = peer_name

    messages: list[NormMessage] = []
    skipped: Counter[str] = Counter()
    used_message_ids: set[str] = set()
    raw_id_map: dict[str, str] = {}

    for index, msg in enumerate(raw_messages):
        if not isinstance(msg, dict):
            skipped["非法消息结构"] += 1
            continue

        msg_type, has_invalid_type = _message_type(msg)
        if has_invalid_type and msg_type is None:
            skipped["非法消息类型"] += 1
            continue
        if msg_type is None:
            skipped["未知消息类型"] += 1
            continue
        if msg_type not in INCLUDE_TYPES:
            skipped[msg_type] += 1
            continue
        content = _message_content_text(msg, msg_type)
        if not content:
            skipped[f"{msg_type}(空内容)"] += 1
            continue
        timestamp = _first_valid_time(msg)
        if timestamp is None:
            skipped[f"{msg_type}(无效时间)"] += 1
            continue

        sender_username = _first_clean_from_keys(msg, SENDER_USERNAME_KEYS)
        if _is_self_message(_first_meaningful_present(msg, SELF_FLAG_KEYS)):
            sender = _first_clean(
                _first_clean_from_keys(msg, SENDER_DISPLAY_KEYS),
                _first_clean_from_keys(msg, SENDER_REMARK_KEYS),
                _first_clean_from_keys(msg, SENDER_NICKNAME_KEYS),
                "我",
            )
            is_self = 1
        else:
            session_wxid = _first_clean_from_keys(session, SESSION_WXID_KEYS)
            sender = (
                peer_name
                if session_wxid and sender_username == session_wxid
                else _first_clean(
                    _first_clean_from_keys(msg, SENDER_DISPLAY_KEYS),
                    _first_clean_from_keys(msg, SENDER_REMARK_KEYS),
                    _first_clean_from_keys(msg, SENDER_NICKNAME_KEYS),
                    sender_username,
                    peer_name,
                )
            )
            is_self = 0

        platform_id = _first_clean_from_keys(msg, PLATFORM_ID_KEYS)
        msg_id = _first_clean_from_keys(msg, MESSAGE_ID_KEYS)
        local_id = _first_clean_from_keys(msg, LOCAL_ID_KEYS)
        if platform_id:
            message_id = _scoped_message_id(file_scope, platform_id)
        elif msg_id:
            message_id = _scoped_message_id(file_scope, msg_id)
        elif local_id:
            message_id = _scoped_message_id(file_scope, local_id)
        else:
            message_id = _scoped_message_id(file_scope, str(index))
        message_id = _unique_message_id(message_id, used_message_ids)
        for raw_id in (platform_id, msg_id, local_id):
            if raw_id:
                raw_id_map.setdefault(raw_id, message_id)
        reply_to = _reply_to_id(msg)

        messages.append(
            NormMessage(
                id=message_id,
                sender=sender,
                is_self=is_self,
                timestamp=timestamp,
                content=content,
                msg_type=msg_type,
                thread=thread,
                reply_to=reply_to,
            )
        )

    if raw_id_map:
        messages = [
            replace(message, reply_to=raw_id_map.get(message.reply_to, message.reply_to) if message.reply_to else None)
            for message in messages
        ]

    return ParseResult(
        thread=thread,
        total=len(raw_messages),
        included=len(messages),
        skipped_by_type=skipped,
        messages=messages,
    )
