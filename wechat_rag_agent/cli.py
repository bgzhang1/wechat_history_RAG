from __future__ import annotations

from pathlib import Path

from langchain_core.messages import BaseMessage

from . import store
from .agent import run_agent
from .console import setup_utf8_console
from .llm import chat_configured


def main() -> None:
    setup_utf8_console()

    if not chat_configured():
        raise SystemExit("未配置主模型。请复制 .env.example 为 .env 并填入 CHAT_BASE_URL / CHAT_API_KEY / CHAT_MODEL。")

    if not Path(store.DB_PATH).exists():
        raise SystemExit("找不到 chat.db。请先运行: python -m wechat_rag_agent.ingest <微信导出JSON文件或目录>")

    store.db()
    stats = store.stats()
    earliest = (stats["time_span"]["earliest"] or "")[:10]
    latest = (stats["time_span"]["latest"] or "")[:10]
    print("微信聊天记录检索 Agent / Python + LangChain（输入 exit 退出）")
    print(f"已索引 {stats['total_messages']} 条消息 / {stats['indexed_session_chunks']} 个会话块，时间跨度 {earliest} ~ {latest}\n")

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
            print(f"\n[错误] {exc}")


if __name__ == "__main__":
    main()
