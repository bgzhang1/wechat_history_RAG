from __future__ import annotations

import json
import sys
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from .llm import chat_model
from .tools import TOOLS, TOOLS_BY_NAME


MAX_ROUNDS = 100
MAX_HISTORY_MESSAGES = 40

SYSTEM_PROMPT = """你是微信聊天记录检索助手，通过检索工具查找并回答用户关于聊天记录的问题。

# 工具路由规则
- 问题含具体词（人名、店名、专名、原话片段）→ search_messages
- 问题模糊、主题性、不记得原话 → semantic_search（用完整句子描述，不要只给关键词）
- search_messages 无结果时 → 换 semantic_search 重试，换近义表述
- "某段时间聊了什么" → browse_by_time
- 统计类问题 / 需要了解数据范围时 → get_stats

# 检索纪律
- 命中关键消息后，回答前用 get_context 确认前后文，禁止断章取义
- 结果过多时收窄条件（加时间/发送人/会话过滤），而不是逐页翻完
- 最多检索几轮后必须给出结论；信息不足就如实说明缺什么

# 回答要求
- 引用原文：发送人 + 时间 + 消息内容
- 明确区分"记录中明确说了"和"根据上下文推断"
- 检索不到就说检索不到，禁止编造聊天内容"""

EMPTY_REPLY_NUDGE = "请基于上面的工具检索结果，直接回答用户问题。检索不到明确答案就说检索不到，不要输出空内容。"
GREETINGS = {"hi", "hello", "hey", "你好", "您好", "嗨", "哈喽"}


def local_reply(question: str) -> str | None:
    normalized = question.strip().lower()
    if normalized in GREETINGS:
        return "你好，我在。你可以直接问聊天记录里的时间、地点、人物、原话或某段时间聊了什么。"
    return None


def trim_history(history: list[BaseMessage]) -> None:
    if len(history) > MAX_HISTORY_MESSAGES:
        del history[: len(history) - MAX_HISTORY_MESSAGES]


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(item["text"])
            elif isinstance(item, str):
                parts.append(item)
        return "\n".join(parts)
    return "" if content is None else str(content)


def _tool_args(raw_args: Any) -> dict[str, Any]:
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str) and raw_args.strip():
        return json.loads(raw_args)
    return {}


def _run_tool_call(tool_call: dict[str, Any]) -> ToolMessage:
    name = tool_call["name"]
    args = _tool_args(tool_call.get("args"))
    tool = TOOLS_BY_NAME.get(name)

    if tool is None:
        result = f"错误：未知工具 {name}"
    else:
        try:
            result = tool.invoke(args)
        except Exception as exc:
            result = f"工具执行错误：{exc}。请检查参数后重试。"

    return ToolMessage(
        content=result,
        tool_call_id=tool_call.get("id") or f"{name}-missing-id",
        name=name,
    )


def _synthesize_from_tool_results(question: str, working_messages: list[BaseMessage]) -> str:
    messages = [
        *working_messages,
        HumanMessage(content=f"用户原问题：{question}\n\n{EMPTY_REPLY_NUDGE}"),
    ]
    final = chat_model().invoke(messages)
    text = _content_to_text(final.content).strip()
    return text or "检索已完成，但模型没有生成有效回答。请换一种问法或缩小时间范围后重试。"


def run_agent(question: str, chat_history: list[BaseMessage] | None = None, verbose: bool = True) -> str:
    chat_history = chat_history if chat_history is not None else []

    local = local_reply(question)
    if local is not None:
        chat_history.extend([HumanMessage(content=question), AIMessage(content=local)])
        trim_history(chat_history)
        return local

    llm_with_tools = chat_model().bind_tools(TOOLS)
    working_messages: list[BaseMessage] = [
        SystemMessage(content=SYSTEM_PROMPT),
        *chat_history,
        HumanMessage(content=question),
    ]

    for _round in range(MAX_ROUNDS):
        ai_message = llm_with_tools.invoke(working_messages)
        working_messages.append(ai_message)

        tool_calls = getattr(ai_message, "tool_calls", None) or []
        if not tool_calls:
            answer = _content_to_text(ai_message.content).strip()
            if not answer and any(isinstance(message, ToolMessage) for message in working_messages):
                answer = _synthesize_from_tool_results(question, working_messages)
            elif not answer:
                answer = "模型返回了空回答，本轮已停止以避免空转。"

            chat_history.extend([HumanMessage(content=question), AIMessage(content=answer)])
            trim_history(chat_history)
            return answer

        for tool_call in tool_calls:
            if verbose:
                args_preview = json.dumps(tool_call.get("args", {}), ensure_ascii=False)[:120]
                print(f"\n  [tool] {tool_call['name']}({args_preview})", file=sys.stderr)
            working_messages.append(_run_tool_call(tool_call))

    answer = "已达单次提问的检索轮数上限。请缩小时间、人物或关键词范围后重试。"
    chat_history.extend([HumanMessage(content=question), AIMessage(content=answer)])
    trim_history(chat_history)
    return answer
