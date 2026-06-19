from __future__ import annotations

import json
import re

from .. import retrieval, store
from ..console import setup_utf8_console


def _first_message() -> dict | None:
    row = store.db().execute(
        """
        SELECT id, sender, timestamp, content, thread
        FROM messages
        WHERE TRIM(content) != ''
        ORDER BY timestamp, id
        LIMIT 1
        """
    ).fetchone()
    return dict(row) if row else None


def _query_terms(content: str) -> tuple[str, str]:
    normalized = re.sub(r"\s+", " ", content).strip()
    compact = normalized.replace(" ", "")
    if not compact:
        return "", ""
    short = compact[: min(2, len(compact))]
    long = normalized[: min(24, len(normalized))] if len(compact) >= 3 else ""
    return short, long


def _date_part(timestamp: str) -> str:
    return str(timestamp or "")[:10]


def main() -> int:
    setup_utf8_console()

    ok = True
    summary = store.stats_summary()
    total_messages = int(summary.get("total_messages") or 0)
    if total_messages == 0:
        print("暂无已导入消息。请先运行 python -m core.ingest local/data 后再执行 smoke。")
        return 1

    sample = _first_message()
    if not sample:
        print("数据库存在消息计数，但没有可抽样的文本内容。请检查导入结果。")
        return 1

    short_query, long_query = _query_terms(sample["content"])
    if short_query:
        r1 = store.search_messages({"query": short_query, "limit": 3})
        first = r1["messages"][0] if r1["messages"] else {}
        print(
            "【search 短词(LIKE回退)】total:",
            r1["total_count"],
            "| 首条:",
            first.get("time"),
            first.get("sender"),
            "->",
            first.get("content"),
        )
        if not r1["messages"]:
            ok = False
            print("x 短词检索没有命中样本消息，请检查 messages 表或 LIKE 回退查询。")

    if long_query:
        r2 = store.search_messages({"query": long_query, "limit": 3})
        first = r2["messages"][0] if r2["messages"] else {}
        print("【search 长词(FTS/LIKE)】total:", r2["total_count"], "| 首条:", first.get("content"))
        if not r2["messages"]:
            ok = False
            print("x 长词检索没有命中样本消息，请检查 FTS 索引是否已构建。")
    else:
        first = {}

    context_id = first.get("message_id") or sample["id"]
    ctx = store.get_context({"message_id": context_id, "before": 2, "after": 2})
    print("【get_context】quoted:", json.dumps(ctx.get("quoted_message"), ensure_ascii=False))
    if ctx.get("error") or not ctx.get("messages"):
        ok = False
        print("x 上下文追溯失败，请检查消息 seq 是否已重算。")
    for message in ctx.get("messages", []):
        mark = ">>" if message.get("is_center") else "  "
        print("   ", mark, message["time"], message["sender"], ":", message["content"][:30])

    sample_day = _date_part(sample["timestamp"])
    r3 = store.browse({"after": sample_day, "before": sample_day, "limit": 3})
    print("【browse_by_time】date:", sample_day, "| total:", r3["total_count"])
    if not r3["messages"]:
        ok = False
        print("x 时间浏览没有命中样本日期，请检查 timestamp 归一化和时间索引。")

    stats = store.stats()
    print(
        "【get_stats】",
        json.dumps(
            {
                "total": stats["total_messages"],
                "span": stats["time_span"],
                "senders_page": stats["senders_page"],
                "chunks": stats["indexed_session_chunks"],
            },
            ensure_ascii=False,
        ),
    )

    sem = retrieval.semantic_search({"query": sample["content"][:80], "limit": 2})
    note = (sem.get("note") or "")[:60]
    print("【semantic_search】note:", f"{note}..." if note else "", "| 命中", len(sem["sessions"]), "块")
    if sem["sessions"]:
        hit = sem["sessions"][0]
        print("   块片段:", hit["time_range"], "|", " / ".join(hit["snippet"].splitlines()[:3]))
    else:
        ok = False
        print("x 语义/会话块检索没有命中，请检查分块、FTS 和可选向量索引。")

    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
