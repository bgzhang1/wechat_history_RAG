"""Streaming variant of the agent loop - yields SSE events as the agent works."""

from __future__ import annotations

import json
import sys
import threading
from collections.abc import Generator
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

import core.agent as agent_module
from core.llm import chat_model

from .redaction import public_exception_message, redact_data, redact_text


_abort_flags: dict[str, bool] = {}
_abort_lock = threading.Lock()
ABORTED_ANSWER = "（用户已停止生成）"
TOOL_ARGS_PREVIEW_LIMIT = 200
TOOL_ERROR_SUMMARY_LIMIT = 160
REASONING_KEYS = (
    "reasoning_content",
    "reasoning",
    "thinking",
    "reasoning_text",
    "reasoning_summary",
)
REASONING_BLOCK_TYPES = {"reasoning", "thinking", "reasoning_content", "reasoning_summary"}
TEXT_BLOCK_TYPES = {"text", "output_text"}


def set_abort_flag(session_id: str) -> None:
    with _abort_lock:
        _abort_flags[session_id] = True


def clear_abort_flag(session_id: str) -> None:
    with _abort_lock:
        _abort_flags.pop(session_id, None)


def _is_aborted(session_id: str) -> bool:
    with _abort_lock:
        return _abort_flags.get(session_id, False)


def _sse(event: str, data: Any) -> str:
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def _stringify_reasoning(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_stringify_reasoning(item) for item in value)
    if isinstance(value, dict):
        parts: list[str] = []
        for key in ("text", "content", "summary", "reasoning", "reasoning_content"):
            if key in value:
                parts.append(_stringify_reasoning(value.get(key)))
        if parts:
            return "".join(parts)
        return ""
    return str(value)


def _split_content_parts(content: Any) -> tuple[str, str]:
    if isinstance(content, str):
        return content, ""
    if not isinstance(content, list):
        return agent_module._content_to_text(content), ""

    text_parts: list[str] = []
    reasoning_parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            text_parts.append(item)
            continue
        if not isinstance(item, dict):
            text_parts.append(str(item))
            continue
        block_type = str(item.get("type") or "").lower()
        if block_type in REASONING_BLOCK_TYPES or "reasoning" in block_type or "thinking" in block_type:
            reasoning_parts.append(_stringify_reasoning(item))
        elif block_type in TEXT_BLOCK_TYPES:
            text_parts.append(str(item.get("text") or item.get("content") or ""))
        else:
            text_parts.append(str(item.get("text") or item.get("content") or ""))
    return "".join(text_parts), "".join(reasoning_parts)


def _message_text(message: BaseMessage) -> str:
    text, _reasoning = _split_content_parts(getattr(message, "content", ""))
    return text


def _message_reasoning(message: BaseMessage) -> str:
    _text, reasoning = _split_content_parts(getattr(message, "content", ""))
    parts = [reasoning]
    for source_name in ("additional_kwargs", "response_metadata"):
        source = getattr(message, source_name, None)
        if not isinstance(source, dict):
            continue
        for key in REASONING_KEYS:
            parts.append(_stringify_reasoning(source.get(key)))
    return "".join(part for part in parts if part)


def _tool_result_summary(tool_content: str) -> str:
    text_content = str(tool_content or "").strip()
    try:
        result_data = json.loads(text_content)
    except (json.JSONDecodeError, TypeError):
        if text_content.startswith(("错误：", "工具执行错误：", "工具参数不是合法 JSON")):
            return redact_text(text_content, limit=TOOL_ERROR_SUMMARY_LIMIT)
        return "工具调用已完成"

    if isinstance(result_data, dict) and result_data.get("error"):
        detail = redact_text(result_data.get("error"), limit=TOOL_ERROR_SUMMARY_LIMIT)
        return f"工具执行错误：{detail}" if detail else "工具执行错误"

    summary_parts: list[str] = []
    if "total_count" in result_data:
        summary_parts.append(f"命中 {result_data['total_count']} 条消息")
    if "returned" in result_data:
        summary_parts.append(f"返回 {result_data['returned']} 条")
    if "sessions" in result_data:
        summary_parts.append(f"匹配 {len(result_data['sessions'])} 个会话块")
    if "messages" in result_data and not summary_parts:
        summary_parts.append(f"载入 {len(result_data['messages'])} 条上下文")
    return "，".join(summary_parts) if summary_parts else "工具调用已完成"


def _tool_args_preview(args: Any, limit: int = TOOL_ARGS_PREVIEW_LIMIT) -> str:
    try:
        preview = json.dumps(redact_data(args or {}, string_limit=limit), ensure_ascii=False)
    except (TypeError, ValueError):
        preview = redact_text(args, limit=limit)

    if len(preview) <= limit:
        return preview
    return preview[: max(0, limit - 1)].rstrip() + "…"


