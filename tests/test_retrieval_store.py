from __future__ import annotations

import json
import sqlite3
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from core import retrieval, store
from core.parser import PARSER_VERSION, NormMessage, parse_weflow


class RetrievalStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self._old_db_path = store.DB_PATH
        store.close_current_connection()
        store.DB_PATH = str(Path(self._tmp.name) / "chat.db")
        self._seed_data()

    def tearDown(self) -> None:
        store.close_current_connection()
        store.DB_PATH = self._old_db_path
        self._tmp.cleanup()

    def test_empty_keyword_search_returns_no_database_dump(self) -> None:
        result = store.search_messages({"query": "   ", "limit": 20})

        self.assertEqual(result["total_count"], 0)
        self.assertEqual(result["messages"], [])
        self.assertIn("query is empty", result["note"])

    def test_direct_search_callers_tolerate_missing_query(self) -> None:
        result = store.search_messages({"limit": 20})

        self.assertEqual(result["total_count"], 0)
        self.assertEqual(result["messages"], [])
        self.assertIn("query is empty", result["note"])

    def test_sqlite_uri_detection_matches_file_uri_paths_only(self) -> None:
        self.assertTrue(store._sqlite_uri("file:chat-test?mode=memory&cache=shared"))
        self.assertFalse(store._sqlite_uri(":memory:"))
        self.assertFalse(store._sqlite_uri(str(Path(self._tmp.name) / "chat.db")))

    def test_short_keyword_search_uses_like_and_filters(self) -> None:
        result = store.search_messages(
            {
                "query": "AI",
                "thread": "  项目群  ",
                "after": "2024-01-01T10:01:00.000Z",
                "before": "2024-01-01T10:01:00.999+08:00",
                "limit": 10,
            }
        )

        self.assertEqual(result["total_count"], 1)
        self.assertEqual(result["returned"], 1)
        self.assertEqual(result["messages"][0]["message_id"], "m2")
        self.assertEqual(result["messages"][0]["thread"], "项目群")

    def test_keyword_search_caps_excessive_terms_for_direct_store_callers(self) -> None:
        result = store.search_messages({"query": " ".join(["AI"] * (store.MAX_QUERY_TERMS + 5)), "limit": 10})

        self.assertGreater(result["total_count"], 0)
        self.assertIn("query too long", result["note"])
        self.assertIn(str(store.MAX_QUERY_TERMS), result["note"])

    def test_keyword_search_preview_keeps_match_visible_inside_long_messages(self) -> None:
        long_content = "开头是无关背景 " + "x" * (store.API_MESSAGE_PREVIEW_CHARS + 40) + " needlekeyword 真实命中"
        store.upsert_messages(
            [
                NormMessage(
                    id="m-long-search",
                    sender="Alice",
                    is_self=0,
                    timestamp="2024-01-01T10:03:00",
                    content=long_content,
                    msg_type="文本消息",
                    thread="项目群",
                    reply_to=None,
                )
            ]
        )
        store.rebuild_fts()

        result = store.search_messages({"query": "needlekeyword", "limit": 1})

        self.assertEqual(result["messages"][0]["message_id"], "m-long-search")
        self.assertIn("needlekeyword", result["messages"][0]["content"])
        self.assertTrue(result["messages"][0]["content"].startswith("..."))
        self.assertTrue(result["messages"][0]["content_truncated"])
        self.assertEqual(result["messages"][0]["content_original_length"], len(long_content))

    def test_direct_store_callers_tolerate_invalid_numeric_windows(self) -> None:
        search = store.search_messages({"query": "AI", "after": "not-a-date", "limit": "not-int", "offset": "bad"})
        browse = store.browse(
            {
                "after": "not-a-date",
                "before": "2024-01-02",
                "limit": object(),
                "offset": object(),
            }
        )
        context = store.get_context({"message_id": "m2", "before": "bad", "after": "bad"})
        threads = store.stats_threads(limit="bad", offset="bad")
        senders = store.stats_senders(limit="bad", offset="bad")
        suggestions = store.suggest_entities(query=None, limit="bad")

        self.assertGreater(search["returned"], 0)
        self.assertEqual(search["offset"], 0)
        self.assertGreater(browse["returned"], 0)
        self.assertEqual(browse["offset"], 0)
        self.assertEqual(context["center_message_id"], "m2")
        self.assertGreaterEqual(len(context["messages"]), 1)
        self.assertGreaterEqual(threads["returned"], 1)
        self.assertEqual(threads["offset"], 0)
        self.assertGreaterEqual(senders["returned"], 1)
        self.assertEqual(senders["offset"], 0)
        self.assertEqual(suggestions["query"], "")
        self.assertGreaterEqual(len(suggestions["items"]), 1)

    def test_search_and_browse_use_stable_tie_breakers_for_same_second_messages(self) -> None:
        search = store.search_messages({"query": "AI", "thread": "项目群", "limit": 10})
        browse = store.browse(
            {
                "after": "2024-01-01T10:02:00",
                "before": "2024-01-01T10:02:00",
                "thread": "项目群",
                "limit": 10,
            }
        )

        self.assertEqual([message["message_id"] for message in search["messages"][:2]], ["m4", "m3"])
        self.assertEqual([message["message_id"] for message in browse["messages"]], ["m3", "m4"])

    def test_unfiltered_session_fts_batches_large_message_candidate_set(self) -> None:
        total = store.SQL_BATCH + 5
        messages = [
            NormMessage(
                id=f"bulk-{index}",
                sender="Alice",
                is_self=0,
                timestamp=f"2024-01-02T00:{index // 60:02d}:{index % 60:02d}",
                content="bulkneedle scoped text",
                msg_type="文本消息",
                thread=f"批量群{index}",
                reply_to=None,
            )
            for index in range(total)
        ]
        store.upsert_messages(messages)
        store.rebuild_fts()
        store.replace_sessions(
            [
                {
                    "thread": message.thread,
                    "start_time": message.timestamp,
                    "end_time": message.timestamp,
                    "participants": json.dumps(["Alice"], ensure_ascii=False),
                    "msg_ids": json.dumps([message.id], ensure_ascii=False),
                    "text": f"Alice: {message.content}",
                    "summary": None,
                }
                for message in messages
            ]
        )

        hits = store.fts_search_sessions("bulkneedle", 50)
        first_session = store.get_sessions([hits[0]["sessionId"]])[0]

        self.assertEqual(len(hits), 50)
        self.assertEqual(first_session["thread"], f"批量群{total - 1}")

    def test_get_session_ids_with_embedding_can_be_scoped_and_batched(self) -> None:
        conn = store.db()
        conn.execute(f"DROP TABLE IF EXISTS {store.VECTOR_TABLE}")
        conn.execute(
            f"CREATE TABLE {store.VECTOR_TABLE} (session_id INTEGER PRIMARY KEY, embedding BLOB)"
        )
        conn.executemany(
            f"INSERT OR REPLACE INTO {store.VECTOR_TABLE} (session_id, embedding) VALUES (?, ?)",
            [(1, b"vec-1"), (3, b"vec-3"), (5, b"vec-5")],
        )
        conn.commit()

        with (
            patch("core.store.has_vec", return_value=True),
            patch("core.store.SQL_BATCH", 2),
        ):
            scoped = store.get_session_ids_with_embedding([5, 2, 3, 1, 3])

        self.assertEqual(scoped, {1, 3, 5})

    def test_vector_search_applies_session_filters_inside_vec_query(self) -> None:
        if not store.has_vec():
            self.skipTest("sqlite-vec is not available")

        store.reset_vector_table(2)
        session_ids = store.replace_sessions(
            [
                {
                    "thread": "目标群",
                    "start_time": "2024-01-01T10:00:00",
                    "end_time": "2024-01-01T10:01:00",
                    "participants": json.dumps(["Alice"], ensure_ascii=False),
                    "msg_ids": json.dumps([], ensure_ascii=False),
                    "text": "far but in requested thread",
                    "summary": None,
                },
                {
                    "thread": "其它群",
                    "start_time": "2024-01-01T10:00:00",
                    "end_time": "2024-01-01T10:01:00",
                    "participants": json.dumps(["Bob"], ensure_ascii=False),
                    "msg_ids": json.dumps([], ensure_ascii=False),
                    "text": "nearest globally",
                    "summary": None,
                },
                {
                    "thread": "其它群",
                    "start_time": "2024-01-01T10:00:00",
                    "end_time": "2024-01-01T10:01:00",
                    "participants": json.dumps(["Carol"], ensure_ascii=False),
                    "msg_ids": json.dumps([], ensure_ascii=False),
                    "text": "second nearest globally",
                    "summary": None,
                },
            ]
        )
        store.insert_embeddings(
            [
                {"session_id": session_ids[0], "embedding": [100.0, 100.0]},
                {"session_id": session_ids[1], "embedding": [1.0, 1.0]},
                {"session_id": session_ids[2], "embedding": [2.0, 2.0]},
            ]
        )

        unfiltered = store.vector_search_sessions([0.0, 0.0], 1)
        filtered = store.vector_search_sessions([0.0, 0.0], 1, filters={"thread": "目标群"})

        self.assertEqual(unfiltered, [{"sessionId": session_ids[1]}])
        self.assertEqual(filtered, [{"sessionId": session_ids[0]}])

    def test_fts_search_treats_user_punctuation_as_literal_text(self) -> None:
        store.upsert_messages(
            [
                NormMessage(
                    id="m-special",
                    sender="Alice",
                    is_self=0,
                    timestamp="2024-01-01T10:03:00",
                    content='alpha "quoted" tag:todo (launch) C++ foo-bar',
                    msg_type="文本消息",
                    thread="项目群",
                    reply_to=None,
                )
            ]
        )
        store.rebuild_fts()
        store.replace_sessions(
            [
                {
                    "thread": "项目群",
                    "start_time": "2024-01-01T10:03:00",
                    "end_time": "2024-01-01T10:03:00",
                    "participants": json.dumps(["Alice"], ensure_ascii=False),
                    "msg_ids": json.dumps(["m-special"], ensure_ascii=False),
                    "text": 'Alice: alpha "quoted" tag:todo (launch) C++ foo-bar',
                    "summary": "特殊符号搜索",
                }
            ]
        )

        search = store.search_messages({"query": 'tag:todo (launch) "quoted"', "limit": 10})
        sessions = store.fts_search_sessions('tag:todo (launch) "quoted"', 10)

        self.assertEqual(search["total_count"], 1)
        self.assertEqual(search["messages"][0]["message_id"], "m-special")
        self.assertEqual(sessions, [{"sessionId": 1}])

    def test_sync_missing_fts_for_ingest_targets_only_indexes_target_messages(self) -> None:
        store.upsert_messages(
            [
                NormMessage(
                    id="scope-a:1",
                    sender="Alice",
                    is_self=0,
                    timestamp="2024-01-01T10:03:00",
                    content="targetalpha scoped text",
                    msg_type="文本消息",
                    thread="项目群",
                    reply_to=None,
                ),
                NormMessage(
                    id="scope-b:1",
                    sender="Bob",
                    is_self=0,
                    timestamp="2024-01-01T10:04:00",
                    content="otherbeta scoped text",
                    msg_type="文本消息",
                    thread="项目群",
                    reply_to=None,
                ),
            ]
        )
        store.rebuild_fts()
        conn = store.db()
        rowids = [
            int(row["rowid"])
            for row in conn.execute("SELECT rowid FROM messages WHERE id IN ('scope-a:1', 'scope-b:1')").fetchall()
        ]
        with conn:
            for rowid in rowids:
                conn.execute("DELETE FROM messages_fts WHERE rowid = ?", (rowid,))

        self.assertEqual(store.count_missing_fts_for_ingest_targets([], ["scope-a:"]), 1)
        synced = store.sync_missing_fts_for_ingest_targets([], ["scope-a:"])

        self.assertEqual(synced, 1)
        self.assertEqual(store.search_messages({"query": "targetalpha", "limit": 10})["total_count"], 1)
        self.assertEqual(store.search_messages({"query": "otherbeta", "limit": 10})["total_count"], 0)

    def test_count_missing_seq_for_ingest_targets_only_counts_target_messages(self) -> None:
        store.upsert_messages(
            [
                NormMessage(
                    id="seq-scope-a:1",
                    sender="Alice",
                    is_self=0,
                    timestamp="2024-01-01T10:03:00",
                    content="target seq",
                    msg_type="文本消息",
                    thread="项目群",
                    reply_to=None,
                ),
                NormMessage(
                    id="seq-scope-b:1",
                    sender="Bob",
                    is_self=0,
                    timestamp="2024-01-01T10:04:00",
                    content="other seq",
                    msg_type="文本消息",
                    thread="其它群",
                    reply_to=None,
                ),
            ]
        )

        self.assertGreaterEqual(store.count_messages_missing_seq(), 2)
        self.assertEqual(store.count_messages_missing_seq_for_ingest_targets([], ["seq-scope-a:"]), 1)
        self.assertEqual(store.count_messages_missing_seq_for_ingest_targets([], ["missing-scope:"]), 0)

    def test_rebuild_fts_for_ingest_targets_accepts_file_message_mapping(self) -> None:
        mapped_path = str(Path(self._tmp.name) / "mapped.json")
        store.upsert_messages(
            [
                NormMessage(
                    id="mapped-message",
                    sender="Alice",
                    is_self=0,
                    timestamp="2024-01-01T10:03:00",
                    content="mappedalpha scoped text",
                    msg_type="文本消息",
                    thread="项目群",
                    reply_to=None,
                )
            ]
        )
        store.record_ingest_file_messages(mapped_path, ["mapped-message"])
        store.rebuild_fts()
        rowid = int(store.db().execute("SELECT rowid FROM messages WHERE id = 'mapped-message'").fetchone()["rowid"])
        with store.db():
            store.db().execute("DELETE FROM messages_fts WHERE rowid = ?", (rowid,))

        rebuilt = store.rebuild_fts_for_ingest_targets([mapped_path], [])

        self.assertEqual(rebuilt, 1)
        self.assertEqual(store.search_messages({"query": "mappedalpha", "limit": 10})["total_count"], 1)

    def test_get_context_respects_zero_before_after(self) -> None:
        result = store.get_context({"message_id": "m2", "before": 0, "after": 0})

        self.assertEqual([message["message_id"] for message in result["messages"]], ["m2"])
        self.assertTrue(result["messages"][0]["is_center"])

    def test_get_context_preserves_long_scoped_message_id_without_filter_truncation(self) -> None:
        long_message_id = f"{'nested account path/' * 14}chat file.json:platform id  with spaces"
        self.assertGreater(len(long_message_id), 240)
        self.assertLessEqual(len(long_message_id), store.MAX_MESSAGE_ID_CHARS)
        store.upsert_messages(
            [
                NormMessage(
                    id=long_message_id,
                    sender="Alice",
                    is_self=0,
                    timestamp="2024-01-01T10:03:00",
                    content="Long scoped id context",
                    msg_type="文本消息",
                    thread="项目群",
                    reply_to=None,
                )
            ]
        )
        store.recompute_message_sequence(["项目群"])

        result = store.get_context({"message_id": f"  {long_message_id}  ", "before": 0, "after": 0})

        self.assertEqual(result["center_message_id"], long_message_id)
        self.assertEqual([message["message_id"] for message in result["messages"]], [long_message_id])

    def test_direct_context_callers_get_structured_error_for_missing_message_id(self) -> None:
        result = store.get_context({})

        self.assertEqual(result, {"error": "message_id is required"})

    def test_semantic_search_without_vectors_does_not_return_unfiltered_recent_sessions(self) -> None:
        with (
            patch("core.retrieval.embed_configured", return_value=False),
            patch("core.retrieval.store.has_vec", return_value=False),
        ):
            result = retrieval.semantic_search({"query": "完全不存在的词", "limit": 1})

        self.assertEqual(result["sessions"], [])
        self.assertIn("向量索引不可用", result["note"])
        self.assertIn("未返回最近会话兜底", result["note"])

    def test_direct_semantic_callers_tolerate_missing_query(self) -> None:
        result = retrieval.semantic_search({"limit": 1})

        self.assertEqual(result["sessions"], [])
        self.assertIn("query is empty", result["note"])

    def test_semantic_search_tolerates_invalid_limit_and_filter_types(self) -> None:
        with (
            patch("core.retrieval.embed_configured", return_value=False),
            patch("core.retrieval.store.has_vec", return_value=False),
        ):
            result = retrieval.semantic_search(
                {
                    "query": "预算",
                    "thread": " 项目群 ",
                    "after": object(),
                    "before": object(),
                    "limit": object(),
                }
            )

        self.assertEqual(len(result["sessions"]), 1)
        self.assertEqual(result["sessions"][0]["thread"], "项目群")

    def test_direct_semantic_callers_do_not_embed_unbounded_queries(self) -> None:
        long_query = "预算" * (retrieval.MAX_SEMANTIC_QUERY_CHARS + 10)
        with (
            patch("core.retrieval.embed_configured", return_value=True),
            patch("core.retrieval.store.has_vec", return_value=True),
            patch("core.retrieval.store.get_session_ids_with_embedding", return_value={1}),
            patch("core.retrieval.store.count_sessions_without_embedding", return_value=0),
            patch("core.retrieval.embed", return_value=[[0.1, 0.2]]) as embed,
            patch("core.retrieval.store.vector_search_sessions", return_value=[{"sessionId": 1}]),
        ):
            result = retrieval.semantic_search({"query": long_query, "limit": 1})

        embedded_query = embed.call_args.args[0][0]
        self.assertEqual(len(embedded_query), retrieval.MAX_SEMANTIC_QUERY_CHARS)
        self.assertIn("query too long", result["note"])
        self.assertEqual(len(result["sessions"]), 1)

    def test_semantic_search_ignores_blank_or_invalid_filters_for_recent_fallback(self) -> None:
        with (
            patch("core.retrieval.embed_configured", return_value=False),
            patch("core.retrieval.store.has_vec", return_value=False),
        ):
            result = retrieval.semantic_search(
                {
                    "query": "完全不存在的词",
                    "thread": "   ",
                    "after": object(),
                    "before": "not-a-date",
                    "limit": 1,
                }
            )

        self.assertEqual(result["sessions"], [])
        self.assertIn("未返回最近会话兜底", result["note"])

    def test_semantic_search_uses_default_candidate_pool_for_blank_or_invalid_filters(self) -> None:
        with (
            patch("core.retrieval.embed_configured", return_value=False),
            patch("core.retrieval.store.has_vec", return_value=False),
            patch("core.retrieval.store.fts_search_sessions", return_value=[]) as fts_search,
        ):
            retrieval.semantic_search({"query": "预算", "thread": "   ", "before": "not-a-date", "limit": 1})

        self.assertEqual(fts_search.call_args.args[1], retrieval.DEFAULT_CANDIDATE_LIMIT)

    def test_get_context_returns_longer_content_than_preview_apis(self) -> None:
        long_content = "context-anchor " + "x" * (store.API_MESSAGE_PREVIEW_CHARS + 20)
        store.upsert_messages(
            [
                NormMessage(
                    id="m-long",
                    sender="Alice",
                    is_self=0,
                    timestamp="2024-01-01T10:03:00",
                    content=long_content,
                    msg_type="文本消息",
                    thread="项目群",
                    reply_to=None,
                )
            ]
        )
        store.recompute_message_sequence(["项目群"])

        preview = store.browse(
            {
                "after": "2024-01-01T10:03:00",
                "before": "2024-01-01T10:03:00",
                "thread": "项目群",
                "limit": 1,
            }
        )
        context = store.get_context({"message_id": "m-long", "before": 0, "after": 0})

        self.assertTrue(preview["messages"][0]["content_truncated"])
        self.assertEqual(preview["messages"][0]["content_original_length"], len(long_content))
        self.assertLess(len(preview["messages"][0]["content"]), len(long_content))
        self.assertEqual(context["messages"][0]["content"], long_content)
        self.assertNotIn("content_truncated", context["messages"][0])

    def test_recent_session_fallback_trims_thread_filter(self) -> None:
        result = retrieval.semantic_search({"query": "完全不存在的词", "thread": " 项目群 ", "limit": 1})

        self.assertEqual(len(result["sessions"]), 1)
        self.assertEqual(result["sessions"][0]["thread"], "项目群")

    def test_semantic_search_trims_thread_filter_before_final_hit_filtering(self) -> None:
        with (
            patch("core.retrieval.embed_configured", return_value=False),
            patch("core.retrieval.store.has_vec", return_value=False),
            patch("core.retrieval.store.get_recent_sessions", side_effect=AssertionError("should not fallback")),
        ):
            result = retrieval.semantic_search({"query": "预算", "thread": " 项目群 ", "limit": 1})

        self.assertEqual(len(result["sessions"]), 1)
        self.assertEqual(result["sessions"][0]["thread"], "项目群")
        self.assertNotIn("最近会话块", result["note"])

    def test_semantic_search_final_thread_filter_matches_sql_case_insensitively(self) -> None:
        store.upsert_messages(
            [
                NormMessage(
                    id="english-thread-1",
                    sender="Alice",
                    is_self=0,
                    timestamp="2024-01-02T10:00:00",
                    content="alpha kickoff note",
                    msg_type="text",
                    thread="Project Chat",
                    reply_to=None,
                )
            ]
        )
        store.recompute_message_sequence(["Project Chat"])
        store.rebuild_fts()
        store.replace_sessions(
            [
                {
                    "thread": "Project Chat",
                    "start_time": "2024-01-02T10:00:00",
                    "end_time": "2024-01-02T10:00:00",
                    "participants": json.dumps(["Alice"], ensure_ascii=False),
                    "msg_ids": json.dumps(["english-thread-1"], ensure_ascii=False),
                    "text": "Alice: alpha kickoff note",
                    "summary": None,
                }
            ],
            ["Project Chat"],
        )

        with (
            patch("core.retrieval.embed_configured", return_value=False),
            patch("core.retrieval.store.has_vec", return_value=False),
            patch("core.retrieval.store.get_recent_sessions", side_effect=AssertionError("should not fallback")),
        ):
            result = retrieval.semantic_search({"query": "alpha", "thread": "project", "limit": 1})

        self.assertEqual(len(result["sessions"]), 1)
        self.assertEqual(result["sessions"][0]["thread"], "Project Chat")

    def test_semantic_search_tolerates_legacy_malformed_session_json_fields(self) -> None:
        conn = store.db()
        with conn:
            conn.execute("UPDATE sessions SET participants = ?, msg_ids = ? WHERE session_id = 1", ("not-json", "{bad"))

        with (
            patch("core.retrieval.embed_configured", return_value=False),
            patch("core.retrieval.store.has_vec", return_value=False),
        ):
            result = retrieval.semantic_search({"query": "预算", "limit": 1})

        self.assertEqual(len(result["sessions"]), 1)
        self.assertEqual(result["sessions"][0]["participants"], [])
        self.assertEqual(result["sessions"][0]["message_ids_sample"], [])

    def test_filtered_semantic_search_uses_larger_candidate_pool(self) -> None:
        with (
            patch("core.retrieval.embed_configured", return_value=False),
            patch("core.retrieval.store.has_vec", return_value=False),
            patch("core.retrieval.store.fts_search_sessions", return_value=[]) as fts_search,
        ):
            retrieval.semantic_search({"query": "预算", "thread": "项目群", "limit": 1})

        self.assertEqual(fts_search.call_args.args[1], retrieval.FILTERED_CANDIDATE_LIMIT)

    def test_filtered_semantic_search_passes_filters_to_vector_search(self) -> None:
        with (
            patch("core.retrieval.embed_configured", return_value=True),
            patch("core.retrieval.store.has_vec", return_value=True),
            patch("core.retrieval.store.get_session_ids_with_embedding", return_value={1}),
            patch("core.retrieval.store.count_sessions_without_embedding", return_value=0),
            patch("core.retrieval.embed", return_value=[[0.1, 0.2]]),
            patch("core.retrieval.store.fts_search_sessions", return_value=[]),
            patch("core.retrieval.store.vector_search_sessions", return_value=[]) as vector_search,
            patch("core.retrieval.store.get_recent_sessions", return_value=[]),
        ):
            retrieval.semantic_search(
                {
                    "query": "预算",
                    "thread": " 项目群 ",
                    "after": "2024-01-01",
                    "before": "2024-01-02",
                    "limit": 1,
                }
            )

        self.assertEqual(vector_search.call_args.args[1], retrieval.FILTERED_CANDIDATE_LIMIT)
        self.assertEqual(vector_search.call_args.kwargs["filters"]["thread"], " 项目群 ")
        self.assertEqual(vector_search.call_args.kwargs["filters"]["after"], "2024-01-01")
        self.assertEqual(vector_search.call_args.kwargs["filters"]["before"], "2024-01-02")

    def test_semantic_search_uses_recency_tie_breaker_for_fusion_ties(self) -> None:
        older_id, newer_id = store.replace_sessions(
            [
                {
                    "thread": "旧会话",
                    "start_time": "2024-01-01T10:00:00",
                    "end_time": "2024-01-01T10:00:00",
                    "participants": json.dumps(["Alice"], ensure_ascii=False),
                    "msg_ids": json.dumps([], ensure_ascii=False),
                    "text": "older tied semantic hit",
                    "summary": "older",
                },
                {
                    "thread": "新会话",
                    "start_time": "2024-01-02T10:00:00",
                    "end_time": "2024-01-02T10:00:00",
                    "participants": json.dumps(["Bob"], ensure_ascii=False),
                    "msg_ids": json.dumps([], ensure_ascii=False),
                    "text": "newer tied semantic hit",
                    "summary": "newer",
                },
            ]
        )

        with (
            patch("core.retrieval.embed_configured", return_value=True),
            patch("core.retrieval.store.has_vec", return_value=True),
            patch("core.retrieval.store.get_session_ids_with_embedding", return_value={older_id, newer_id}),
            patch("core.retrieval.store.count_sessions_without_embedding", return_value=0),
            patch("core.retrieval.embed", return_value=[[0.1, 0.2]]),
            patch(
                "core.retrieval.store.fts_search_sessions",
                return_value=[{"sessionId": older_id}, {"sessionId": newer_id}],
            ),
            patch(
                "core.retrieval.store.vector_search_sessions",
                return_value=[{"sessionId": newer_id}, {"sessionId": older_id}],
            ),
        ):
            result = retrieval.semantic_search({"query": "tie breaker", "limit": 2})

        self.assertEqual([session["thread"] for session in result["sessions"]], ["新会话", "旧会话"])

    def test_get_sessions_batches_large_candidate_lists(self) -> None:
        session_count = store.SQL_BATCH + 5
        store.replace_sessions(
            [
                {
                    "thread": f"thread-{index}",
                    "start_time": "2024-01-01T00:00:00",
                    "end_time": "2024-01-01T00:01:00",
                    "participants": json.dumps(["Alice"], ensure_ascii=False),
                    "msg_ids": json.dumps([], ensure_ascii=False),
                    "text": f"session text {index}",
                    "summary": None,
                }
                for index in range(session_count)
            ]
        )

        rows = store.get_sessions(list(range(1, session_count + 1)))

        self.assertEqual(len(rows), session_count)
        self.assertEqual({row["session_id"] for row in rows}, set(range(1, session_count + 1)))

    def test_scoped_thread_operations_batch_large_thread_lists(self) -> None:
        old_batch = store.SQL_BATCH
        store.SQL_BATCH = 2
        try:
            self.assertEqual(list(store._batched([1, 2, 3, 4, 5])), [[1, 2], [3, 4], [5]])
            messages = [
                NormMessage(
                    id=f"batch-thread-{thread_index}-{message_index}",
                    sender="Alice",
                    is_self=0,
                    timestamp=f"2024-01-07T10:{thread_index:02d}:{message_index:02d}",
                    content=f"batch scoped {thread_index} {message_index}",
                    msg_type="text",
                    thread=f"thread-{thread_index}",
                    reply_to=None,
                )
                for thread_index in range(5)
                for message_index in range(2)
            ]
            store.upsert_messages(messages)
            thread_names = [f"thread-{index}" for index in range(5)]

            store.recompute_message_sequence(thread_names)
            grouped = store.get_all_messages_by_thread([*thread_names, *thread_names])
            initial_rows = [
                {
                    "thread": thread,
                    "start_time": grouped[thread][0]["timestamp"],
                    "end_time": grouped[thread][-1]["timestamp"],
                    "participants": json.dumps(["Alice"], ensure_ascii=False),
                    "msg_ids": json.dumps([message["id"] for message in grouped[thread]], ensure_ascii=False),
                    "text": f"old chunk {thread}",
                    "summary": None,
                }
                for thread in thread_names
            ]
            initial_rows.append(
                {
                    "thread": "untouched-thread",
                    "start_time": "2024-01-07T11:00:00",
                    "end_time": "2024-01-07T11:00:00",
                    "participants": json.dumps(["Alice"], ensure_ascii=False),
                    "msg_ids": json.dumps([], ensure_ascii=False),
                    "text": "old untouched chunk",
                    "summary": None,
                }
            )
            store.replace_sessions(initial_rows)

            replacement_rows = [
                {
                    **row,
                    "text": f"new chunk {row['thread']}",
                    "summary": "new summary",
                }
                for row in initial_rows
                if row["thread"] != "untouched-thread"
            ]
            new_ids = store.replace_sessions(replacement_rows, [*thread_names, *thread_names])
            sessions = store.get_all_sessions()

            self.assertEqual(len(new_ids), 5)
            self.assertEqual([message["seq"] for message in grouped["thread-0"]], [1, 2])
            self.assertEqual(sum(1 for session in sessions if session["thread"] in thread_names), 5)
            self.assertTrue(all(session["text"].startswith("new chunk") for session in sessions if session["thread"] in thread_names))
            self.assertEqual([session["text"] for session in sessions if session["thread"] == "untouched-thread"], ["old untouched chunk"])
            self.assertEqual(
                store.get_session_ids_for_message_id_prefixes(["batch-thread-0-"]),
                [new_ids[0]],
            )
        finally:
            store.SQL_BATCH = old_batch

    def test_file_scoped_message_prefixes_find_related_threads_and_sessions(self) -> None:
        store.upsert_messages(
            [
                NormMessage(
                    id="account-a/chat.json:1",
                    sender="Alice",
                    is_self=0,
                    timestamp="2024-01-06T10:00:00",
                    content="account a first",
                    msg_type="文本消息",
                    thread="项目群",
                    reply_to=None,
                ),
                NormMessage(
                    id="account-a/chat.json:2",
                    sender="Bob",
                    is_self=0,
                    timestamp="2024-01-06T10:01:00",
                    content="account a second",
                    msg_type="文本消息",
                    thread="项目群",
                    reply_to=None,
                ),
                NormMessage(
                    id="account-b/chat.json:1",
                    sender="Carol",
                    is_self=0,
                    timestamp="2024-01-06T11:00:00",
                    content="account b first",
                    msg_type="文本消息",
                    thread="其它群",
                    reply_to=None,
                ),
            ]
        )
        store.replace_sessions(
            [
                {
                    "thread": "项目群",
                    "start_time": "2024-01-06T10:00:00",
                    "end_time": "2024-01-06T10:01:00",
                    "participants": json.dumps(["Alice", "Bob"], ensure_ascii=False),
                    "msg_ids": json.dumps(["account-a/chat.json:1", "account-a/chat.json:2"], ensure_ascii=False),
                    "text": "Alice: account a first\nBob: account a second",
                    "summary": None,
                },
                {
                    "thread": "其它群",
                    "start_time": "2024-01-06T11:00:00",
                    "end_time": "2024-01-06T11:00:00",
                    "participants": json.dumps(["Carol"], ensure_ascii=False),
                    "msg_ids": json.dumps(["account-b/chat.json:1"], ensure_ascii=False),
                    "text": "Carol: account b first",
                    "summary": None,
                },
            ]
        )

        threads = store.get_threads_for_message_id_prefixes(["account-a/chat.json:"])
        session_ids = store.get_session_ids_for_message_id_prefixes(["account-a/chat.json:"])
        scoped_sessions = store.get_sessions(session_ids)
        index_status = store.get_session_index_status(session_ids)

        self.assertEqual(threads, ["项目群"])
        self.assertEqual(len(session_ids), 1)
        self.assertEqual(scoped_sessions[0]["thread"], "项目群")
        self.assertEqual(index_status["total"], 1)
        self.assertEqual(index_status["missing_summary"], 1)
        self.assertIn("missing_embedding", index_status)

    def test_ingest_file_records_expose_changed_alias_for_legacy_inserted_column(self) -> None:
        path = str(Path(self._tmp.name) / "chat.json")

        store.record_ingest_file(path, size=100, mtime_ns=200, total=10, included=9, changed=4)
        records = store.get_ingest_file_records([path])

        self.assertEqual(records[path]["changed"], 4)
        self.assertEqual(records[path]["inserted"], 4)
        self.assertEqual(records[path]["parser_version"], PARSER_VERSION)
        self.assertTrue(store.ingest_file_unchanged(path, size=100, mtime_ns=200))

    def test_ingest_file_unchanged_treats_legacy_parser_version_as_stale(self) -> None:
        path = str(Path(self._tmp.name) / "legacy-parser.json")
        conn = store.db()
        conn.execute(
            """
            INSERT INTO ingest_files (path, size, mtime_ns, total, included, inserted, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            """,
            (path, 100, 200, 10, 9, 4),
        )
        conn.commit()

        records = store.get_ingest_file_records([path])

        self.assertIsNone(records[path]["parser_version"])
        self.assertFalse(store.ingest_file_unchanged(path, size=100, mtime_ns=200))

    def test_ingest_file_message_mapping_finds_sessions_for_platform_ids(self) -> None:
        path = str(Path(self._tmp.name) / "platform.json")
        store.record_ingest_file_messages(path, ["m1", "m2"])

        threads = store.get_threads_for_ingest_file_paths([path])
        session_ids = store.get_session_ids_for_ingest_file_paths([path])
        status = store.get_session_index_status(session_ids)

        self.assertEqual(threads, ["项目群"])
        self.assertEqual(len(session_ids), 1)
        self.assertEqual(status["total"], 1)
        self.assertEqual(status["missing_summary"], 0)

    def test_batch_ingest_file_mapping_helpers_preserve_per_file_scope(self) -> None:
        first_path = str(Path(self._tmp.name) / "first.json")
        second_path = str(Path(self._tmp.name) / "second.json")
        missing_path = str(Path(self._tmp.name) / "missing.json")
        store.record_ingest_file_messages(first_path, ["m1", "m2"])
        store.record_ingest_file_messages(second_path, ["m3"])

        mapped_paths = store.get_ingest_file_message_mapping_paths([first_path, second_path, missing_path])
        by_path = store.get_session_ids_by_ingest_file_paths([first_path, second_path, missing_path])

        self.assertEqual(mapped_paths, {first_path, second_path})
        self.assertEqual(by_path[first_path], [1])
        self.assertNotIn(second_path, by_path)
        self.assertNotIn(missing_path, by_path)
        self.assertEqual(store.get_session_ids_for_ingest_file_paths([first_path, second_path]), [1])

    def test_batch_session_index_statuses_match_single_status(self) -> None:
        single = store.get_session_index_status([1])
        batch = store.get_session_index_statuses({"target": [1], "empty": [], "missing": [999]})

        self.assertEqual(batch["target"], single)
        self.assertEqual(batch["empty"]["total"], 0)
        self.assertEqual(batch["empty"]["missing_summary"], 0)
        self.assertEqual(batch["missing"]["total"], 0)
        self.assertEqual(batch["missing"]["missing_summary"], 0)

    def test_blank_summary_counts_as_missing_for_global_repair(self) -> None:
        from core import ingest

        conn = store.db()
        with conn:
            conn.execute("UPDATE sessions SET summary = '   ' WHERE session_id = 1")

        status = store.get_session_index_status([1])
        chunks, session_ids, summaries = ingest.load_existing_chunks([1])
        with (
            patch("core.retrieval.embed_configured", return_value=False),
            patch("core.retrieval.store.has_vec", return_value=False),
        ):
            search = retrieval.semantic_search({"query": "预算", "limit": 1})

        self.assertEqual(status["missing_summary"], 1)
        self.assertEqual(store.count_sessions_missing_summary(), 1)
        self.assertEqual(session_ids, [1])
        self.assertIsNone(chunks[0].summary)
        self.assertEqual(summaries, {})
        self.assertIsNone(search["sessions"][0]["summary"])

    def test_ingest_file_message_mapping_ignores_nonexistent_message_ids(self) -> None:
        path = str(Path(self._tmp.name) / "stale-mapping.json")
        store.record_ingest_file_messages(path, ["m1", "missing-id", "m2", "m1"])

        rows = store.db().execute(
            "SELECT msg_id FROM ingest_file_messages WHERE path = ? ORDER BY msg_id",
            (path,),
        ).fetchall()
        self.assertEqual([row["msg_id"] for row in rows], ["m1", "m2"])

        store.record_ingest_file_messages(path, ["missing-id"])
        rows = store.db().execute(
            "SELECT msg_id FROM ingest_file_messages WHERE path = ? ORDER BY msg_id",
            (path,),
        ).fetchall()
        self.assertEqual(rows, [])

    def test_imported_file_scoped_message_ids_prevent_cross_file_overwrites(self) -> None:
        template = {
            "weflow": {},
            "session": {"displayName": "项目群"},
            "messages": [
                {
                    "type": "文本消息",
                    "content": "same exported msg id",
                    "createTime": 1_700_000_000,
                    "msgId": "1",
                }
            ],
        }
        first = parse_weflow(template, Path("first.json"))
        second = parse_weflow(template, Path("second.json"))

        first_write = store.upsert_messages(first.messages)
        second_write = store.upsert_messages(second.messages)

        self.assertEqual(first_write.inserted, 1)
        self.assertEqual(second_write.inserted, 1)
        store.rebuild_fts()
        result = store.search_messages({"query": "same exported", "limit": 10})
        self.assertEqual(result["total_count"], 2)
        self.assertEqual(
            {message["message_id"] for message in result["messages"]},
            {"first.json:1", "second.json:1"},
        )

    def test_imported_file_scoped_platform_ids_prevent_cross_file_overwrites(self) -> None:
        template = {
            "weflow": {},
            "session": {"displayName": "项目群"},
            "messages": [
                {
                    "type": "文本消息",
                    "content": "same platform id different file",
                    "createTime": 1_700_000_000,
                    "platformMessageId": "platform-same",
                }
            ],
        }
        first = parse_weflow(template, Path("first.json"))
        second = parse_weflow(template, Path("second.json"))

        first_write = store.upsert_messages(first.messages)
        second_write = store.upsert_messages(second.messages)

        self.assertEqual(first_write.inserted, 1)
        self.assertEqual(second_write.inserted, 1)
        store.rebuild_fts()
        result = store.search_messages({"query": "same platform", "limit": 10})
        self.assertEqual(result["total_count"], 2)
        self.assertEqual(
            {message["message_id"] for message in result["messages"]},
            {"first.json:platform-same", "second.json:platform-same"},
        )

    def test_imported_same_filename_in_different_directories_keeps_distinct_messages(self) -> None:
        template = {
            "weflow": {},
            "session": {"displayName": "项目群"},
            "messages": [
                {
                    "type": "文本消息",
                    "content": "same filename different directory",
                    "createTime": 1_700_000_000,
                    "msgId": "1",
                }
            ],
        }
        first = parse_weflow(template, Path("account-a/chat.json"))
        second = parse_weflow(template, Path("account-b/chat.json"))

        first_write = store.upsert_messages(first.messages)
        second_write = store.upsert_messages(second.messages)

        self.assertEqual(first_write.inserted, 1)
        self.assertEqual(second_write.inserted, 1)
        store.rebuild_fts()
        result = store.search_messages({"query": "same filename", "limit": 10})
        self.assertEqual(result["total_count"], 2)
        self.assertEqual(
            {message["message_id"] for message in result["messages"]},
            {"account-a/chat.json:1", "account-b/chat.json:1"},
        )

    def test_directory_scoped_reimport_upgrades_legacy_basename_scoped_ids_without_duplicates(self) -> None:
        legacy_messages = [
            NormMessage(
                id="chat.json:1",
                sender="Alice",
                is_self=0,
                timestamp="2024-01-06T10:00:00",
                content="basename scoped parent",
                msg_type="文本消息",
                thread="目录升级群",
                reply_to=None,
            ),
            NormMessage(
                id="chat.json:2",
                sender="Bob",
                is_self=0,
                timestamp="2024-01-06T10:01:00",
                content="basename scoped reply",
                msg_type="引用消息",
                thread="目录升级群",
                reply_to="chat.json:1",
            ),
            NormMessage(
                id="1",
                sender="Raw",
                is_self=0,
                timestamp="2024-01-06T10:00:00",
                content="raw id should not be preferred over basename scoped id",
                msg_type="文本消息",
                thread="其它目录升级群",
                reply_to=None,
            ),
        ]
        store.upsert_messages(legacy_messages)
        store.replace_sessions(
            [
                {
                    "thread": "目录升级群",
                    "start_time": "2024-01-06T10:00:00",
                    "end_time": "2024-01-06T10:01:00",
                    "participants": json.dumps(["Alice", "Bob"], ensure_ascii=False),
                    "msg_ids": json.dumps(["chat.json:1", "chat.json:2"], ensure_ascii=False),
                    "text": "Alice: basename scoped parent\nBob: basename scoped reply",
                    "summary": None,
                }
            ],
            ["目录升级群"],
        )
        data = {
            "weflow": {},
            "session": {"displayName": "目录升级群"},
            "messages": [
                {
                    "type": "文本消息",
                    "content": "basename scoped parent",
                    "createTime": "2024-01-06T10:00:00",
                    "senderDisplayName": "Alice",
                    "msgId": "1",
                },
                {
                    "type": "引用消息",
                    "content": "basename scoped reply",
                    "createTime": "2024-01-06T10:01:00",
                    "senderDisplayName": "Bob",
                    "msgId": "2",
                    "replyToMessageId": "1",
                },
            ],
        }

        parsed = parse_weflow(data, Path("account-a/chat.json"))
        write = store.upsert_messages(parsed.messages)

        self.assertEqual(write.inserted, 0)
        self.assertEqual(write.updated, 2)
        conn = store.db()
        rows = conn.execute("SELECT id, reply_to FROM messages WHERE thread = ? ORDER BY id", ("目录升级群",)).fetchall()
        self.assertEqual(
            [(row["id"], row["reply_to"]) for row in rows],
            [("account-a/chat.json:1", None), ("account-a/chat.json:2", "account-a/chat.json:1")],
        )
        self.assertIsNotNone(conn.execute("SELECT id FROM messages WHERE id = '1'").fetchone())
        mapped = conn.execute(
            """
            SELECT ms.msg_id
            FROM msg_session ms
            JOIN sessions s ON s.session_id = ms.session_id
            WHERE s.thread = ?
            ORDER BY ms.msg_id
            """,
            ("目录升级群",),
        ).fetchall()
        self.assertEqual([row["msg_id"] for row in mapped], ["account-a/chat.json:1", "account-a/chat.json:2"])

    def test_scoped_message_id_upgrade_keeps_ingest_file_mapping_attached(self) -> None:
        path = str(Path(self._tmp.name) / "upgrade-source.json")
        store.upsert_messages(
            [
                NormMessage(
                    id="source.json:1",
                    sender="Alice",
                    is_self=0,
                    timestamp="2024-01-07T10:00:00",
                    content="source mapping survives rename",
                    msg_type="文本消息",
                    thread="来源升级群",
                    reply_to=None,
                )
            ]
        )
        store.record_ingest_file_messages(path, ["source.json:1"])

        parsed = parse_weflow(
            {
                "weflow": {},
                "session": {"displayName": "来源升级群"},
                "messages": [
                    {
                        "type": "文本消息",
                        "content": "source mapping survives rename",
                        "createTime": "2024-01-07T10:00:00",
                        "senderDisplayName": "Alice",
                        "msgId": "1",
                    }
                ],
            },
            Path("account-a/source.json"),
        )
        store.upsert_messages(parsed.messages)

        rows = store.db().execute(
            "SELECT msg_id FROM ingest_file_messages WHERE path = ? ORDER BY msg_id",
            (path,),
        ).fetchall()
        self.assertEqual([row["msg_id"] for row in rows], ["account-a/source.json:1"])

    def test_partial_reimport_scopes_reply_to_existing_target_in_same_file_scope(self) -> None:
        store.upsert_messages(
            [
                NormMessage(
                    id="account-a/chat.json:1",
                    sender="Alice",
                    is_self=0,
                    timestamp="2024-01-07T10:00:00",
                    content="already imported parent",
                    msg_type="文本消息",
                    thread="部分导入群",
                    reply_to=None,
                )
            ]
        )
        parsed = parse_weflow(
            {
                "weflow": {},
                "session": {"displayName": "部分导入群"},
                "messages": [
                    {
                        "type": "引用消息",
                        "content": "partial reply",
                        "createTime": "2024-01-07T10:01:00",
                        "senderDisplayName": "Bob",
                        "msgId": "2",
                        "replyToMessageId": "1",
                    }
                ],
            },
            Path("account-a/chat.json"),
        )
        self.assertEqual(parsed.messages[0].reply_to, "1")

        store.upsert_messages(parsed.messages)

        row = store.db().execute(
            "SELECT reply_to FROM messages WHERE id = ?",
            ("account-a/chat.json:2",),
        ).fetchone()
        self.assertEqual(row["reply_to"], "account-a/chat.json:1")

    def test_partial_reimport_scopes_reply_to_existing_stable_upload_scope(self) -> None:
        scope = "uploads/stable-chat-scope"
        upload_path = Path(self._tmp.name) / "uploads" / "random-upload.json"
        upload_path.parent.mkdir()
        upload_path.with_suffix(upload_path.suffix + ".meta").write_text(
            json.dumps({"scope": scope}, ensure_ascii=False),
            encoding="utf-8",
        )
        store.upsert_messages(
            [
                NormMessage(
                    id=f"{scope}:1",
                    sender="Alice",
                    is_self=0,
                    timestamp="2024-01-07T10:00:00",
                    content="stable upload parent",
                    msg_type="文本消息",
                    thread="稳定上传群",
                    reply_to=None,
                )
            ]
        )
        parsed = parse_weflow(
            {
                "weflow": {},
                "session": {"displayName": "稳定上传群"},
                "messages": [
                    {
                        "type": "引用消息",
                        "content": "stable upload reply",
                        "createTime": "2024-01-07T10:01:00",
                        "senderDisplayName": "Bob",
                        "msgId": "2",
                        "replyToMessageId": "1",
                    }
                ],
            },
            upload_path,
        )
        self.assertEqual(parsed.messages[0].id, f"{scope}:2")
        self.assertEqual(parsed.messages[0].reply_to, "1")

        store.upsert_messages(parsed.messages)

        row = store.db().execute(
            "SELECT reply_to FROM messages WHERE id = ?",
            (f"{scope}:2",),
        ).fetchone()
        self.assertEqual(row["reply_to"], f"{scope}:1")

    def test_stable_upload_reimport_upgrades_legacy_upload_uuid_scope_without_duplicate(self) -> None:
        legacy_id = "local/uploads/11111111-1111-1111-1111-111111111111.json:m-1"
        other_thread_id = "local/uploads/22222222-2222-2222-2222-222222222222.json:m-1"
        stable_id = "uploads/stable-chat-scope:m-1"
        legacy_file_key = str(Path(self._tmp.name) / "local" / "uploads" / "legacy.json")
        store.upsert_messages(
            [
                NormMessage(
                    id=legacy_id,
                    sender="Alice",
                    is_self=0,
                    timestamp="2024-01-07T10:00:00",
                    content="legacy upload row",
                    msg_type="文本消息",
                    thread="旧上传升级群",
                    reply_to=None,
                ),
                NormMessage(
                    id=other_thread_id,
                    sender="Alice",
                    is_self=0,
                    timestamp="2024-01-07T10:00:00",
                    content="legacy upload row",
                    msg_type="文本消息",
                    thread="其它旧上传群",
                    reply_to=None,
                ),
            ]
        )
        store.record_ingest_file_messages(legacy_file_key, [legacy_id])
        store.replace_sessions(
            [
                {
                    "thread": "旧上传升级群",
                    "start_time": "2024-01-07T10:00:00",
                    "end_time": "2024-01-07T10:00:00",
                    "participants": json.dumps(["Alice"], ensure_ascii=False),
                    "msg_ids": json.dumps([legacy_id], ensure_ascii=False),
                    "text": "Alice: legacy upload row",
                    "summary": None,
                }
            ],
            ["旧上传升级群"],
        )

        write = store.upsert_messages(
            [
                NormMessage(
                    id=stable_id,
                    sender="Alice",
                    is_self=0,
                    timestamp="2024-01-07T10:00:00",
                    content="legacy upload row",
                    msg_type="文本消息",
                    thread="旧上传升级群",
                    reply_to=None,
                )
            ]
        )

        self.assertEqual(write.inserted, 0)
        self.assertEqual(write.updated, 1)
        rows = store.db().execute(
            "SELECT id FROM messages WHERE thread IN (?, ?) ORDER BY thread, id",
            ("旧上传升级群", "其它旧上传群"),
        ).fetchall()
        self.assertEqual(
            [row["id"] for row in rows],
            [other_thread_id, stable_id],
        )
        mapped = store.db().execute(
            "SELECT msg_id FROM ingest_file_messages WHERE path = ?",
            (legacy_file_key,),
        ).fetchone()
        self.assertEqual(mapped["msg_id"], stable_id)
        session_msg = store.db().execute(
            """
            SELECT ms.msg_id
            FROM msg_session ms
            JOIN sessions s ON s.session_id = ms.session_id
            WHERE s.thread = ?
            """,
            ("旧上传升级群",),
        ).fetchone()
        self.assertEqual(session_msg["msg_id"], stable_id)

    def test_partial_stable_upload_reimport_links_reply_to_legacy_upload_parent(self) -> None:
        legacy_parent_id = "local/uploads/11111111-1111-1111-1111-111111111111.json:1"
        stable_reply_id = "uploads/stable-chat-scope:2"
        store.upsert_messages(
            [
                NormMessage(
                    id=legacy_parent_id,
                    sender="Alice",
                    is_self=0,
                    timestamp="2024-01-07T10:00:00",
                    content="legacy upload parent",
                    msg_type="文本消息",
                    thread="旧上传引用群",
                    reply_to=None,
                )
            ]
        )

        store.upsert_messages(
            [
                NormMessage(
                    id=stable_reply_id,
                    sender="Bob",
                    is_self=0,
                    timestamp="2024-01-07T10:01:00",
                    content="stable upload reply",
                    msg_type="引用消息",
                    thread="旧上传引用群",
                    reply_to="1",
                )
            ]
        )

        row = store.db().execute(
            "SELECT reply_to FROM messages WHERE id = ?",
            (stable_reply_id,),
        ).fetchone()
        self.assertEqual(row["reply_to"], legacy_parent_id)

    def test_partial_reimport_scopes_reply_to_existing_basename_target(self) -> None:
        store.upsert_messages(
            [
                NormMessage(
                    id="chat.json:1",
                    sender="Alice",
                    is_self=0,
                    timestamp="2024-01-07T10:00:00",
                    content="legacy basename parent",
                    msg_type="文本消息",
                    thread="部分旧库群",
                    reply_to=None,
                )
            ]
        )
        parsed = parse_weflow(
            {
                "weflow": {},
                "session": {"displayName": "部分旧库群"},
                "messages": [
                    {
                        "type": "引用消息",
                        "content": "partial basename reply",
                        "createTime": "2024-01-07T10:01:00",
                        "senderDisplayName": "Bob",
                        "msgId": "2",
                        "replyToMessageId": "1",
                    }
                ],
            },
            Path("account-a/chat.json"),
        )

        store.upsert_messages(parsed.messages)

        row = store.db().execute(
            "SELECT reply_to FROM messages WHERE id = ?",
            ("account-a/chat.json:2",),
        ).fetchone()
        self.assertEqual(row["reply_to"], "chat.json:1")

    def test_reply_to_normalization_does_not_create_self_reference(self) -> None:
        store.upsert_messages(
            [
                NormMessage(
                    id="account-a/chat.json:1",
                    sender="Alice",
                    is_self=0,
                    timestamp="2024-01-07T10:00:00",
                    content="malformed self reply",
                    msg_type="引用消息",
                    thread="自引用保护群",
                    reply_to="1",
                )
            ]
        )

        row = store.db().execute(
            "SELECT reply_to FROM messages WHERE id = ?",
            ("account-a/chat.json:1",),
        ).fetchone()
        self.assertEqual(row["reply_to"], "1")

    def test_scoped_platform_id_reimport_upgrades_legacy_raw_platform_id_without_duplicate(self) -> None:
        legacy = NormMessage(
            id="legacy-platform",
            sender="Alice",
            is_self=0,
            timestamp="2024-01-08T10:00:00",
            content="legacy platform id upgrade",
            msg_type="文本消息",
            thread="平台升级群",
            reply_to=None,
        )
        store.upsert_messages([legacy])
        parsed = parse_weflow(
            {
                "weflow": {},
                "session": {"displayName": "平台升级群"},
                "messages": [
                    {
                        "type": "文本消息",
                        "content": "legacy platform id upgrade",
                        "createTime": "2024-01-08T10:00:00",
                        "senderDisplayName": "Alice",
                        "platformMessageId": "legacy-platform",
                    }
                ],
            },
            Path("account-a/platform.json"),
        )

        write = store.upsert_messages(parsed.messages)

        self.assertEqual(write.inserted, 0)
        self.assertEqual(write.updated, 1)
        rows = store.db().execute(
            "SELECT id FROM messages WHERE thread = ? ORDER BY id",
            ("平台升级群",),
        ).fetchall()
        self.assertEqual([row["id"] for row in rows], ["account-a/platform.json:legacy-platform"])

    def test_scoped_message_id_reimport_upgrades_legacy_unscoped_rows_without_duplicates(self) -> None:
        legacy_messages = [
            NormMessage(
                id="1",
                sender="wxid-alice",
                is_self=0,
                timestamp="2024-01-05T10:00:00",
                content="legacy parent",
                msg_type="文本消息",
                thread="升级群",
                reply_to=None,
            ),
            NormMessage(
                id="2",
                sender="Bob",
                is_self=0,
                timestamp="2024-01-05T10:01:00",
                content="old reply without inline quote",
                msg_type="引用消息",
                thread="升级群",
                reply_to="1",
            ),
        ]
        store.upsert_messages(legacy_messages)
        store.upsert_messages(
            [
                NormMessage(
                    id="other-thread-reply",
                    sender="Carol",
                    is_self=0,
                    timestamp="2024-01-05T10:02:00",
                    content="other thread should keep raw reply target",
                    msg_type="引用消息",
                    thread="其它群",
                    reply_to="1",
                )
            ]
        )
        store.replace_sessions(
            [
                {
                    "thread": "升级群",
                    "start_time": "2024-01-05T10:00:00",
                    "end_time": "2024-01-05T10:01:00",
                    "participants": json.dumps(["Alice", "Bob"], ensure_ascii=False),
                    "msg_ids": json.dumps(["1", "2"], ensure_ascii=False),
                    "text": "Alice: legacy parent\nBob: legacy reply",
                    "summary": None,
                }
            ],
            ["升级群"],
        )
        data = {
            "weflow": {},
            "session": {"displayName": "升级群"},
            "messages": [
                {
                    "type": "文本消息",
                    "content": "legacy parent",
                    "createTime": "2024-01-05T10:00:00",
                    "senderDisplayName": "Alice",
                    "msgId": "1",
                },
                {
                    "type": "引用消息",
                    "content": "legacy reply",
                    "quotedSender": "Alice",
                    "quotedContent": "legacy parent",
                    "createTime": "2024-01-05T10:01:00",
                    "senderDisplayName": "Bob",
                    "msgId": "2",
                    "replyToMessageId": "1",
                },
            ],
        }

        parsed = parse_weflow(data, Path("upgrade.json"))
        write = store.upsert_messages(parsed.messages)

        self.assertEqual(write.inserted, 0)
        self.assertEqual(write.updated, 2)
        self.assertEqual(write.unchanged, 0)
        self.assertEqual(write.threads, frozenset({"升级群"}))

        conn = store.db()
        rows = conn.execute(
            "SELECT id, sender, content, reply_to FROM messages WHERE thread = ? ORDER BY id",
            ("升级群",),
        ).fetchall()
        self.assertEqual(
            [(row["id"], row["sender"], row["content"], row["reply_to"]) for row in rows],
            [
                ("upgrade.json:1", "Alice", "legacy parent", None),
                ("upgrade.json:2", "Bob", "legacy reply\n[引用 Alice：legacy parent]", "upgrade.json:1"),
            ],
        )
        other_reply = conn.execute(
            "SELECT reply_to FROM messages WHERE id = ?",
            ("other-thread-reply",),
        ).fetchone()
        self.assertEqual(other_reply["reply_to"], "1")
        mapped = conn.execute(
            """
            SELECT ms.msg_id
            FROM msg_session ms
            JOIN sessions s ON s.session_id = ms.session_id
            WHERE s.thread = ?
            ORDER BY ms.msg_id
            """,
            ("升级群",),
        ).fetchall()
        self.assertEqual([row["msg_id"] for row in mapped], ["upgrade.json:1", "upgrade.json:2"])

    def test_close_all_connections_releases_connections_from_worker_threads(self) -> None:
        worker_connections = []

        def open_worker_connection() -> None:
            worker_connections.append(store.db())

        thread = threading.Thread(target=open_worker_connection)
        thread.start()
        thread.join()

        main_connection = store.db()
        self.assertGreaterEqual(len(store._connections), 2)

        store.close_all_connections()

        self.assertEqual(store._connections, {})
        self.assertIsNone(store._conn)
        with self.assertRaises(sqlite3.ProgrammingError):
            main_connection.execute("SELECT 1")
        with self.assertRaises(sqlite3.ProgrammingError):
            worker_connections[0].execute("SELECT 1")

    def test_sqlite_vec_load_failure_warns_only_once_per_process(self) -> None:
        original_import = __import__

        def import_side_effect(name, *args, **kwargs):
            if name == "sqlite_vec":
                raise ImportError("sqlite_vec missing")
            return original_import(name, *args, **kwargs)

        store.close_all_connections()
        store._vec_warning_emitted = False
        worker_connections = []

        def open_worker_connection() -> None:
            worker_connections.append(store.db())

        with patch("builtins.__import__", side_effect=import_side_effect), patch("builtins.print") as print_mock:
            main_connection = store.db()
            thread = threading.Thread(target=open_worker_connection)
            thread.start()
            thread.join()

        self.assertIsNotNone(main_connection)
        self.assertEqual(len(worker_connections), 1)
        warning_calls = [
            call
            for call in print_mock.call_args_list
            if call.args and "sqlite-vec 加载失败" in str(call.args[0])
        ]
        self.assertEqual(len(warning_calls), 1)
        store.close_all_connections()

    def _seed_data(self) -> None:
        messages = [
            NormMessage(
                id="m1",
                sender="Alice",
                is_self=0,
                timestamp="2024-01-01T10:00:00",
                content="周五例会讨论预算",
                msg_type="文本消息",
                thread="项目群",
                reply_to=None,
            ),
            NormMessage(
                id="m2",
                sender="我",
                is_self=1,
                timestamp="2024-01-01T10:01:00",
                content="AI 方案下午同步",
                msg_type="文本消息",
                thread="项目群",
                reply_to=None,
            ),
            NormMessage(
                id="m3",
                sender="Alice",
                is_self=0,
                timestamp="2024-01-01T10:02:00",
                content="AI 排序测试 A",
                msg_type="文本消息",
                thread="项目群",
                reply_to=None,
            ),
            NormMessage(
                id="m4",
                sender="我",
                is_self=1,
                timestamp="2024-01-01T10:02:00",
                content="AI 排序测试 B",
                msg_type="文本消息",
                thread="项目群",
                reply_to=None,
            ),
        ]
        store.upsert_messages(messages)
        store.recompute_message_sequence(["项目群"])
        store.rebuild_fts()
        store.replace_sessions(
            [
                {
                    "thread": "项目群",
                    "start_time": "2024-01-01T10:00:00",
                    "end_time": "2024-01-01T10:01:00",
                    "participants": json.dumps(["Alice", "我"], ensure_ascii=False),
                    "msg_ids": json.dumps(["m1", "m2"], ensure_ascii=False),
                    "text": "Alice: 周五例会讨论预算\n我: AI 方案下午同步",
                    "summary": "项目群讨论预算和 AI 方案",
                }
            ]
        )


if __name__ == "__main__":
    unittest.main()
