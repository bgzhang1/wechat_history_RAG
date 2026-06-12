from __future__ import annotations

import json
from typing import Any

from . import store
from .llm import embed, embed_configured


SNIPPET_CHARS = 600


def _to_hit(row: dict[str, Any]) -> dict[str, Any]:
    ids = json.loads(row["msg_ids"])
    sample = ids if len(ids) <= 6 else [*ids[:2], ids[len(ids) // 2], *ids[-2:]]
    text = row["text"]
    return {
        "session_id": row["session_id"],
        "thread": row["thread"],
        "time_range": f"{row['start_time'].replace('T', ' ')} ~ {row['end_time'].replace('T', ' ')}",
        "participants": json.loads(row["participants"]),
        "summary": row["summary"],
        "snippet": text if len(text) <= SNIPPET_CHARS else text[:SNIPPET_CHARS] + "...",
        "message_ids_sample": sample,
    }


def _apply_filters(rows: list[dict[str, Any]], filters: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for row in rows:
        if filters.get("thread") and filters["thread"] not in row["thread"]:
            continue
        if filters.get("after") and row["end_time"] < filters["after"]:
            continue
        before = filters.get("before")
        if before and row["start_time"] > f"{before[:10]}T23:59:59" and row["start_time"] > before:
            continue
        result.append(row)
    return result


def semantic_search(args: dict[str, Any]) -> dict[str, Any]:
    top_n = min(int(args.get("limit") or 8), 20)
    vec_ready = store.has_vec() and embed_configured() and len(store.get_all_session_ids_without_embedding()) == 0

    fts_hits = store.fts_search_sessions(args["query"], 20)
    vec_hits: list[dict[str, int]] = []
    note: str | None = None

    if vec_ready:
        try:
            query_vec = embed([args["query"]])[0]
            vec_hits = store.vector_search_sessions(query_vec, 20)
        except Exception as exc:
            note = f"向量检索失败（{exc}），本次结果仅来自全文检索"
    else:
        note = "向量索引不可用（未配置 EMBED_* 或索引未建），本次结果仅来自全文检索，模糊语义召回可能偏弱"

    k = 60
    scores: dict[int, float] = {}
    for rank, hit in enumerate(fts_hits, start=1):
        session_id = hit["sessionId"]
        scores[session_id] = scores.get(session_id, 0.0) + 1 / (k + rank)
    for rank, hit in enumerate(vec_hits, start=1):
        session_id = hit["sessionId"]
        scores[session_id] = scores.get(session_id, 0.0) + 1 / (k + rank)

    ranked_ids = [session_id for session_id, _score in sorted(scores.items(), key=lambda item: item[1], reverse=True)]
    rows = store.get_sessions(ranked_ids)
    by_id = {row["session_id"]: row for row in rows}
    ordered = [by_id[session_id] for session_id in ranked_ids if session_id in by_id]
    filtered = _apply_filters(ordered, args)[:top_n]

    return {"note": note, "sessions": [_to_hit(row) for row in filtered]}