def stream_agent(
    question: str,
    chat_history: list[BaseMessage],
    session_id: str,
) -> Generator[str, None, None]:
    """
    Run the agent loop, yielding SSE-formatted events:

    - ``event: tool_call``: ``{"name": "...", "args": "..."}``
    - ``event: tool_result``: ``{"name": "...", "summary": "..."}``
    - ``event: text``: ``{"chunk": "..."}``
    - ``event: done``: ``{"answer": "...", "session_id": "..."}``
    - ``event: error``: ``{"detail": "..."}``
    """

    try:
        question = agent_module.normalize_question(question)
    except ValueError as exc:
        yield _sse("error", {"detail": str(exc)})
        return
    agent_module.trim_history(chat_history)

    local = agent_module.local_reply(question)
    if local is not None:
        chat_history.extend([HumanMessage(content=question), AIMessage(content=local)])
        agent_module.trim_history(chat_history)
        yield _sse("text", {"chunk": local})
        yield _sse("done", {"answer": local, "thinking": "", "session_id": session_id})
        return

    try:
        llm_with_tools = chat_model().bind_tools(agent_module.get_active_tools())
        llm_plain = chat_model()
    except Exception as exc:
        yield _sse("error", {"detail": public_exception_message("Agent 初始化失败", exc)})
        return

    working_messages: list[BaseMessage] = [
        SystemMessage(content=agent_module.build_system_prompt()),
        *chat_history,
        HumanMessage(content=question),
    ]

    full_answer = ""
    full_thinking = ""

    try:
        for _round in range(agent_module.MAX_ROUNDS):
            if _is_aborted(session_id):
                full_answer = ABORTED_ANSWER
                yield _sse("text", {"chunk": full_answer})
                break

            ai_message = llm_with_tools.invoke(working_messages)
            thinking = _message_reasoning(ai_message)
            if thinking:
                full_thinking += thinking
                yield _sse("thinking", {"chunk": thinking})
            if _is_aborted(session_id):
                full_answer = ABORTED_ANSWER
                yield _sse("text", {"chunk": full_answer})
                break

            working_messages.append(ai_message)
            tool_calls = getattr(ai_message, "tool_calls", None) or []

            if not tool_calls:
                text = _message_text(ai_message).strip()

                if not text and any(isinstance(m, ToolMessage) for m in working_messages):
                    synth_messages = [
                        *working_messages,
                        HumanMessage(content=f"User original question: {question}\n\n{agent_module.EMPTY_REPLY_NUDGE}"),
                    ]
                    for chunk in llm_plain.stream(synth_messages):
                        if _is_aborted(session_id):
                            full_answer = (
                                ABORTED_ANSWER
                                if not full_answer
                                else f"{full_answer}\n{ABORTED_ANSWER}"
                            )
                            yield _sse("text", {"chunk": ABORTED_ANSWER})
                            break
                        thinking_piece = _message_reasoning(chunk)
                        if thinking_piece:
                            full_thinking += thinking_piece
                            yield _sse("thinking", {"chunk": thinking_piece})
                        piece = _message_text(chunk)
                        if piece:
                            full_answer += piece
                            yield _sse("text", {"chunk": piece})
                    if _is_aborted(session_id):
                        break
                    if not full_answer:
                        full_answer = (
                            "检索已完成，但模型没有生成有效回答。请换一种问法，或缩小时间范围后重试。"
                        )
                        yield _sse("text", {"chunk": full_answer})
                elif not text:
                    full_answer = "模型返回了空回答，本轮已停止。"
                    yield _sse("text", {"chunk": full_answer})
                else:
                    full_answer = text
                    yield _sse("text", {"chunk": text})

                break

            aborted_during_tools = False
            for tool_call in tool_calls:
                if _is_aborted(session_id):
                    full_answer = ABORTED_ANSWER
                    yield _sse("text", {"chunk": full_answer})
                    aborted_during_tools = True
                    break

                args = tool_call.get("args", {}) if isinstance(tool_call, dict) else {}
                tool_name = str(tool_call.get("name") or "unknown_tool") if isinstance(tool_call, dict) else "unknown_tool"
                args_preview = _tool_args_preview(args)
                yield _sse("tool_call", {"name": tool_name, "args": args_preview})
                print(f"  [tool] {tool_name}({args_preview[:120]})", file=sys.stderr)

                tool_msg = agent_module._run_tool_call(tool_call)
                working_messages.append(tool_msg)

                if _is_aborted(session_id):
                    full_answer = ABORTED_ANSWER
                    yield _sse("text", {"chunk": full_answer})
                    aborted_during_tools = True
                    break

                yield _sse(
                    "tool_result",
                    {
                        "name": tool_name,
                        "summary": _tool_result_summary(str(tool_msg.content)),
                    },
                )

            if aborted_during_tools:
                break
        else:
            full_answer = (
                "已达到单次提问的工具调用轮数上限。请缩小时间、人物或关键词范围后重试。"
            )
            yield _sse("text", {"chunk": full_answer})

    except Exception as exc:
        yield _sse("error", {"detail": public_exception_message("Agent 执行失败", exc)})
        return

    finally:
        clear_abort_flag(session_id)

    chat_history.extend([HumanMessage(content=question), AIMessage(content=full_answer)])
    agent_module.trim_history(chat_history)

    yield _sse("done", {"answer": full_answer, "thinking": full_thinking, "session_id": session_id})
