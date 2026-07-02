from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from . import store
from .llm import invoke_chat
from .redaction import redact_data, redact_text
from .tools import TOOLS, TOOLS_BY_NAME


MAX_HISTORY_MESSAGES = 40

DATA_OVERVIEW_TOP_THREADS = 12
DATA_OVERVIEW_TOP_SENDERS = 12
WEEKDAY_NAMES = ("周一", "周二", "周三", "周四", "周五", "周六", "周日")

# ── 检索努力档位：控制轮数预算、引导次数和检索纪律 ──────────────────────
SEARCH_EFFORT_PRESETS: dict[str, dict[str, Any]] = {
    "low": {
        "max_rounds": 6,
        "max_nudges": 1,
        "guidance": (
            "# 检索努力程度：low（快速模式）\n"
            "- 目标是快：用最直接的 1~3 次工具调用找到答案就作答\n"
            "- 首选最可能命中的一种工具；命中后仅在明显有断章取义风险时才用 get_context\n"
            "- 换一种工具重试一次仍无结果就如实说明，不做穷举"
        ),
    },
    "medium": {
        "max_rounds": 12,
        "max_nudges": 2,
        "guidance": (
            "# 检索努力程度：medium（均衡模式）\n"
            "- 按工具路由规则检索，无结果时至少换一种工具、换一组参数重试\n"
            "- 关键结论回答前用 get_context 确认前后文"
        ),
    },
    "high": {
        "max_rounds": 20,
        "max_nudges": 3,
        "guidance": (
            "# 检索努力程度：high（深挖模式）\n"
            "- 多角度检索：关键词与语义并用，尝试同义词、别称、相关时间段\n"
            "- 每个关键结论必须 get_context 验证，重要事实用第二种工具交叉印证\n"
            "- 无结果时系统地放宽条件（去过滤 → 扩时间 → 换表述）后再下结论"
        ),
    },
    "max": {
        "max_rounds": 32,
        "max_nudges": 4,
        "guidance": (
            "# 检索努力程度：max（穷尽模式）\n"
            "- 穷尽可用手段：多组关键词、多种语义表述、必要时分时间段 browse_by_time 逐段排查\n"
            "- 交叉验证所有关键事实，主动检索反例排除歧义\n"
            "- 只有穷尽以上手段后才允许说检索不到，并完整列出已尝试的策略"
        ),
    },
}
DEFAULT_SEARCH_EFFORT = "medium"


def _normalize_effort(value: Any) -> str | None:
    normalized = str(value or "").strip().lower()
    return normalized if normalized in SEARCH_EFFORT_PRESETS else None


SEARCH_EFFORT = _normalize_effort(os.getenv("SEARCH_EFFORT")) or DEFAULT_SEARCH_EFFORT
MAX_ROUNDS = int(SEARCH_EFFORT_PRESETS[SEARCH_EFFORT]["max_rounds"])
MAX_NUDGES = int(SEARCH_EFFORT_PRESETS[SEARCH_EFFORT]["max_nudges"])


def set_search_effort(level: str) -> dict[str, Any]:
    """切换全局检索努力档位，并同步轮数/引导预算。返回该档位预设。"""
    global SEARCH_EFFORT, MAX_ROUNDS, MAX_NUDGES
    normalized = _normalize_effort(level)
    if normalized is None:
        raise ValueError(f"未知检索努力档位：{level}。可选：{', '.join(SEARCH_EFFORT_PRESETS)}")
    SEARCH_EFFORT = normalized
    preset = SEARCH_EFFORT_PRESETS[normalized]
    MAX_ROUNDS = int(preset["max_rounds"])
    MAX_NUDGES = int(preset["max_nudges"])
    return dict(preset)


