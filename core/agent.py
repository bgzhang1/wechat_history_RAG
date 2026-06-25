from __future__ import annotations

import json
import sys
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from .llm import invoke_chat
from .redaction import redact_data, redact_text
from .tools import TOOLS, TOOLS_BY_NAME


MAX_ROUNDS = 12
MAX_HISTORY_MESSAGES = 40

ENABLED_TOOLS: list[str] = ["search_messages", "semantic_search", "get_context", "browse_by_time", "get_stats"]


def get_active_tools() -> list[Any]:
    return [t for t in TOOLS if t.name in ENABLED_TOOLS]

SYSTEM_PROMPT = """你是微信聊天记录检索助手，通过检索工具查找并回答用户关于聊天记录的问题。

# 核心原则：必须先检索再回答
- 任何关于聊天记录的问题都必须至少调用一次工具获取数据，严禁在未检索的情况下凭空回答或猜测
- 即使你觉得问题很简单或很常见，也必须用工具检索确认，不能凭经验回答

# 工具路由规则
- 问题含具体词（人名、店名、专名、原话片段）→ search_messages
- 问题模糊、主题性、不记得原话 → semantic_search（用完整句子描述，不要只给关键词）
- search_messages 无结果时 → 换 semantic_search 重试，换近义表述
- "某段时间聊了什么" → browse_by_time
- 统计类问题 / 需要了解数据范围时 → get_stats

# 检索纪律
- 命中关键消息后，回答前用 get_context 确认前后文，禁止断章取义
- 结果过多时收窄条件（加时间/发送人/会话过滤），而不是逐页翻完
- 一种工具无结果时，必须换用另一种工具或调整参数（换关键词、放宽时间、去过滤条件）重试，不能一次无结果就放弃
- 最多检索几轮后必须给出结论；信息不足就如实说明你试了哪些方法、缺什么信息

# 回答要求
- 引用原文：发送人 + 时间 + 消息内容
- 明确区分"记录中明确说了"和"根据上下文推断"
- 检索不到就说检索不到，但必须说明你具体尝试了哪些检索方式（用了什么关键词、什么时间范围等），禁止只说"找不到"而不说明尝试过程
- 禁止编造聊天内容"""


def build_system_prompt() -> str:
    active_tool_names = [tool.name for tool in get_active_tools()]
    disabled_tool_names = [tool.name for tool in TOOLS if tool.name not in active_tool_names]
    active_text = ", ".join(active_tool_names) if active_tool_names else "无"
    disabled_text = ", ".join(disabled_tool_names) if disabled_tool_names else "无"

    runtime_policy = f"""# 当前运行工具策略（优先级高于上方提示词中的工具路由）
- 当前启用工具：{active_text}
- 当前停用工具：{disabled_text}
- 只能调用当前启用工具。若上方提示词提到已停用工具，不要调用该工具；可用已启用工具替代时优先替代，否则明确说明该能力当前未启用，需要到设置页开启后再使用。"""

    return f"{SYSTEM_PROMPT.rstrip()}\n\n{runtime_policy}"

EMPTY_REPLY_NUDGE = "请基于上面的工具检索结果，直接回答用户问题。检索不到明确答案就说检索不到，不要输出空内容。"
GREETINGS = {"hi", "hello", "hey", "你好", "您好", "嗨", "哈喽"}

NO_TOOL_NUDGE = """你还没有调用任何检索工具就直接结束了。用户的问题是："{question}"

请根据工具路由规则选择合适的工具开始检索：
- 含具体关键词（人名、店名、原话片段）→ search_messages
- 问题模糊/主题性 → semantic_search
- 时间范围问题 → browse_by_time
- 需要了解数据概况 → get_stats

必须至少尝试一次检索再回答，不能凭空猜测。如果所有工具都无结果，请具体说明你尝试了哪些工具和条件。"""

RETRY_NUDGE = """你的上一次回答看起来像是在没有充分检索的情况下放弃的。用户的问题是："{question}"

请重新审视：你调用过哪些工具？返回了什么信息？如果某个工具无结果，是否尝试了替代方案？
- search_messages 无结果 → 换 semantic_search，用不同的近义描述重试
- 时间范围太窄 → 放宽 after/before 条件
- 会话/发送人过滤太严 → 去掉过滤条件重试

如果多次尝试确实检索不到，请具体说明你尝试了哪些检索策略，以及为什么没有找到。不要在还没穷尽可用工具前就说"找不到"。"""

MAX_NUDGES = 2


def _has_tool_calls_in_history(messages: list[BaseMessage]) -> bool:
    return any(isinstance(message, ToolMessage) for message in messages)


def _looks_like_giving_up(answer: str, *, has_tool_results: bool) -> bool:
    if not answer or len(answer) < 30:
        return True
    if len(answer) >= 200:
        return False
    giving_up_markers = (
        "无法回答", "不知道", "没有找到", "检索不到", "找不到",
        "没有相关", "未找到", "无结果", "无法确定", "没有检索到",
    )
    lower = answer.lower()
    if any(marker in lower for marker in giving_up_markers):
        return True
    if not has_tool_results and len(answer) < 80:
        return True
    return False


def normalize_question(question: str) -> str:
    if not isinstance(question, str):
        raise ValueError("question must be a string")
    normalized = question.strip()
    if not normalized:
        raise ValueError("question cannot be empty")
    return normalized


def local_reply(question: str) -> str | None:
    normalized = question.strip().lower()
    if normalized in GREETINGS:
        return "你好，我在。你可以直接问聊天记录里的时间、地点、人物、原话或某段时间聊了什么。"
    return None


