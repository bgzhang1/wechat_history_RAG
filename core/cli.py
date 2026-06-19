from __future__ import annotations

from pathlib import Path

from langchain_core.messages import BaseMessage

from . import store
from .agent import run_agent
from .console import setup_utf8_console
from .llm import chat_configured
from .redaction import public_exception_message


def _format_cli_error(exc: Exception) -> str:
    return public_exception_message("Agent 执行失败", exc)


def main() -> None:
    setup_utf8_console()

    if not chat_configured():
        raise SystemExit("未配置主模型。请复制 .env.example 为 .env 并填入 CHAT_BASE_URL / CHAT_API_KEY / CHAT_MODEL。")

    db_path = Path(store.DB_PATH)
    if not db_path.exists():
        raise SystemExit(f"找不到数据库 {db_path}。请先运行: python -m core.ingest local/data")

    store.db()
    stats = store.stats_summary()
    if int(stats.get("total_messages") or 0) == 0:
        raise SystemExit(f"数据库 {db_path} 中暂无已导入消息。请先运行: python -m core.ingest local/data")

    earliest = (stats["time_span"]["earliest"] or "")[:10]
    latest = (stats["time_span"]["latest"] or "")[:10]
    print("微信聊天记录检索 Agent / Python + LangChain（输入 exit 退出）")
    print(f"已索引 {stats['total_messages']} 条消息 / {stats['indexed_session_chunks']} 个会话块，时间跨度 {earliest} ~ {latest}\n")
    if int(stats.get("indexed_session_chunks") or 0) == 0:
        print(
            "提示：当前没有会话块索引，语义检索效果会受限。"
            "可运行 python -m core.ingest local/data --skip-import --force-chunks 补建会话块，"
            "或在设置页的数据导入面板执行仅分块构建。\n"
        )

    history: list[BaseMessage] = []
    while True:
        try:
            question = input("\n你: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not question or question.lower() in {"exit", "quit", "q"}:
            break

        print("\n助手: ", end="", flush=True)
        try:
            answer = run_agent(question, history)
            print(answer)
        except Exception as exc:
            print(f"\n[错误] {_format_cli_error(exc)}")


if __name__ == "__main__":
    main()
