from __future__ import annotations

import json

from .. import retrieval, store
from ..console import setup_utf8_console


def main() -> None:
    setup_utf8_console()

    r1 = store.search_messages({"query": "晚安", "limit": 3})
    first = r1["messages"][0] if r1["messages"] else {}
    print("【search 短词(LIKE回退)】total:", r1["total_count"], "| 首条:", first.get("time"), first.get("sender"), "->", first.get("content"))

    r2 = store.search_messages({"query": "老婆老婆我看看", "limit": 3})
    first = r2["messages"][0] if r2["messages"] else {}
    print("【search 长词(FTS)】total:", r2["total_count"], "| 首条:", first.get("content"))

    if first:
        ctx = store.get_context({"message_id": first["message_id"], "before": 2, "after": 2})
        print("【get_context】quoted:", json.dumps(ctx.get("quoted_message"), ensure_ascii=False))
        for message in ctx.get("messages", []):
            mark = ">>" if message.get("is_center") else "  "
            print("   ", mark, message["time"], message["sender"], ":", message["content"][:30])

    r3 = store.browse({"after": "2026-06-12", "before": "2026-06-12", "limit": 3})
    print("【browse_by_time】total:", r3["total_count"])

    stats = store.stats()
    print(
        "【get_stats】",
        json.dumps(
            {
                "total": stats["total_messages"],
                "span": stats["time_span"],
                "senders": stats["senders"],
                "chunks": stats["indexed_session_chunks"],
            },
            ensure_ascii=False,
        ),
    )

    sem = retrieval.semantic_search({"query": "房顶", "limit": 2})
    note = (sem.get("note") or "")[:30]
    print("【semantic_search】note:", f"{note}..." if note else "", "| 命中", len(sem["sessions"]), "块")
    if sem["sessions"]:
        hit = sem["sessions"][0]
        print("   块片段:", hit["time_range"], "|", " / ".join(hit["snippet"].splitlines()[:3]))


if __name__ == "__main__":
    main()
