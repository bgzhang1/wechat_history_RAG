from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.chunker import Chunk
from core import ingest


class UnprocessableEntityError(Exception):
    pass


def _chunk(text: str) -> Chunk:
    return Chunk(
        thread="thread-a",
        start_time="2024-01-01T10:00:00",
        end_time="2024-01-01T10:01:00",
        participants='["alice"]',
        msg_ids='["m1"]',
        text=text,
    )


class IngestSummaryTests(unittest.TestCase):
    def test_extract_json_accepts_fenced_or_prose_wrapped_json(self) -> None:
        fenced = '```json\n[{"id": 1, "summary": "one"}]\n```'
        wrapped = 'Here is the result: {"1": "one"} thanks'

        self.assertEqual(ingest.extract_json(fenced), '[{"id": 1, "summary": "one"}]')
        self.assertEqual(ingest.extract_json(wrapped), '{"1": "one"}')

    def test_parse_summary_batch_response_accepts_common_model_shapes(self) -> None:
        self.assertEqual(
            ingest.parse_summary_batch_response('{"summaries":[{"id":1,"summary":"one"}]}', [1]),
            {1: "one"},
        )
        self.assertEqual(
            ingest.parse_summary_batch_response('{"1":"one","2":"two"}', [1, 2]),
            {1: "one", 2: "two"},
        )
        self.assertEqual(
            ingest.parse_summary_batch_response('["one","two"]', [3, 4]),
            {3: "one", 4: "two"},
        )

    def test_parse_summary_batch_response_rejects_unusable_json(self) -> None:
        with self.assertRaises(ingest.SummaryBatchParseError):
            ingest.parse_summary_batch_response("no json here", [1])

        with self.assertRaises(ingest.SummaryBatchParseError):
            ingest.parse_summary_batch_response('["only one"]', [1, 2])

    def test_summarize_batch_retries_short_text_on_422_like_errors(self) -> None:
        calls: list[int] = []

        def fake_batch_once(_model: str, _items: list, max_chars: int) -> dict[int, str]:
            calls.append(max_chars)
            if max_chars == 1000:
                raise UnprocessableEntityError("too long")
            return {1: "short-one", 2: "short-two"}

        with patch("core.ingest.summarize_batch_once", side_effect=fake_batch_once):
            summaries, errors, examples = ingest.summarize_batch(
                "model",
                [(10, _chunk("a" * 2000)), (20, _chunk("b" * 2000))],
                max_chars=1000,
                fallback_chars=250,
            )

        self.assertEqual(calls, [1000, 250])
        self.assertEqual(summaries, {10: "short-one", 20: "short-two"})
        self.assertEqual(errors, {})
        self.assertEqual(examples, {})

    def test_summarize_batch_splits_parse_failures_down_to_single_chunks(self) -> None:
        def fake_batch_once(_model: str, items: list[tuple[int, Chunk]], _max_chars: int) -> dict[int, str]:
            if len(items) > 1:
                raise ingest.SummaryBatchParseError("bad batch")
            return {1: f"summary-{items[0][0]}"}

        items = [(10, _chunk("one")), (20, _chunk("two")), (30, _chunk("three")), (40, _chunk("four"))]
        with (
            patch("core.ingest.summarize_batch_once", side_effect=fake_batch_once) as patched,
            patch("core.ingest.summarize_once", side_effect=lambda _model, text: f"single-{text}"),
        ):
            summaries, errors, examples = ingest.summarize_batch(
                "model",
                items,
                max_chars=1000,
                fallback_chars=250,
            )

        self.assertEqual(summaries, {10: "single-one", 20: "single-two", 30: "single-three", 40: "single-four"})
        self.assertEqual(errors, {})
        self.assertEqual(examples, {})
        self.assertEqual(patched.call_count, 3)

    def test_summarize_batch_recovers_missing_items_with_single_chunk_prompt(self) -> None:
        with (
            patch("core.ingest.summarize_batch_once", return_value={1: "batch-one"}),
            patch("core.ingest.invoke_chat", return_value=SimpleNamespace(content="single-two")) as invoke_chat,
        ):
            summaries, errors, examples = ingest.summarize_batch(
                "model",
                [(10, _chunk("one")), (20, _chunk("two"))],
                max_chars=1000,
                fallback_chars=250,
            )

        self.assertEqual(summaries, {10: "batch-one", 20: "single-two"})
        self.assertEqual(errors, {})
        self.assertEqual(examples, {})
        invoke_chat.assert_called_once()


if __name__ == "__main__":
    unittest.main()
