"""Streaming variant of the agent loop — yields SSE events as the agent works."""

from __future__ import annotations

import json
import sys
from collections.abc import Generator
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

import core.agent as agent_module
from core.llm import chat_model
from core.tools import TOOLS


_abort_flags: dict[str, bool] = {}

def set_abort_flag(session_id: str) -> None:
    _abort_flags[session_id] = True

def clear_abort_flag(session_id: str) -> None:
    _abort_flags.pop(session_id, None)

def _is_aborted(session_id: str) -> bool:
    return _abort_flags.get(session_id, False)


def _sse(event: str, data: Any) -> str:
    """Format a single SSE event."""
    payload = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {payload}\n\n"


def stream_agent(
    question: str,
    chat_history: list[BaseMessage],
    session_id: str,
) -> Generator[str, None, None]:
    """
    Run the agent loop, yielding SSE-formatted events:

    - ``event: tool_call``  — ``{"name": "...", "args": "..."}``
    - ``event: text``       — ``{"chunk": "..."}``
    - ``event: done``       — ``{"answer": "...", "session_id": "..."}``
    - ``event: error``      — ``{"detail": "..."}``
    """

    # Fast-path for greetings
    local = agent_module.local_reply(question)
    if local is not None:
        chat_history.extend([HumanMessage(content=question), AIMessage(content=local)])
        agent_module.trim_history(chat_history)
        yield _sse("text", {"chunk": local})
        yield _sse("done", {"answer": local, "session_id": session_id})
        return

    llm_with_tools = chat_model().bind_tools(agent_module.get_active_tools())
    llm_plain = chat_model()
    working_messages: list[BaseMessage] = [
        SystemMessage(content=agent_module.SYSTEM_PROMPT),
        *chat_history,
        HumanMessage(content=question),
    ]

    full_answer = ""

    clear_abort_flag(session_id)
    try:
        for _round in range(agent_module.MAX_ROUNDS):
            if _is_aborted(session_id):
                full_answer = "（生成已被用户强制终止）"
                yield _sse("text", {"chunk": full_answer})
                break

            ai_message = llm_with_tools.invoke(working_messages)
            working_messages.append(ai_message)

            tool_calls = getattr(ai_message, "tool_calls", None) or []

            if not tool_calls:
                text = agent_module._content_to_text(ai_message.content).strip()

                if not text and any(isinstance(m, ToolMessage) for m in working_messages):
                    synth_messages = [
                        *working_messages,
                        HumanMessage(content=f"用户原问题：{question}\n\n{agent_module.EMPTY_REPLY_NUDGE}"),
                    ]
                    for chunk in llm_plain.stream(synth_messages):
                        piece = agent_module._content_to_text(chunk.content)
                        if piece:
                            full_answer += piece
                            yield _sse("text", {"chunk": piece})
                    if not full_answer:
                        full_answer = "检索已完成，但模型没有生成有效回答。请换一种问法或缩小时间范围后重试。"
                        yield _sse("text", {"chunk": full_answer})
                elif not text:
                    full_answer = "模型返回了空回答，本轮已停止以避免空转。"
                    yield _sse("text", {"chunk": full_answer})
                else:
                    full_answer = text
                    yield _sse("text", {"chunk": text})

                break
            else:
                for tool_call in tool_calls:
                    args_preview = json.dumps(tool_call.get("args", {}), ensure_ascii=False)[:200]
                    yield _sse("tool_call", {"name": tool_call["name"], "args": args_preview})
                    print(f"  [tool] {tool_call['name']}({args_preview[:120]})", file=sys.stderr)
                    tool_msg = agent_module._run_tool_call(tool_call)
                    working_messages.append(tool_msg)

                    # Emit a tool_result summary so the frontend can show progress
                    try:
                        result_data = json.loads(tool_msg.content)
                        summary_parts = []
                        if "total_count" in result_data:
                            summary_parts.append(f"找到 {result_data['total_count']} 条消息")
                        if "returned" in result_data:
                            summary_parts.append(f"返回 {result_data['returned']} 条")
                        if "sessions" in result_data:
                            summary_parts.append(f"匹配 {len(result_data['sessions'])} 个会话块")
                        if "messages" in result_data and not summary_parts:
                            summary_parts.append(f"获取了 {len(result_data['messages'])} 条上下文消息")
                        tool_summary = "，".join(summary_parts) if summary_parts else "调用完成"
                    except (json.JSONDecodeError, TypeError):
                        tool_summary = "调用完成"
                    yield _sse("tool_result", {
                        "name": tool_call["name"],
                        "summary": tool_summary,
                    })
        else:
            full_answer = "已达单次提问的检索轮数上限。请缩小时间、人物或关键词范围后重试。"
            yield _sse("text", {"chunk": full_answer})

    except Exception as exc:
        yield _sse("error", {"detail": f"Agent 执行出错：{exc}"})
        return

    finally:
        clear_abort_flag(session_id)

    chat_history.extend([HumanMessage(content=question), AIMessage(content=full_answer)])
    agent_module.trim_history(chat_history)

    yield _sse("done", {"answer": full_answer, "session_id": session_id})
