from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from . import store
from .llm import embed, embed_configured


SNIPPET_CHARS = 600
MAX_SEMANTIC_QUERY_CHARS = 2000
DEFAULT_CANDIDATE_LIMIT = 20
FILTERED_CANDIDATE_LIMIT = 500


def _append_note(note: str | None, extra: str) -> str:
    return f"{note}；{extra}" if note else extra


def _safe_int(value: Any, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if value is None or value == "":
        result = default
    else:
        try:
            result = int(value)
        except (TypeError, ValueError, OverflowError):
            result = default
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _norm_time(value: Any, is_before: bool) -> str:
    value = str(value or "").strip().replace(" ", "T")
    if value.endswith(("Z", "z")):
        value = value[:-1]
    elif len(value) >= 6 and value[-6] in {"+", "-"} and value[-3] == ":":
        value = value[:-6]
    if len(value) == 10:
        try:
            datetime.fromisoformat(value)
        except ValueError:
            return ""
        return f"{value}T23:59:59" if is_before else f"{value}T00:00:00"
    try:
        return datetime.fromisoformat(value).isoformat(timespec="seconds")
    except ValueError:
        return ""


def _safe_json_list(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    try:
        parsed = json.loads(str(value or "[]"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return []
    return parsed if isinstance(parsed, list) else []


def _clean_thread_filter(value: Any) -> str:
    return " ".join(str(value or "").split())[: store.MAX_FILTER_CHARS]


def _to_hit(row: dict[str, Any]) -> dict[str, Any]:
    ids = _safe_json_list(row.get("msg_ids"))
    sample = ids if len(ids) <= 6 else [*ids[:2], ids[len(ids) // 2], *ids[-2:]]
    text = row["text"]
    summary = str(row.get("summary") or "").strip() or None
    return {
        "session_id": row["session_id"],
        "thread": row["thread"],
        "time_range": f"{row['start_time'].replace('T', ' ')} ~ {row['end_time'].replace('T', ' ')}",
        "participants": _safe_json_list(row.get("participants")),
        "summary": summary,
        "snippet": text if len(text) <= SNIPPET_CHARS else text[:SNIPPET_CHARS] + "...",
        "message_ids_sample": sample,
    }


def _apply_filters(rows: list[dict[str, Any]], filters: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    after = _norm_time(filters["after"], False) if filters.get("after") else None
    before = _norm_time(filters["before"], True) if filters.get("before") else None
    thread = _clean_thread_filter(filters.get("thread")).casefold()
    for row in rows:
        if thread and thread not in str(row.get("thread") or "").casefold():
            continue
        if after and row["end_time"] < after:
            continue
        if before and row["start_time"] > before:
            continue
        result.append(row)
    return result


def _has_effective_filters(args: dict[str, Any]) -> bool:
    thread = _clean_thread_filter(args.get("thread"))
    after = _norm_time(args["after"], False) if args.get("after") else ""
    before = _norm_time(args["before"], True) if args.get("before") else ""
    return bool(thread or after or before)


def semantic_search(args: dict[str, Any]) -> dict[str, Any]:
    raw_query = str(args.get("query") or "").strip()
    query_truncated = len(raw_query) > MAX_SEMANTIC_QUERY_CHARS
    query = raw_query[:MAX_SEMANTIC_QUERY_CHARS].strip() if query_truncated else raw_query
    if not query:
        return {"note": "query is empty; provide a natural-language description", "sessions": []}

    top_n = _safe_int(args.get("limit"), 8, minimum=1, maximum=20)
    has_filters = _has_effective_filters(args)
    candidate_limit = FILTERED_CANDIDATE_LIMIT if has_filters else DEFAULT_CANDIDATE_LIMIT
    vector_enabled = store.has_vec() and embed_configured()
    indexed_vector_ids = store.get_session_ids_with_embedding() if vector_enabled else set()
    missing_vector_count = store.count_sessions_without_embedding() if vector_enabled else 0
    vec_ready = bool(indexed_vector_ids)

    fts_hits = store.fts_search_sessions(query, candidate_limit, filters=args)
    vec_hits: list[dict[str, int]] = []
    note: str | None = None

    if vec_ready:
        try:
            query_vec = embed([query])[0]
            vec_hits = store.vector_search_sessions(query_vec, candidate_limit, filters=args)
            if missing_vector_count:
                note = f"向量索引不完整（缺少 {missing_vector_count} 个会话块），本次语义结果已结合现有向量和全文检索；重跑 ingest 可自动补齐"
        except Exception as exc:
            note = f"向量检索失败（{type(exc).__name__}），本次结果仅来自全文检索"
    else:
        note = "向量索引不可用（未配置 EMBED_* 或索引未建），本次结果仅来自全文检索，模糊语义召回可能偏弱"

    if query_truncated:
        note = _append_note(note, f"query too long; only the first {MAX_SEMANTIC_QUERY_CHARS} characters were searched")

    k = 60
    scores: dict[int, float] = {}
    for rank, hit in enumerate(fts_hits, start=1):
        session_id = hit["sessionId"]
        scores[session_id] = scores.get(session_id, 0.0) + 1 / (k + rank)
    for rank, hit in enumerate(vec_hits, start=1):
        session_id = hit["sessionId"]
        scores[session_id] = scores.get(session_id, 0.0) + 1 / (k + rank)

    rows = store.get_sessions(list(scores))
    by_id = {row["session_id"]: row for row in rows}
    ranked_ids = sorted(
        scores,
        key=lambda session_id: (
            scores[session_id],
            str(by_id.get(session_id, {}).get("end_time") or ""),
            session_id,
        ),
        reverse=True,
    )
    ordered = [by_id[session_id] for session_id in ranked_ids if session_id in by_id]
    filtered = _apply_filters(ordered, args)[:top_n]

    if not filtered and has_filters:
        filtered = store.get_recent_sessions(top_n, filters=args)
        if filtered:
            note = _append_note(note, "未找到语义/全文命中；已返回符合条件的最近会话块作为上下文兜底")
        else:
            note = _append_note(note, "未找到语义/全文命中，且过滤条件内没有可用会话块")
    elif not filtered:
        note = _append_note(note, "未找到语义/全文命中；未返回最近会话兜底，避免把无关聊天误当作答案依据")

    return {"note": note, "sessions": [_to_hit(row) for row in filtered]}
