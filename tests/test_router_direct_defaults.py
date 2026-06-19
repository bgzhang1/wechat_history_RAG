from __future__ import annotations

import unittest
from unittest.mock import patch

from backend.routers import chat, ingest, logs, stats, suggestions


class RouterDirectDefaultTests(unittest.TestCase):
    def test_logs_route_uses_http_defaults_when_called_directly(self) -> None:
        with patch("backend.routers.logs.read_recent_logs", return_value=[]) as read_logs:
            self.assertEqual(logs.get_recent_logs(), [])

        read_logs.assert_called_once_with(level="error", limit=100)

    def test_stats_route_does_not_treat_query_default_as_include_details(self) -> None:
        with (
            patch("backend.routers.stats.store.stats_summary", return_value={"total_messages": 1}) as summary,
            patch("backend.routers.stats.store.stats") as detailed,
        ):
            result = stats.get_stats()

        self.assertEqual(result, {"total_messages": 1})
        summary.assert_called_once_with()
        detailed.assert_not_called()

    def test_suggestions_route_uses_http_defaults_when_called_directly(self) -> None:
        with patch(
            "backend.routers.suggestions.store.suggest_entities",
            return_value={"query": "", "items": []},
        ) as suggest:
            result = suggestions.get_suggestions()

        self.assertEqual(result, {"query": "", "items": []})
        suggest.assert_called_once_with(query="", limit=10)

    def test_chat_session_routes_use_http_defaults_when_called_directly(self) -> None:
        with (
            patch("backend.routers.chat.session_store.list_sessions", return_value=[]) as list_sessions,
            patch("backend.routers.chat.session_store.count_sessions", return_value=0),
        ):
            sessions = chat.list_sessions()

        self.assertEqual(sessions["offset"], 0)
        list_sessions.assert_called_once_with(limit=100, offset=0)

        with (
            patch("backend.routers.chat.session_store.get_session", return_value={"session_id": "s1"}),
            patch("backend.routers.chat.session_store.get_messages", return_value=[]) as get_messages,
            patch("backend.routers.chat.session_store.count_messages", return_value=0),
        ):
            messages = chat.get_session_messages("s1")

        self.assertEqual(messages["offset"], 0)
        get_messages.assert_called_once_with("s1", limit=None, offset=0)

    def test_ingest_list_routes_use_http_defaults_when_called_directly(self) -> None:
        with ingest._tasks_lock:
            original_tasks = dict(ingest._tasks)
            ingest._tasks.clear()
            ingest._tasks["task-1"] = {
                "status": "completed",
                "logs": [],
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "error": None,
                "file_id": "chat.json",
                "mode": "full",
                "process": None,
            }

        try:
            tasks = ingest.list_tasks()
        finally:
            with ingest._tasks_lock:
                ingest._tasks.clear()
                ingest._tasks.update(original_tasks)

        self.assertEqual(tasks["offset"], 0)
        self.assertEqual(tasks["returned"], 1)

        with (
            patch("backend.routers.ingest._json_file_snapshots", return_value=[]),
            patch("backend.routers.ingest.store.get_ingest_file_records", return_value={}),
        ):
            files = ingest.list_files()

        self.assertEqual(files, {"total_count": 0, "returned": 0, "offset": 0, "items": []})


if __name__ == "__main__":
    unittest.main()
