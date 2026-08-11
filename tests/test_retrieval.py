from __future__ import annotations

import unittest

from core import retrieval


class RetrievalHelperTests(unittest.TestCase):
    def test_norm_time_expands_date_boundaries(self) -> None:
        self.assertEqual(retrieval._norm_time("2026-06-12", False), "2026-06-12T00:00:00")
        self.assertEqual(retrieval._norm_time("2026-06-12", True), "2026-06-12T23:59:59")

    def test_norm_time_strips_timezone_suffix(self) -> None:
        self.assertEqual(retrieval._norm_time("2026-06-12T20:30:00+08:00", False), "2026-06-12T20:30:00")
        self.assertEqual(retrieval._norm_time("2026-06-12T12:30:00Z", False), "2026-06-12T12:30:00")

    def test_norm_time_rejects_invalid_values(self) -> None:
        self.assertEqual(retrieval._norm_time("not-a-time", False), "")
        self.assertEqual(retrieval._norm_time("2026-02-30", True), "")

    def test_apply_filters_combines_thread_and_time(self) -> None:
        rows = [
            {
                "session_id": 1,
                "thread": "项目群",
                "start_time": "2026-06-10T09:00:00",
                "end_time": "2026-06-10T10:00:00",
            },
            {
                "session_id": 2,
                "thread": "项目群",
                "start_time": "2026-06-13T09:00:00",
                "end_time": "2026-06-13T10:00:00",
            },
            {
                "session_id": 3,
                "thread": "家人群",
                "start_time": "2026-06-10T09:00:00",
                "end_time": "2026-06-10T10:00:00",
            },
        ]

        filtered = retrieval._apply_filters(
            rows,
            {"thread": "项目", "after": "2026-06-09", "before": "2026-06-11"},
        )

        self.assertEqual([row["session_id"] for row in filtered], [1])

    def test_safe_int_clamps_untrusted_limits(self) -> None:
        self.assertEqual(retrieval._safe_int("999", 8, minimum=1, maximum=20), 20)
        self.assertEqual(retrieval._safe_int("bad", 8, minimum=1, maximum=20), 8)
        self.assertEqual(retrieval._safe_int(-4, 8, minimum=1, maximum=20), 1)


if __name__ == "__main__":
    unittest.main()
