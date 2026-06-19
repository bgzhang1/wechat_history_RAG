from __future__ import annotations

import json
import unittest

from core import chunker


class ChunkerTests(unittest.TestCase):
    def test_chunk_thread_sorts_messages_by_timestamp_before_splitting(self) -> None:
        messages = [
            self._message("m3", "Carol", "2024-01-01T10:02:00", "third"),
            self._message("m1", "Alice", "2024-01-01T10:00:00", "first"),
            self._message("m2", "Bob", "2024-01-01T10:01:00", "second"),
        ]

        chunks = chunker.chunk_thread("项目群", messages)

        self.assertEqual(json.loads(chunks[0].msg_ids), ["m1", "m2", "m3"])
        self.assertLess(chunks[0].text.index("Alice: first"), chunks[0].text.index("Carol: third"))

    def test_trailing_small_block_merges_back_into_previous_nearby_chunk(self) -> None:
        messages = [
            self._message("m1", "Alice", "2024-01-01T10:00:00", "A" * chunker.MAX_CHARS),
            self._message("m2", "Bob", "2024-01-01T10:40:00", "收到"),
        ]

        chunks = chunker.chunk_thread("项目群", messages)

        self.assertEqual(len(chunks), 1)
        self.assertEqual(json.loads(chunks[0].msg_ids), ["m1", "m2"])
        self.assertIn("Bob: 收到", chunks[0].text)

    def test_trailing_small_block_stays_separate_when_gap_is_too_large(self) -> None:
        messages = [
            self._message("m1", "Alice", "2024-01-01T10:00:00", "A" * chunker.MAX_CHARS),
            self._message("m2", "Bob", "2024-01-01T13:00:00", "收到"),
        ]

        chunks = chunker.chunk_thread("项目群", messages)

        self.assertEqual(len(chunks), 2)
        self.assertEqual(json.loads(chunks[1].msg_ids), ["m2"])

    def test_single_oversized_message_is_truncated_only_inside_chunk_text(self) -> None:
        content = "需求细节" * 500
        messages = [self._message("m-long", "Alice", "2024-01-01T10:00:00", content)]

        chunks = chunker.chunk_thread("项目群", messages)

        self.assertEqual(json.loads(chunks[0].msg_ids), ["m-long"])
        self.assertIn(content[: chunker.MAX_MESSAGE_CHARS_IN_CHUNK], chunks[0].text)
        self.assertIn("使用 get_context 查看原文", chunks[0].text)
        self.assertLess(len(chunks[0].text), len(content))

    def test_multiline_messages_repeat_sender_on_each_line_for_summary_context(self) -> None:
        messages = [
            self._message("m1", "Alice", "2024-01-01T10:00:00", "第一行\n第二行\n\n第四行"),
            self._message("m2", "Bob", "2024-01-01T10:01:00", "确认"),
        ]

        chunks = chunker.chunk_thread("项目群", messages)

        self.assertIn("Alice: 第一行\nAlice: 第二行\nAlice: \nAlice: 第四行", chunks[0].text)
        self.assertIn("Bob: 确认", chunks[0].text)

    def test_cross_day_chunk_header_keeps_end_date_visible(self) -> None:
        messages = [
            self._message("m1", "Alice", "2024-01-01T23:50:00", "今晚先发一版"),
            self._message("m2", "Bob", "2024-01-02T00:05:00", "明早继续看"),
        ]

        chunks = chunker.chunk_thread("项目群", messages)

        self.assertIn("[2024-01-01 23:50 ~ 2024-01-02 00:05]", chunks[0].text)

    @staticmethod
    def _message(message_id: str, sender: str, timestamp: str, content: str) -> dict:
        return {
            "id": message_id,
            "sender": sender,
            "timestamp": timestamp,
            "content": content,
        }


if __name__ == "__main__":
    unittest.main()
