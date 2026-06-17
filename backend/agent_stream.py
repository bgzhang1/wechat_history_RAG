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


_abort_flags: dict[str, bool] = {}
_abort_lock = threading.Lock()
ABORTED_ANSWER = "(generation aborted by user)"


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


def _tool_result_summary(tool_content: str) -> str:
    try:
        result_data = json.loads(tool_content)
    except (json.JSONDecodeError, TypeError):
        return "Tool call completed"

    summary_parts: list[str] = []
    if "total_count" in result_data:
        summary_parts.append(f"found {result_data['total_count']} messages")
    if "returned" in result_data:
        summary_parts.append(f"returned {result_data['returned']}")
    if "sessions" in result_data:
        summary_parts.append(f"matched {len(result_data['sessions'])} session chunks")
    if "messages" in result_data and not summary_parts:
        summary_parts.append(f"loaded {len(result_data['messages'])} context messages")
    return ", ".join(summary_parts) if summary_parts else "Tool call completed"


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

    local = agent_module.local_reply(question)
    if local is not None:
        chat_history.extend([HumanMessage(content=question), AIMessage(content=local)])
        agent_module.trim_history(chat_history)
        yield _sse("text", {"chunk": local})
        yield _sse("done", {"answer": local, "session_id": session_id})
        return

    try:
        llm_with_tools = chat_model().bind_tools(agent_module.get_active_tools())
        llm_plain = chat_model()
    except Exception as exc:
        yield _sse("error", {"detail": f"Agent initialization failed: {exc}"})
        return

    working_messages: list[BaseMessage] = [
        SystemMessage(content=agent_module.SYSTEM_PROMPT),
        *chat_history,
        HumanMessage(content=question),
    ]

    full_answer = ""

    try:
        for _round in range(agent_module.MAX_ROUNDS):
            if _is_aborted(session_id):
                full_answer = ABORTED_ANSWER
                yield _sse("text", {"chunk": full_answer})
                break

            ai_message = llm_with_tools.invoke(working_messages)
            if _is_aborted(session_id):
                full_answer = ABORTED_ANSWER
                yield _sse("text", {"chunk": full_answer})
                break

            working_messages.append(ai_message)
            tool_calls = getattr(ai_message, "tool_calls", None) or []

            if not tool_calls:
                text = agent_module._content_to_text(ai_message.content).strip()

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
                        piece = agent_module._content_to_text(chunk.content)
                        if piece:
                            full_answer += piece
                            yield _sse("text", {"chunk": piece})
                    if _is_aborted(session_id):
                        break
                    if not full_answer:
                        full_answer = (
                            "Search completed, but the model did not produce a useful answer. "
                            "Try rephrasing or narrowing the time range."
                        )
                        yield _sse("text", {"chunk": full_answer})
                elif not text:
                    full_answer = "The model returned an empty answer; this round has stopped."
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

                args_preview = json.dumps(tool_call.get("args", {}), ensure_ascii=False)[:200]
                yield _sse("tool_call", {"name": tool_call["name"], "args": args_preview})
                print(f"  [tool] {tool_call['name']}({args_preview[:120]})", file=sys.stderr)

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
                        "name": tool_call["name"],
                        "summary": _tool_result_summary(str(tool_msg.content)),
                    },
                )

            if aborted_during_tools:
                break
        else:
            full_answer = (
                "The single-question tool-call limit was reached. "
                "Try narrowing the time, person, or keyword range."
            )
            yield _sse("text", {"chunk": full_answer})

    except Exception as exc:
        yield _sse("error", {"detail": f"Agent execution failed: {exc}"})
        return

    finally:
        clear_abort_flag(session_id)

    chat_history.extend([HumanMessage(content=question), AIMessage(content=full_answer)])
    agent_module.trim_history(chat_history)

    yield _sse("done", {"answer": full_answer, "session_id": session_id})
