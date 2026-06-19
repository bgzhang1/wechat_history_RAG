from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from backend.routers import suggestions
from core import store
from core.parser import NormMessage


class StatsSuggestionsTests(unittest.TestCase):
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

    def test_stats_summary_counts_messages_threads_senders_and_sessions(self) -> None:
        store.replace_sessions(
            [
                {
                    "thread": "Project 100%",
                    "start_time": "2024-01-01T09:00:00",
                    "end_time": "2024-01-01T09:02:00",
                    "participants": '["Alice","Me"]',
                    "msg_ids": '["m1","m2","m3"]',
                    "text": "budget and launch",
                    "summary": "Project launch planning",
                }
            ]
        )

        summary = store.stats_summary()

        self.assertEqual(summary["total_messages"], 6)
        self.assertEqual(summary["thread_count"], 3)
        self.assertEqual(summary["sender_count"], 3)
        self.assertEqual(summary["indexed_session_chunks"], 1)
        self.assertEqual(summary["time_span"]["earliest"], "2024-01-01T09:00:00")
        self.assertEqual(summary["time_span"]["latest"], "2024-01-03T11:00:00")
        self.assertEqual(summary["message_types"][0], {"msg_type": "text", "count": 6})

        light_summary = store.stats_summary(include_message_types=False)
        self.assertEqual(light_summary["total_messages"], 6)
        self.assertEqual(light_summary["thread_count"], 3)
        self.assertEqual(light_summary["sender_count"], 3)
        self.assertEqual(light_summary["indexed_session_chunks"], 1)
        self.assertEqual(light_summary["message_types"], [])

    def test_stats_threads_and_senders_are_paginated_with_total_counts(self) -> None:
        threads = store.stats_threads(limit=2, offset=0)
        next_threads = store.stats_threads(limit=2, offset=2)
        senders = store.stats_senders(limit=2, offset=0)

        self.assertEqual(threads["total_count"], 3)
        self.assertEqual(threads["returned"], 2)
        self.assertEqual(threads["offset"], 0)
        self.assertEqual([item["thread"] for item in threads["items"]], ["Project 100%", "Project 100X"])
        self.assertEqual([item["thread"] for item in next_threads["items"]], ["Road_map"])

        self.assertEqual(senders["total_count"], 3)
        self.assertEqual(senders["returned"], 2)
        self.assertEqual([item["sender"] for item in senders["items"]], ["Alice", "Bob"])
        self.assertIsNone(senders["items"][0]["is_self"])

    def test_stats_lists_use_stable_tie_breakers(self) -> None:
        store.upsert_messages(
            [
                NormMessage(
                    id="m7",
                    sender="Aaron",
                    is_self=0,
                    timestamp="2024-01-04T08:00:00",
                    content="tie one",
                    msg_type="text",
                    thread="Alpha",
                    reply_to=None,
                ),
                NormMessage(
                    id="m8",
                    sender="Aaron",
                    is_self=0,
                    timestamp="2024-01-04T08:01:00",
                    content="tie two",
                    msg_type="text",
                    thread="Alpha",
                    reply_to=None,
                ),
            ]
        )

        threads = store.stats_threads(limit=3, offset=0)
        senders = store.stats_senders(limit=3, offset=0)

        self.assertEqual([item["thread"] for item in threads["items"]], ["Project 100%", "Alpha", "Project 100X"])
        self.assertEqual([item["sender"] for item in senders["items"]], ["Alice", "Aaron", "Bob"])

    def test_suggestions_escape_like_wildcards_and_merge_sender_thread_results(self) -> None:
        percent = store.suggest_entities(query="100%", limit=10)
        underscore = store.suggest_entities(query="Road_", limit=10)
        sender = store.suggest_entities(query="Ali", limit=10)

        self.assertEqual([item["value"] for item in percent["items"]], ["Project 100%"])
        self.assertEqual([item["value"] for item in underscore["items"]], ["Road_map"])
        self.assertEqual(sender["items"][0]["type"], "sender")
        self.assertEqual(sender["items"][0]["value"], "Alice")
        self.assertEqual(sender["items"][0]["count"], 3)

    def test_suggestions_limit_and_empty_query_return_popular_entities(self) -> None:
        result = store.suggest_entities(query="   ", limit=2)

        self.assertEqual(result["query"], "")
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["items"][0]["value"], "Project 100%")
        self.assertGreaterEqual(result["items"][0]["count"], result["items"][1]["count"])

    def test_stats_and_suggestions_ignore_blank_entity_names_and_merge_trimmed_names(self) -> None:
        store.upsert_messages(
            [
                NormMessage(
                    id="m-dirty-trimmed",
                    sender=" Alice ",
                    is_self=0,
                    timestamp="2024-01-04T09:00:00",
                    content="dirty but useful",
                    msg_type="text",
                    thread=" Project 100% ",
                    reply_to=None,
                ),
                NormMessage(
                    id="m-dirty-blank",
                    sender="   ",
                    is_self=0,
                    timestamp="2024-01-04T09:01:00",
                    content="missing sender and thread",
                    msg_type="text",
                    thread=" ",
                    reply_to=None,
                ),
            ]
        )

        summary = store.stats_summary()
        threads = store.stats_threads(limit=10)["items"]
        senders = store.stats_senders(limit=10)["items"]
        suggestions_result = store.suggest_entities(query="", limit=20)

        self.assertEqual(summary["total_messages"], 8)
        self.assertEqual(summary["thread_count"], 3)
        self.assertEqual(summary["sender_count"], 3)
        self.assertEqual(next(item for item in threads if item["thread"] == "Project 100%")["count"], 4)
        self.assertEqual(next(item for item in senders if item["sender"] == "Alice")["count"], 4)
        serialized_suggestions = {item["value"] for item in suggestions_result["items"]}
        self.assertIn("Project 100%", serialized_suggestions)
        self.assertIn("Alice", serialized_suggestions)
        self.assertNotIn("", serialized_suggestions)
        self.assertNotIn(" ", serialized_suggestions)

    def test_suggestion_websocket_message_parser_clamps_and_skips_huge_json(self) -> None:
        query, limit = suggestions._parse_suggestion_message('{"query":" Alice ","limit":999}')
        huge_query = "A" * (suggestions.MAX_SUGGESTION_WS_MESSAGE_CHARS + 100)
        huge_json_query, huge_json_limit = suggestions._parse_suggestion_message(
            f'{{"query":"Bob","limit":50,"padding":"{huge_query}"}}'
        )

        self.assertEqual(query, "Alice")
        self.assertEqual(limit, 50)
        self.assertEqual(huge_json_limit, 0)
        self.assertEqual(huge_json_query, "")

    def test_suggestion_websocket_skips_database_work_for_oversized_messages(self) -> None:
        huge_message = "x" * (suggestions.MAX_SUGGESTION_WS_MESSAGE_CHARS + 1)
        websocket = _OneMessageWebSocket(huge_message)

        with patch("backend.routers.suggestions.store.suggest_entities") as suggest_entities:
            asyncio.run(suggestions.suggestions_socket(websocket))

        suggest_entities.assert_not_called()
        self.assertEqual(websocket.sent, [{"query": "", "items": []}])

    def test_suggestions_get_skips_database_work_for_oversized_query(self) -> None:
        huge_query = "x" * (suggestions.MAX_SUGGESTION_HTTP_QUERY_CHARS + 1)

        with patch("backend.routers.suggestions.store.suggest_entities") as suggest_entities:
            result = suggestions.get_suggestions(query=huge_query, limit=10)

        suggest_entities.assert_not_called()
        self.assertEqual(result, {"query": "", "items": []})

    def _seed_data(self) -> None:
        messages = [
            NormMessage(
                id="m1",
                sender="Alice",
                is_self=0,
                timestamp="2024-01-01T09:00:00",
                content="budget",
                msg_type="text",
                thread="Project 100%",
                reply_to=None,
            ),
            NormMessage(
                id="m2",
                sender="Alice",
                is_self=0,
                timestamp="2024-01-01T09:01:00",
                content="timeline",
                msg_type="text",
                thread="Project 100%",
                reply_to=None,
            ),
            NormMessage(
                id="m3",
                sender="Alice",
                is_self=0,
                timestamp="2024-01-01T09:02:00",
                content="launch",
                msg_type="text",
                thread="Project 100%",
                reply_to=None,
            ),
            NormMessage(
                id="m4",
                sender="Bob",
                is_self=0,
                timestamp="2024-01-02T10:00:00",
                content="roadmap",
                msg_type="text",
                thread="Project 100X",
                reply_to=None,
            ),
            NormMessage(
                id="m5",
                sender="Bob",
                is_self=0,
                timestamp="2024-01-02T10:01:00",
                content="owners",
                msg_type="text",
                thread="Project 100X",
                reply_to=None,
            ),
            NormMessage(
                id="m6",
                sender="Me",
                is_self=1,
                timestamp="2024-01-03T11:00:00",
                content="personal note",
                msg_type="text",
                thread="Road_map",
                reply_to=None,
            ),
        ]
        store.upsert_messages(messages)


class _OneMessageWebSocket:
    def __init__(self, message: str) -> None:
        self.message = message
        self.sent: list[dict] = []
        self.accepted = False
        self._received = False

    async def accept(self) -> None:
        self.accepted = True

    async def receive_text(self) -> str:
        if self._received:
            raise suggestions.WebSocketDisconnect()
        self._received = True
        return self.message

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


if __name__ == "__main__":
    unittest.main()