def trim_history(history: list[BaseMessage]) -> None:
    history[:] = [message for message in history if isinstance(message, HumanMessage | AIMessage)]
    try:
        max_messages = int(MAX_HISTORY_MESSAGES)
    except (TypeError, ValueError):
        max_messages = 0
    if max_messages <= 0:
        history.clear()
        return
    if len(history) > max_messages:
        del history[: len(history) - max_messages]
    while history and not isinstance(history[0], HumanMessage):
        del history[0]


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


def _safe_error_detail(exc: Exception, limit: int = 360) -> str:
    return redact_text(exc, limit=limit)


def _tool_args_preview(raw_args: Any, limit: int = 120) -> str:
    try:
        preview = json.dumps(redact_data(raw_args or {}, string_limit=limit), ensure_ascii=False)
    except (TypeError, ValueError):
        preview = redact_text(raw_args, limit=limit)
    if len(preview) <= limit:
        return preview
    return preview[: max(0, limit - 1)].rstrip() + "…"


def _tool_args(raw_args: Any) -> dict[str, Any]:
    if isinstance(raw_args, dict):
        return raw_args
    if isinstance(raw_args, str) and raw_args.strip():
        parsed = json.loads(raw_args)
        if isinstance(parsed, dict):
            return parsed
        raise ValueError("工具参数必须是 JSON 对象。")
    return {}


def _run_tool_call(tool_call: dict[str, Any]) -> ToolMessage:
    if not isinstance(tool_call, dict):
        return ToolMessage(
            content="错误：工具调用格式无效。请重新生成结构化工具调用。",
            tool_call_id="invalid-tool-call",
            name="",
        )

    name = str(tool_call.get("name") or "")
    tool = TOOLS_BY_NAME.get(name)

    if not name:
        result = "错误：工具调用缺少 name。请重新选择一个可用工具并给出参数。"
    elif tool is None:
        result = f"错误：未知工具 {name}"
    elif name not in ENABLED_TOOLS:
        result = f"错误：工具 {name} 当前未启用。请改用已启用工具，或到设置页启用该工具。"
    else:
        try:
            args = _tool_args(tool_call.get("args"))
            result = tool.invoke(args)
        except json.JSONDecodeError:
            result = "工具参数不是合法 JSON。请重新生成结构化参数后再调用该工具。"
        except Exception as exc:
            detail = _safe_error_detail(exc)
            suffix = f"：{detail}" if detail else ""
            result = f"工具执行错误：{type(exc).__name__}{suffix}。请检查参数后重试。"

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
    final = invoke_chat(messages)
    text = _content_to_text(final.content).strip()
    return text or "检索已完成，但模型没有生成有效回答。请换一种问法或缩小时间范围后重试。"


def run_agent(question: str, chat_history: list[BaseMessage] | None = None, verbose: bool = True) -> str:
    question = normalize_question(question)
    chat_history = chat_history if chat_history is not None else []
    trim_history(chat_history)

    local = local_reply(question)
    if local is not None:
        chat_history.extend([HumanMessage(content=question), AIMessage(content=local)])
        trim_history(chat_history)
        return local

    working_messages: list[BaseMessage] = [
        SystemMessage(content=build_system_prompt()),
        *chat_history,
        HumanMessage(content=question),
    ]

    nudge_count = 0

    for _round in range(MAX_ROUNDS):
        ai_message = invoke_chat(working_messages, tools=get_active_tools())
        working_messages.append(ai_message)

        tool_calls = getattr(ai_message, "tool_calls", None) or []
        if not tool_calls:
            answer = _content_to_text(ai_message.content).strip()
            has_tools = _has_tool_calls_in_history(working_messages)

            if not answer and has_tools:
                answer = _synthesize_from_tool_results(question, working_messages)
            elif not answer and not has_tools:
                if nudge_count < MAX_NUDGES and _round < MAX_ROUNDS - 1:
                    nudge_count += 1
                    working_messages.append(HumanMessage(content=NO_TOOL_NUDGE.format(question=question)))
                    if verbose:
                        print("\n  [nudge] 模型未调用任何工具即返回空回答，追加引导提示", file=sys.stderr)
                    continue
                answer = "模型未调用检索工具且返回了空回答，本轮已停止。请确认已导入聊天记录后重试。"
            elif _looks_like_giving_up(answer, has_tool_results=has_tools):
                if nudge_count < MAX_NUDGES and _round < MAX_ROUNDS - 1:
                    nudge_count += 1
                    if not has_tools:
                        working_messages.append(HumanMessage(content=NO_TOOL_NUDGE.format(question=question)))
                    else:
                        working_messages.append(HumanMessage(content=RETRY_NUDGE.format(question=question)))
                    if verbose:
                        print("\n  [nudge] 模型疑似提前放弃，追加引导提示", file=sys.stderr)
                    continue

            chat_history.extend([HumanMessage(content=question), AIMessage(content=answer)])
            trim_history(chat_history)
            return answer

        for tool_call in tool_calls:
            if verbose:
                args_preview = _tool_args_preview(tool_call.get("args", {}) if isinstance(tool_call, dict) else {})
                tool_name = str(tool_call.get("name") or "unknown_tool") if isinstance(tool_call, dict) else "unknown_tool"
                print(f"\n  [tool] {tool_name}({args_preview})", file=sys.stderr)
            working_messages.append(_run_tool_call(tool_call))

    answer = "已达单次提问的检索轮数上限。请缩小时间、人物或关键词范围后重试。"
    chat_history.extend([HumanMessage(content=question), AIMessage(content=answer)])
    trim_history(chat_history)
    return answer
