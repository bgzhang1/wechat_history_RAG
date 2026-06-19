from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from backend import session_store


class SessionStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self._old_db_path = session_store.DB_PATH
        self._old_conn = session_store._conn
        if self._old_conn is not None:
            self._old_conn.close()
        session_store._conn = None
        session_store.DB_PATH = str(Path(self._tmp.name) / "backend_chat.db")

    def tearDown(self) -> None:
        if session_store._conn is not None:
            session_store._conn.close()
        session_store._conn = None
        session_store.DB_PATH = self._old_db_path
        self._old_conn = None
        self._tmp.cleanup()

    def test_append_exchange_and_message_pagination_return_recent_pages_in_chronological_order(self) -> None:
        session_id = "session-a"
        for index in range(3):
            session_store.append_exchange(session_id, f"question {index}", f"answer {index}")

        self.assertEqual(session_store.count_messages(session_id), 6)

        latest = session_store.get_messages(session_id, limit=2, offset=0)
        previous = session_store.get_messages(session_id, limit=2, offset=2)

        self.assertEqual([row["role"] for row in latest], ["user", "assistant"])
        self.assertEqual([row["content"] for row in latest], ["question 2", "answer 2"])
        self.assertEqual([row["content"] for row in previous], ["question 1", "answer 1"])

        session = session_store.get_session(session_id)
        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(session["title"], "question 0")
        self.assertEqual(session["status"], "idle")

    def test_rename_session_trims_collapses_and_rejects_empty_titles(self) -> None:
        session_store.get_or_create_session("session-rename")

        renamed = session_store.rename_session("session-rename", "  hello   world  ")
        self.assertIsNotNone(renamed)
        assert renamed is not None
        self.assertEqual(renamed["title"], "hello world")

        with self.assertRaises(ValueError):
            session_store.rename_session("session-rename", "   ")

        self.assertIsNone(session_store.rename_session("missing", "title"))

    def test_rename_session_rejects_active_sessions_without_changing_title(self) -> None:
        session_store.append_exchange("active-rename", "original question", "answer")
        self.assertTrue(session_store.try_begin("active-rename"))

        with self.assertRaises(session_store.ActiveSessionError):
            session_store.rename_session("active-rename", "new title")

        session = session_store.get_session("active-rename")
        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(session["title"], "original question")
        self.assertEqual(session["status"], "running")

    def test_batch_delete_deduplicates_and_preserves_active_sessions(self) -> None:
        session_store.append_exchange("delete-me", "q1", "a1")
        session_store.append_exchange("active", "q2", "a2")
        self.assertTrue(session_store.try_begin("active"))

        result = session_store.delete_sessions(["delete-me", "active", "missing", "delete-me"])

        self.assertEqual(result["deleted"], ["delete-me"])
        self.assertEqual(result["missing"], ["missing"])
        self.assertEqual(result["active"], ["active"])
        self.assertIsNone(session_store.get_session("delete-me"))
        self.assertEqual(session_store.count_messages("delete-me"), 0)
        self.assertIsNotNone(session_store.get_session("active"))

    def test_batch_delete_handles_more_ids_than_single_sql_batch(self) -> None:
        for index in range(5):
            session_store.append_exchange(f"delete-{index}", f"q{index}", f"a{index}")
        session_store.append_exchange("active", "q-active", "a-active")
        self.assertTrue(session_store.try_begin("active"))

        with patch.object(session_store, "SQL_BATCH", 2):
            result = session_store.delete_sessions(
                ["delete-0", "delete-1", "active", "missing", "delete-2", "delete-3", "delete-4"]
            )

        self.assertEqual(result["deleted"], ["delete-0", "delete-1", "delete-2", "delete-3", "delete-4"])
        self.assertEqual(result["missing"], ["missing"])
        self.assertEqual(result["active"], ["active"])
        for index in range(5):
            self.assertIsNone(session_store.get_session(f"delete-{index}"))
        self.assertIsNotNone(session_store.get_session("active"))

    def test_list_sessions_uses_stable_tie_breakers_for_same_second_updates(self) -> None:
        conn = session_store.db()
        fixed = "2024-01-01T00:00:00Z"
        rows = [
            ("session-a", "2024-01-01T00:00:00Z"),
            ("session-c", "2024-01-01T00:00:01Z"),
            ("session-b", "2024-01-01T00:00:01Z"),
        ]
        with conn:
            for session_id, created_at in rows:
                conn.execute(
                    """
                    INSERT INTO backend_chat_sessions
                      (session_id, title, status, last_error, created_at, updated_at)
                    VALUES (?, ?, 'idle', NULL, ?, ?)
                    """,
                    (session_id, session_id, created_at, fixed),
                )

        first_page = session_store.list_sessions(limit=2, offset=0)
        second_page = session_store.list_sessions(limit=2, offset=2)

        self.assertEqual([row["session_id"] for row in first_page], ["session-c", "session-b"])
        self.assertEqual([row["session_id"] for row in second_page], ["session-a"])

    def test_session_timestamps_keep_millisecond_precision_for_recent_sorting(self) -> None:
        self.assertRegex(session_store._now(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")

    def test_pagination_helpers_tolerate_invalid_direct_call_values(self) -> None:
        session_store.append_exchange("session-a", "q1", "a1")
        session_store.append_exchange("session-b", "q2", "a2")

        listed = session_store.list_sessions(limit="not-a-number", offset="bad")
        messages = session_store.get_messages("session-a", limit="bad", offset="bad")  # type: ignore[arg-type]

        self.assertGreaterEqual(len(listed), 2)
        self.assertEqual(messages, [])

    def test_database_enforces_message_cascade_when_session_row_is_deleted(self) -> None:
        session_store.append_exchange("cascade-session", "q", "a")
        conn = session_store.db()

        with conn:
            conn.execute(
                "DELETE FROM backend_chat_sessions WHERE session_id = ?",
                ("cascade-session",),
            )

        self.assertEqual(session_store.count_messages("cascade-session"), 0)

    def test_close_connection_releases_and_reopens_database(self) -> None:
        first = session_store.db()
        session_store.close_connection()

        self.assertIsNone(session_store._conn)

        second = session_store.db()
        self.assertIsNot(first, second)
        self.assertIsNotNone(session_store.get_or_create_session("after-close"))

    def test_sqlite_uri_paths_are_connected_with_uri_enabled(self) -> None:
        fake_conn = Mock()
        session_store.DB_PATH = "file:backend-session-test?mode=memory&cache=shared"

        with patch("backend.session_store.sqlite3.connect", return_value=fake_conn) as connect:
            conn = session_store.db()

        self.assertIs(conn, fake_conn)
        self.assertTrue(session_store._sqlite_uri(session_store.DB_PATH))
        connect.assert_called_once_with(
            "file:backend-session-test?mode=memory&cache=shared",
            timeout=30,
            check_same_thread=False,
            uri=True,
        )
        fake_conn.executescript.assert_called_once()


if __name__ == "__main__":
    unittest.main()