def _resolve_effort(effort: str | None) -> tuple[str, int, int]:
    """单次调用的档位解析：显式传入且不同于全局档位时用预设值，否则用全局值。

    全局值可能被设置页单独调过（如 medium 档手动改 max_rounds），此时保持尊重。
    """
    explicit = _normalize_effort(effort)
    if explicit is not None and explicit != SEARCH_EFFORT:
        preset = SEARCH_EFFORT_PRESETS[explicit]
        return explicit, int(preset["max_rounds"]), int(preset["max_nudges"])
    return SEARCH_EFFORT, int(MAX_ROUNDS), int(MAX_NUDGES)


ENABLED_TOOLS: list[str] = ["search_messages", "semantic_search", "get_context", "browse_by_time", "get_stats"]


def get_active_tools() -> list[Any]:
    return [t for t in TOOLS if t.name in ENABLED_TOOLS]

SYSTEM_PROMPT = """你是微信聊天记录检索助手，通过检索工具查找并回答用户关于聊天记录的问题。

# 核心原则：必须先检索再回答
- 任何关于聊天记录的问题都必须至少调用一次工具获取数据，严禁在未检索的情况下凭空回答或猜测
- 即使你觉得问题很简单或很常见，也必须用工具检索确认，不能凭经验回答

# 工具路由规则
- 问题含具体词（人名、店名、专名、原话片段）→ search_messages
- search_messages 的多个关键词是 AND 关系（须在同一条消息内全部出现），关键词宜少而精（1~2 个）；AND 无命中时系统会自动放宽为 OR 并在 note 中说明
- 不要把整句话当关键词传给 search_messages；长句、描述性内容改用 semantic_search
- 问题模糊、主题性、不记得原话 → semantic_search（用完整句子描述，不要只给关键词）
- search_messages 无结果时 → 换 semantic_search 重试，换近义表述
- 问题含相对时间（今天/昨天/上周/最近/上个月等）→ 先按"数据概况"中的当前时间换算成具体日期，再配 after/before 过滤；超出记录时间范围时直接说明
- "某段时间聊了什么" → browse_by_time
- 统计类问题 / 需要了解数据范围时 → get_stats
- semantic_search 结果中的 message_ids_sample 可直接作为 get_context 的 message_id 使用

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


def _current_time_text() -> str:
    now = datetime.now()
    return f"{now.strftime('%Y-%m-%d %H:%M')}（{WEEKDAY_NAMES[now.weekday()]}）"


DATA_OVERVIEW_CACHE_TTL_SECONDS = 10.0
_data_overview_cache: dict[str, tuple[float, str]] = {}


def _cached_corpus_overview_lines() -> list[str]:
    """数据概况的库统计部分（不含当前时间），按 DB 路径做短 TTL 缓存。

    三组 GROUP BY 聚合在大库上有可感知开销，且每次提问都会构建系统提示词；
    10 秒内的数据概况差异对检索路由没有影响。
    """
    cache_key = str(store.DB_PATH)
    now = time.monotonic()
    cached = _data_overview_cache.get(cache_key)
    if cached is not None and now - cached[0] < DATA_OVERVIEW_CACHE_TTL_SECONDS:
        return cached[1].splitlines()

    lines = _build_corpus_overview_lines()
    _data_overview_cache.clear()  # 只保留当前库，避免测试/换库场景堆积
    _data_overview_cache[cache_key] = (now, "\n".join(lines))
    return lines


def _build_corpus_overview_lines() -> list[str]:
    lines: list[str] = []
    summary = store.stats_summary(include_message_types=False)
    total = int(summary.get("total_messages") or 0)
    if total <= 0:
        lines.append("- 数据库为空（0 条消息）：直接告知用户先到导入页导入聊天记录，不要调用检索工具。")
        return lines
    span = summary.get("time_span") or {}
    earliest = str(span.get("earliest") or "").replace("T", " ")
    latest = str(span.get("latest") or "").replace("T", " ")
    lines.append(
        f"- 记录范围：{earliest} ~ {latest}，共 {total} 条消息、"
        f"{summary.get('thread_count')} 个会话、{summary.get('sender_count')} 个发送人"
    )
    threads = store.stats_threads(limit=DATA_OVERVIEW_TOP_THREADS)
    if threads["items"]:
        shown = "、".join(f"{item['thread']}({item['count']})" for item in threads["items"])
        rest = int(threads["total_count"]) - len(threads["items"])
        lines.append(f"- 会话（按消息量前 {len(threads['items'])}）：{shown}" + (f"，另有 {rest} 个" if rest > 0 else ""))
    senders = store.stats_senders(limit=DATA_OVERVIEW_TOP_SENDERS)
    if senders["items"]:
        shown = "、".join(
            f"{item['sender']}{'(我)' if item.get('is_self') else ''}({item['count']})"
            for item in senders["items"]
        )
        rest = int(senders["total_count"]) - len(senders["items"])
        lines.append(f"- 发送人（按消息量前 {len(senders['items'])}）：{shown}" + (f"，另有 {rest} 个" if rest > 0 else ""))
    lines.append("- 相对时间必须按上面的当前时间换算成具体日期；检索时间范围不要超出记录范围。")
    return lines


def build_data_overview() -> str:
    """动态数据概况：当前时间 + 库内数据范围，帮助模型换算相对时间、选对过滤条件。"""
    lines = [f"- 当前时间：{_current_time_text()}"]
    try:
        lines.extend(_cached_corpus_overview_lines())
    except Exception:
        # 概况仅是辅助信息，读取失败时不阻断问答
        pass
    return "# 数据概况\n" + "\n".join(lines)


def build_system_prompt(effort: str | None = None) -> str:
    active_tool_names = [tool.name for tool in get_active_tools()]
    disabled_tool_names = [tool.name for tool in TOOLS if tool.name not in active_tool_names]
    active_text = ", ".join(active_tool_names) if active_tool_names else "无"
    disabled_text = ", ".join(disabled_tool_names) if disabled_tool_names else "无"

    runtime_policy = f"""# 当前运行工具策略（优先级高于上方提示词中的工具路由）
- 当前启用工具：{active_text}
- 当前停用工具：{disabled_text}
- 只能调用当前启用工具。若上方提示词提到已停用工具，不要调用该工具；可用已启用工具替代时优先替代，否则明确说明该能力当前未启用，需要到设置页开启后再使用。"""

    level = _normalize_effort(effort) or SEARCH_EFFORT
    effort_guidance = str(SEARCH_EFFORT_PRESETS[level]["guidance"])

    return f"{SYSTEM_PROMPT.rstrip()}\n\n{runtime_policy}\n\n{effort_guidance}\n\n{build_data_overview()}"

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

# 含具体日期/时间引用（如 2024-06-15、6月15日、10:30）视为有据可依的回答
_EVIDENCE_RE = re.compile(r"\d{4}-\d{1,2}-\d{1,2}|\d{1,2}月\d{1,2}[日号]|\d{1,2}:\d{2}")

EXHAUSTED_NUDGE = (
    "已达到本次提问的检索轮数上限，不要再调用工具。"
    "请立即基于上面已获取的全部工具检索结果直接回答用户问题："
    "能确定的部分给出结论并引用原文（发送人+时间+内容）；"
    "不能确定的部分如实说明已尝试的检索方式和缺少的信息。"
)

DUPLICATE_TOOL_CALL_NOTE = (
    "重复调用：本次提问中已用完全相同的参数调用过 {name}，结果不会变化，本次未执行。"
    "请更换检索策略：换关键词或减少关键词、调整 after/before 时间范围、增删 sender/thread 过滤，"
    "或改用其他已启用工具。"
)


def _has_tool_calls_in_history(messages: list[BaseMessage]) -> bool:
    return any(isinstance(message, ToolMessage) for message in messages)


def _looks_like_giving_up(answer: str, *, has_tool_results: bool) -> bool:
    if not answer:
        return True
    if has_tool_results and _EVIDENCE_RE.search(answer):
        # 已引用具体日期/时间的回答视为有依据；
        # 即使包含"没有找到"（如实说明部分未命中）也不算放弃
        return False
    if len(answer) < 30:
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


def _tool_call_signature(name: str, raw_args: Any) -> str | None:
    """规范化 (工具名, 参数) 为可比对签名；参数非法时返回 None（交由执行层报错）。"""
    try:
        args = _tool_args(raw_args)
        return f"{name}:{json.dumps(args, ensure_ascii=False, sort_keys=True)}"
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _run_tool_call(tool_call: dict[str, Any], executed_signatures: set[str] | None = None) -> ToolMessage:
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
        signature = _tool_call_signature(name, tool_call.get("args"))
        if executed_signatures is not None and signature is not None and signature in executed_signatures:
            result = DUPLICATE_TOOL_CALL_NOTE.format(name=name)
        else:
            try:
                args = _tool_args(tool_call.get("args"))
                result = tool.invoke(args)
                # 仅在成功执行后记录签名，失败的调用允许原样重试
                if executed_signatures is not None and signature is not None:
                    executed_signatures.add(signature)
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


def _synthesize_from_tool_results(
    question: str,
    working_messages: list[BaseMessage],
    nudge: str = EMPTY_REPLY_NUDGE,
) -> str:
    messages = [
        *working_messages,
        HumanMessage(content=f"用户原问题：{question}\n\n{nudge}"),
    ]
    final = invoke_chat(messages)
    text = _content_to_text(final.content).strip()
    return text or "检索已完成，但模型没有生成有效回答。请换一种问法或缩小时间范围后重试。"


def run_agent(
    question: str,
    chat_history: list[BaseMessage] | None = None,
    verbose: bool = True,
    effort: str | None = None,
) -> str:
    question = normalize_question(question)
    chat_history = chat_history if chat_history is not None else []
    trim_history(chat_history)

    local = local_reply(question)
    if local is not None:
        chat_history.extend([HumanMessage(content=question), AIMessage(content=local)])
        trim_history(chat_history)
        return local

    level, max_rounds, max_nudges = _resolve_effort(effort)
    working_messages: list[BaseMessage] = [
        SystemMessage(content=build_system_prompt(level)),
        *chat_history,
        HumanMessage(content=question),
    ]

    nudge_count = 0
    executed_signatures: set[str] = set()

    for _round in range(max_rounds):
        ai_message = invoke_chat(working_messages, tools=get_active_tools())
        working_messages.append(ai_message)

        tool_calls = getattr(ai_message, "tool_calls", None) or []
        if not tool_calls:
            answer = _content_to_text(ai_message.content).strip()
            has_tools = _has_tool_calls_in_history(working_messages)

            if not answer and has_tools:
                answer = _synthesize_from_tool_results(question, working_messages)
            elif not answer and not has_tools:
                if nudge_count < max_nudges and _round < max_rounds - 1:
                    nudge_count += 1
                    working_messages.append(HumanMessage(content=NO_TOOL_NUDGE.format(question=question)))
                    if verbose:
                        print("\n  [nudge] 模型未调用任何工具即返回空回答，追加引导提示", file=sys.stderr)
                    continue
                answer = "模型未调用检索工具且返回了空回答，本轮已停止。请确认已导入聊天记录后重试。"
            elif _looks_like_giving_up(answer, has_tool_results=has_tools):
                if nudge_count < max_nudges and _round < max_rounds - 1:
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
            working_messages.append(_run_tool_call(tool_call, executed_signatures))

    # 轮数耗尽：已有工具结果时强制综合作答，而不是丢弃全部检索成果
    answer = ""
    if _has_tool_calls_in_history(working_messages):
        try:
            answer = _synthesize_from_tool_results(question, working_messages, nudge=EXHAUSTED_NUDGE)
        except Exception:
            answer = ""
    if not answer:
        answer = "已达单次提问的检索轮数上限。请缩小时间、人物或关键词范围后重试。"
    chat_history.extend([HumanMessage(content=question), AIMessage(content=answer)])
    trim_history(chat_history)
    return answer
