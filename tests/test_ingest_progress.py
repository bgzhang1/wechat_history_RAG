from __future__ import annotations

import io
import json
import os
import unittest
from argparse import Namespace
from contextlib import ExitStack, redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import patch


class IngestProgressTests(unittest.TestCase):
    def test_backend_progress_events_update_state_without_user_logs(self) -> None:
        from backend.routers import ingest

        task_id = "test-progress"
        with ingest._tasks_lock:
            original_tasks = dict(ingest._tasks)
            ingest._tasks.clear()
            ingest._tasks[task_id] = {
                "status": "running",
                "logs": [],
                "created_at": ingest._now(),
                "updated_at": ingest._now(),
                "error": None,
                "file_id": "uploads/test.json",
                "mode": "full",
                "process": None,
            }

        try:
            ingest._append_log(
                task_id,
                '__INGEST_PROGRESS__ {"stage":"embedding","progress":88,"message":"embedding 44/50"}\n',
            )

            status = ingest._task_snapshot(task_id, include_logs=True)
            progress = ingest._task_progress(task_id)

            self.assertEqual(status.progress, 88)
            self.assertEqual(status.stage, "embedding")
            self.assertEqual(status.message, "embedding 44/50")
            self.assertEqual(status.mode, "full")
            self.assertEqual(status.logs, "")
            self.assertEqual(progress.progress, 88)
            self.assertEqual(progress.stage, "embedding")
            self.assertEqual(progress.message, "embedding 44/50")
            self.assertEqual(progress.mode, "full")
            self.assertEqual(progress.log_tail, "")
        finally:
            with ingest._tasks_lock:
                ingest._tasks.clear()
                ingest._tasks.update(original_tasks)

    def test_backend_progress_event_message_is_redacted(self) -> None:
        from backend.routers import ingest

        task_id = "test-progress-redaction"
        with ingest._tasks_lock:
            original_tasks = dict(ingest._tasks)
            ingest._tasks.clear()
            ingest._tasks[task_id] = {
                "status": "running",
                "logs": [],
                "created_at": ingest._now(),
                "updated_at": ingest._now(),
                "error": None,
                "file_id": "uploads/test.json",
                "mode": "full",
                "process": None,
            }

        try:
            ingest._append_log(
                task_id,
                '__INGEST_PROGRESS__ {"stage":"embedding","progress":50,'
                '"message":"embedding failed api_key=sk-progress-secret-123456"}\n',
            )

            progress = ingest._task_progress(task_id)

            self.assertIn("[redacted]", progress.message)
            self.assertNotIn("sk-progress-secret", progress.message)
        finally:
            with ingest._tasks_lock:
                ingest._tasks.clear()
                ingest._tasks.update(original_tasks)

    def test_backend_task_error_is_redacted_in_public_snapshots(self) -> None:
        from backend.routers import ingest

        task_id = "test-error-redaction"
        with ingest._tasks_lock:
            original_tasks = dict(ingest._tasks)
            ingest._tasks.clear()
            ingest._tasks[task_id] = {
                "status": "error",
                "logs": [],
                "created_at": ingest._now(),
                "updated_at": ingest._now(),
                "error": "embedding failed Authorization: Bearer raw-progress-token",
                "file_id": "uploads/test.json",
                "mode": "embeddings",
                "process": None,
            }

        try:
            status = ingest._task_snapshot(task_id, include_logs=True)
            progress = ingest._task_progress(task_id)

            self.assertIn("[redacted]", status.error or "")
            self.assertIn("[redacted]", progress.error or "")
            self.assertNotIn("raw-progress-token", status.error or "")
            self.assertNotIn("raw-progress-token", progress.error or "")
        finally:
            with ingest._tasks_lock:
                ingest._tasks.clear()
                ingest._tasks.update(original_tasks)

    def test_backend_failure_error_uses_redacted_progress_message(self) -> None:
        from backend.routers import ingest

        task_id = "test-failure-progress"
        with ingest._tasks_lock:
            original_tasks = dict(ingest._tasks)
            ingest._tasks.clear()
            ingest._tasks[task_id] = {
                "status": "running",
                "logs": ["generic setup log\n"],
                "created_at": ingest._now(),
                "updated_at": ingest._now(),
                "error": None,
                "file_id": "uploads/test.json",
                "mode": "embeddings",
                "process": None,
                "progress_event": {
                    "stage": "error",
                    "progress": 0,
                    "message": "embedding failed api_key=sk-progress-secret-123456",
                },
            }

        try:
            message = ingest._task_failure_error(task_id, 1)

            self.assertIn("ingest exited with code 1", message)
            self.assertIn("embedding failed", message)
            self.assertIn("[redacted]", message)
            self.assertNotIn("sk-progress-secret", message)
            self.assertNotIn("generic setup log", message)
        finally:
            with ingest._tasks_lock:
                ingest._tasks.clear()
                ingest._tasks.update(original_tasks)

    def test_backend_failure_error_uses_log_tail_when_progress_is_not_error(self) -> None:
        from backend.routers import ingest

        task_id = "test-failure-log-tail"
        with ingest._tasks_lock:
            original_tasks = dict(ingest._tasks)
            ingest._tasks.clear()
            ingest._tasks[task_id] = {
                "status": "running",
                "logs": [
                    "starting ingest\n",
                    "RuntimeError: embedding endpoint rejected token=sk-log-tail-secret-123456\n",
                ],
                "created_at": ingest._now(),
                "updated_at": ingest._now(),
                "error": None,
                "file_id": "uploads/test.json",
                "mode": "embeddings",
                "process": None,
                "progress_event": {
                    "stage": "embedding",
                    "progress": 88,
                    "message": "embedding 44/50",
                },
            }

        try:
            message = ingest._task_failure_error(task_id, 1)

            self.assertIn("ingest exited with code 1", message)
            self.assertIn("embedding endpoint rejected", message)
            self.assertIn("[redacted]", message)
            self.assertNotIn("sk-log-tail-secret", message)
            self.assertNotIn("embedding 44/50", message)
        finally:
            with ingest._tasks_lock:
                ingest._tasks.clear()
                ingest._tasks.update(original_tasks)

    def test_backend_terminal_progress_events_do_not_report_eta(self) -> None:
        from backend.routers import ingest

        task_id = "test-progress-terminal"
        with ingest._tasks_lock:
            original_tasks = dict(ingest._tasks)
            ingest._tasks.clear()
            ingest._tasks[task_id] = {
                "status": "cancelled",
                "logs": [],
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": ingest._now(),
                "error": None,
                "file_id": "uploads/test.json",
                "mode": "full",
                "process": None,
                "progress_event": {"stage": "embedding", "progress": 80, "message": "cancelled"},
            }

        try:
            status = ingest._task_snapshot(task_id, include_logs=True)
            progress = ingest._task_progress(task_id)

            self.assertEqual(status.stage, "cancelled")
            self.assertEqual(status.progress, 80)
            self.assertIsNone(status.eta)
            self.assertEqual(progress.stage, "cancelled")
            self.assertEqual(progress.progress, 80)
            self.assertIsNone(progress.eta)
        finally:
            with ingest._tasks_lock:
                ingest._tasks.clear()
                ingest._tasks.update(original_tasks)

    def test_core_ingest_compact_error_redacts_secret_like_values(self) -> None:
        from core import ingest

        message = ingest.compact_error(
            RuntimeError("Authorization: Bearer raw-token-value api_key=sk-ingest-secret-123456"),
        )

        self.assertIn("[redacted]", message)
        self.assertNotIn("raw-token-value", message)
        self.assertNotIn("sk-ingest-secret", message)

    def test_core_ingest_files_emits_file_progress_only_when_enabled(self) -> None:
        from core import ingest

        default_output = self._run_ingest_files_smoke(ingest, progress_enabled=False)
        self.assertNotIn(ingest.PROGRESS_PREFIX, default_output)

        progress_output = self._run_ingest_files_smoke(ingest, progress_enabled=True)
        events = [
            json.loads(line.removeprefix(ingest.PROGRESS_PREFIX))
            for line in progress_output.splitlines()
            if line.startswith(ingest.PROGRESS_PREFIX)
        ]

        self.assertEqual([event["progress"] for event in events], [10, 17, 17, 24])
        self.assertEqual(events[0]["stage"], "parsing")
        self.assertEqual(events[0]["message"], "解析 1/2: chat-1.json")
        self.assertEqual(events[-1]["message"], "已解析 2/2: chat-2.json，入库 2 条")

    def test_core_ingest_files_continues_after_single_weflow_parse_failure(self) -> None:
        from core import ingest

        class WriteResult:
            inserted = 1
            updated = 0
            changed = 1
            threads = {"good-thread"}

        class ParseResult:
            total = 1
            included = 1
            thread = "good-thread"
            messages = []
            skipped_by_type = {}

        with TemporaryDirectory() as td:
            root = Path(td)
            bad = root / "bad.json"
            good = root / "good.json"
            bad.write_text(json.dumps({"weflow": {}, "session": {}, "messages": []}), encoding="utf-8")
            good.write_text(json.dumps({"weflow": {}, "session": {}, "messages": []}), encoding="utf-8")
            pending: list[tuple[str, int, int, int, int, int]] = []

            def parse_side_effect(_data, file_path):
                if Path(file_path).name == "bad.json":
                    raise RuntimeError("api_key=sk-bad-file-secret-123456 malformed payload")
                return ParseResult()

            with (
                patch("core.ingest.store.ingest_file_unchanged", return_value=False),
                patch("core.ingest.parse_weflow", side_effect=parse_side_effect),
                patch("core.ingest.store.upsert_messages", return_value=WriteResult()) as upsert,
                redirect_stdout(io.StringIO()) as output,
            ):
                result = ingest.ingest_files([bad, good], pending)

        total_included, total_inserted, total_updated, affected_threads, usable_files, failed_files = result
        self.assertEqual(total_included, 1)
        self.assertEqual(total_inserted, 1)
        self.assertEqual(total_updated, 0)
        self.assertEqual(affected_threads, {"good-thread"})
        self.assertEqual(usable_files, 1)
        self.assertEqual(failed_files, 1)
        self.assertEqual(upsert.call_count, 1)
        self.assertEqual(len(pending), 1)
        self.assertIn("WeFlow 内容解析失败", output.getvalue())
        self.assertNotIn("sk-bad-file-secret", output.getvalue())
        self.assertIn("[redacted]", output.getvalue())

    def test_core_ingest_files_force_import_reparses_unchanged_files(self) -> None:
        from core import ingest

        class WriteResult:
            inserted = 0
            updated = 0
            changed = 0
            threads = frozenset()

        with TemporaryDirectory() as td:
            path = Path(td) / "chat.json"
            path.write_text(
                json.dumps(
                    {
                        "weflow": {},
                        "session": {"displayName": "项目群"},
                        "messages": [
                            {
                                "type": "动画表情",
                                "content": "[动画表情]",
                                "emojiCaption": "收到",
                                "createTime": 1_700_000_000,
                                "msgId": "emoji-1",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            pending: list[tuple[str, int, int, int, int, int]] = []

            with (
                patch("core.ingest.store.ingest_file_unchanged", return_value=True),
                patch("core.ingest.store.ingest_file_message_mapping_exists", return_value=True),
                patch("core.ingest.store.upsert_messages", return_value=WriteResult()) as upsert,
                patch("core.ingest.record_file_message_sources") as record_sources,
                redirect_stdout(io.StringIO()) as output,
            ):
                result = ingest.ingest_files([path], pending, force_import=True)

        self.assertEqual(result[:3], (1, 0, 0))
        upsert.assert_called_once()
        record_sources.assert_called_once()
        self.assertEqual(upsert.call_args.args[0][0].msg_type, "动画表情")
        self.assertIn("强制重建，重新解析未变化文件", output.getvalue())

    def test_core_ingest_main_passes_force_import_to_ingest_files(self) -> None:
        from core import ingest

        with TemporaryDirectory() as td:
            path = Path(td) / "chat.json"
            path.write_text("{}", encoding="utf-8")
            args = Namespace(
                targets=[str(path)],
                skip_import=False,
                force_import=True,
                force_rebuild=False,
                force_fts=False,
                force_chunks=False,
                force_summary=False,
                force_embeddings=False,
                no_summary=False,
            )

            with (
                patch("core.ingest.parse_args", return_value=args),
                patch("core.ingest.index_scope_for_files", return_value=([], [])),
                patch("core.ingest.ingest_files", return_value=(0, 0, 0, set(), 1, 0)) as ingest_files,
                patch("core.ingest.embed_configured", return_value=False),
                patch("core.ingest.store.has_vec", return_value=False),
                patch("core.ingest.store.count_missing_fts", return_value=0),
                patch("core.ingest.store.count_messages_missing_seq", return_value=0),
                redirect_stdout(io.StringIO()),
            ):
                ingest.main()

        ingest_files.assert_called_once()
        self.assertTrue(ingest_files.call_args.kwargs["force_import"])

    def test_core_ingest_force_rebuild_scopes_chunks_to_target_json_threads(self) -> None:
        from core import ingest

        with TemporaryDirectory() as td:
            path = Path(td) / "chat.json"
            path.write_text("{}", encoding="utf-8")
            args = Namespace(
                targets=[str(path)],
                skip_import=False,
                force_import=False,
                force_rebuild=True,
                force_fts=False,
                force_chunks=False,
                force_summary=False,
                force_embeddings=False,
                no_summary=False,
            )

            with (
                patch.dict(os.environ, {"SUMMARY_MODEL": ""}, clear=False),
                patch("core.ingest.parse_args", return_value=args),
                patch("core.ingest.index_scope_for_files", return_value=(["目标群"], [7])),
                patch("core.ingest.ingest_files", return_value=(0, 0, 0, set(), 1, 0)),
                patch("core.ingest.embed_configured", return_value=False),
                patch("core.ingest.store.has_vec", return_value=False),
                patch("core.ingest.store.count_missing_fts", return_value=0),
                patch("core.ingest.store.count_messages_missing_seq", return_value=0),
                patch("core.ingest.store.recompute_message_sequence"),
                patch("core.ingest.store.rebuild_fts"),
                patch("core.ingest.rebuild_chunks_scoped") as rebuild_chunks,
                patch("core.ingest.load_existing_chunks", return_value=([], [], {})),
                patch("core.ingest.record_pending_files"),
                redirect_stdout(io.StringIO()),
            ):
                ingest.main()

        rebuild_chunks.assert_called_once_with(["目标群"], True, True)

    def test_core_ingest_rebuild_chunks_does_not_carry_blank_summary(self) -> None:
        from core import ingest

        chunk = ingest.Chunk(
            thread="项目群",
            start_time="2024-01-01T10:00:00",
            end_time="2024-01-01T10:01:00",
            participants='["Alice"]',
            msg_ids='["m1"]',
            text="Alice: hello",
        )

        with (
            patch("core.ingest.store.get_all_messages_by_thread", return_value={"项目群": [{"id": "m1"}]}),
            patch("core.ingest.chunk_thread", return_value=[chunk]),
            patch(
                "core.ingest.store.get_carryover_for_threads",
                return_value={ingest.store.chunk_text_hash(chunk.text): ("   ", None)},
            ),
            patch("core.ingest.store.replace_sessions", return_value=[7]),
            patch("core.ingest.store.set_summaries") as set_summaries,
            patch("core.ingest.store.has_vec", return_value=False),
            redirect_stdout(io.StringIO()),
        ):
            ingest.rebuild_chunks_scoped(["项目群"], force_summary=False, force_embeddings=False)

        set_summaries.assert_called_once_with([])

    def test_core_ingest_fts_only_rebuilds_target_json_fts_when_safe(self) -> None:
        from core import ingest

        with TemporaryDirectory() as td:
            path = Path(td) / "chat.json"
            path.write_text("{}", encoding="utf-8")
            args = Namespace(
                targets=[str(path)],
                skip_import=True,
                force_import=False,
                force_rebuild=False,
                force_fts=True,
                force_chunks=False,
                force_summary=False,
                force_embeddings=False,
                no_summary=False,
            )

            with (
                patch.dict(os.environ, {"SUMMARY_MODEL": ""}, clear=False),
                patch("core.ingest.parse_args", return_value=args),
                patch("core.ingest.index_scope_for_files", return_value=(["目标群"], [7])),
                patch("core.ingest.embed_configured", return_value=False),
                patch("core.ingest.store.has_vec", return_value=False),
                patch("core.ingest.store.count_missing_fts", return_value=0),
                patch("core.ingest.store.count_messages_missing_seq", return_value=0),
                patch("core.ingest.store.recompute_message_sequence") as recompute_sequence,
                patch("core.ingest.store.rebuild_fts_for_ingest_targets", return_value=12) as scoped_rebuild_fts,
                patch("core.ingest.store.rebuild_fts") as rebuild_fts,
                patch("core.ingest.record_pending_files"),
                redirect_stdout(io.StringIO()) as output,
            ):
                ingest.main()

        recompute_sequence.assert_called_once_with(["目标群"])
        scoped_rebuild_fts.assert_called_once_with([str(path.resolve())], [f"{ingest.file_scope_for_path(path)}:"])
        rebuild_fts.assert_not_called()
        self.assertIn("FTS 索引已按目标 JSON 重建 12 条", output.getvalue())

    def test_core_ingest_fts_only_does_not_auto_repair_summary_or_vectors(self) -> None:
        from core import ingest

        with TemporaryDirectory() as td:
            path = Path(td) / "chat.json"
            path.write_text("{}", encoding="utf-8")
            args = Namespace(
                targets=[str(path)],
                skip_import=True,
                force_import=False,
                force_rebuild=False,
                force_fts=True,
                force_chunks=False,
                force_summary=False,
                force_embeddings=False,
                no_summary=False,
            )

            with (
                patch.dict(os.environ, {"SUMMARY_MODEL": "summary-model"}, clear=False),
                patch("core.ingest.parse_args", return_value=args),
                patch("core.ingest.index_scope_for_files", return_value=(["目标群"], [7])),
                patch("core.ingest.chat_config_status", return_value={"configured": True, "missing": [], "model": "summary-model"}),
                patch("core.ingest.embed_configured", return_value=True),
                patch("core.ingest.store.has_vec", return_value=True),
                patch(
                    "core.ingest.store.get_session_index_status",
                    return_value={"total": 1, "missing_summary": 1, "missing_embedding": 1},
                ),
                patch("core.ingest.store.get_session_ids_without_embedding", return_value=[7]),
                patch("core.ingest.store.count_missing_fts_for_ingest_targets", return_value=0),
                patch("core.ingest.store.count_messages_missing_seq_for_ingest_targets", return_value=0),
                patch("core.ingest.store.recompute_message_sequence"),
                patch("core.ingest.store.rebuild_fts_for_ingest_targets", return_value=1),
                patch("core.ingest.load_existing_chunks") as load_chunks,
                patch("core.ingest.summarize_batch") as summarize_batch,
                patch("core.ingest.embed") as embed,
                patch("core.ingest.record_pending_files"),
                redirect_stdout(io.StringIO()) as output,
            ):
                ingest.main()

        load_chunks.assert_not_called()
        summarize_batch.assert_not_called()
        embed.assert_not_called()
        self.assertNotIn("自动补齐", output.getvalue())

    def test_core_ingest_main_fails_when_no_target_file_is_usable(self) -> None:
        from core import ingest

        with TemporaryDirectory() as td:
            path = Path(td) / "broken.json"
            path.write_text("{not-json", encoding="utf-8")
            args = Namespace(
                targets=[str(path)],
                skip_import=False,
                force_import=False,
                force_rebuild=False,
                force_fts=False,
                force_chunks=False,
                force_summary=False,
                force_embeddings=False,
            )

            with (
                patch("core.ingest.parse_args", return_value=args),
                patch("core.ingest.store.ingest_file_unchanged", return_value=False),
                redirect_stdout(io.StringIO()) as output,
                self.assertRaises(SystemExit) as exc,
            ):
                ingest.main()

        self.assertEqual(exc.exception.code, 1)
        self.assertIn("没有可导入的 WeFlow 聊天记录", output.getvalue())

    def test_core_ingest_main_fails_when_no_json_files_are_found(self) -> None:
        from core import ingest

        with TemporaryDirectory() as td:
            root = Path(td)
            (root / "notes.txt").write_text("not json", encoding="utf-8")
            args = Namespace(
                targets=[str(root)],
                skip_import=False,
                force_import=False,
                force_rebuild=False,
                force_fts=False,
                force_chunks=False,
                force_summary=False,
                force_embeddings=False,
            )

            with (
                patch("core.ingest.parse_args", return_value=args),
                redirect_stdout(io.StringIO()) as output,
                self.assertRaises(SystemExit) as exc,
            ):
                ingest.main()

        self.assertEqual(exc.exception.code, 1)
        self.assertIn("未找到可导入的 JSON 文件", output.getvalue())

    def test_core_ingest_skip_import_fails_when_no_json_target_is_found(self) -> None:
        from core import ingest

        with TemporaryDirectory() as td:
            root = Path(td)
            (root / "notes.txt").write_text("not json", encoding="utf-8")
            args = Namespace(
                targets=[str(root)],
                skip_import=True,
                force_import=False,
                force_rebuild=False,
                force_fts=True,
                force_chunks=False,
                force_summary=False,
                force_embeddings=False,
                no_summary=False,
            )

            with (
                patch("core.ingest.parse_args", return_value=args),
                patch("core.ingest.store.rebuild_fts") as rebuild_fts,
                patch("core.ingest.store.sync_missing_fts") as sync_missing_fts,
                redirect_stdout(io.StringIO()) as output,
                self.assertRaises(SystemExit) as exc,
            ):
                ingest.main()

        self.assertEqual(exc.exception.code, 1)
        rebuild_fts.assert_not_called()
        sync_missing_fts.assert_not_called()
        self.assertIn("未找到可构建索引的 JSON 文件", output.getvalue())

    def test_core_ingest_skip_import_fails_when_target_has_no_known_message_scope(self) -> None:
        from core import ingest

        with TemporaryDirectory() as td:
            path = Path(td) / "chat.json"
            path.write_text("{}", encoding="utf-8")
            args = Namespace(
                targets=[str(path)],
                skip_import=True,
                force_import=False,
                force_rebuild=False,
                force_fts=True,
                force_chunks=False,
                force_summary=False,
                force_embeddings=False,
                no_summary=False,
            )

            with (
                patch("core.ingest.parse_args", return_value=args),
                patch("core.ingest.index_scope_for_files", return_value=([], [])),
                patch("core.ingest.store.rebuild_fts") as rebuild_fts,
                patch("core.ingest.store.rebuild_fts_for_ingest_targets") as scoped_rebuild_fts,
                redirect_stdout(io.StringIO()) as output,
                self.assertRaises(SystemExit) as exc,
            ):
                ingest.main()

        self.assertEqual(exc.exception.code, 1)
        rebuild_fts.assert_not_called()
        scoped_rebuild_fts.assert_not_called()
        self.assertIn("已入库消息范围", output.getvalue())

    def test_core_ingest_summary_only_fails_when_target_has_no_session_chunks(self) -> None:
        from core import ingest

        with TemporaryDirectory() as td:
            path = Path(td) / "chat.json"
            path.write_text("{}", encoding="utf-8")
            args = Namespace(
                targets=[str(path)],
                skip_import=True,
                force_import=False,
                force_rebuild=False,
                force_fts=False,
                force_chunks=False,
                force_summary=True,
                force_embeddings=False,
                no_summary=False,
            )

            with (
                patch("core.ingest.parse_args", return_value=args),
                patch("core.ingest.index_scope_for_files", return_value=(["目标群"], [])),
                patch("core.ingest.load_existing_chunks") as load_chunks,
                redirect_stdout(io.StringIO()) as output,
                self.assertRaises(SystemExit) as exc,
            ):
                ingest.main()

        self.assertEqual(exc.exception.code, 1)
        load_chunks.assert_not_called()
        self.assertIn("已有会话分块", output.getvalue())

    def test_core_ingest_vector_only_fails_when_embedding_is_unconfigured(self) -> None:
        from core import ingest

        with TemporaryDirectory() as td:
            path = Path(td) / "chat.json"
            path.write_text("{}", encoding="utf-8")
            args = Namespace(
                targets=[str(path)],
                skip_import=True,
                force_import=False,
                force_rebuild=False,
                force_fts=False,
                force_chunks=False,
                force_summary=False,
                force_embeddings=True,
                no_summary=False,
            )

            with (
                patch("core.ingest.parse_args", return_value=args),
                patch("core.ingest.embed_configured", return_value=False),
                patch("core.ingest.store.get_threads_for_message_id_prefixes", return_value=["目标群"]),
                patch("core.ingest.store.get_session_ids_for_message_id_prefixes", return_value=[1]),
                patch("core.ingest.store.count_missing_fts", return_value=0),
                patch("core.ingest.store.count_messages_missing_seq", return_value=0),
                patch("core.ingest.load_existing_chunks", return_value=([object()], [1], {})) as load_chunks,
                patch("core.ingest.record_pending_files"),
                redirect_stdout(io.StringIO()) as output,
                self.assertRaises(SystemExit) as exc,
            ):
                ingest.main()

        self.assertEqual(exc.exception.code, 1)
        load_chunks.assert_called_once_with([1])
        self.assertIn("未配置 EMBED_*", output.getvalue())

    def test_core_ingest_vector_only_loads_sessions_related_to_target_json(self) -> None:
        from core import ingest

        with TemporaryDirectory() as td:
            path = Path(td) / "chat.json"
            path.write_text("{}", encoding="utf-8")
            args = Namespace(
                targets=[str(path)],
                skip_import=True,
                force_import=False,
                force_rebuild=False,
                force_fts=False,
                force_chunks=False,
                force_summary=False,
                force_embeddings=True,
                no_summary=False,
                summary_workers=1,
                summary_batch_size=1,
                summary_max_chars=3000,
                summary_fallback_chars=1200,
                embed_workers=1,
                embed_batch_size=32,
                progress_every=1,
                progress_interval=0.1,
                keep_going=False,
            )

            with (
                patch("core.ingest.parse_args", return_value=args),
                patch("core.ingest.embed_configured", return_value=True),
                patch("core.ingest.store.has_vec", return_value=True),
                patch("core.ingest.store.get_threads_for_message_id_prefixes", return_value=["目标群"]) as get_threads,
                patch("core.ingest.store.get_session_ids_for_message_id_prefixes", return_value=[7]) as get_session_ids,
                patch("core.ingest.store.count_sessions_missing_summary", return_value=0),
                patch("core.ingest.store.count_missing_fts", return_value=0),
                patch("core.ingest.store.count_messages_missing_seq", return_value=0),
                patch(
                    "core.ingest.load_existing_chunks",
                    return_value=([SimpleNamespace(text="hello")], [7], {}),
                ) as load_chunks,
                patch("core.ingest.store.get_session_ids_with_embedding", return_value=set()) as get_with_embedding,
                patch("core.ingest.embed", return_value=[[0.1, 0.2]]) as embed,
                patch("core.ingest.store.ensure_vector_table_dimension", return_value=False),
                patch("core.ingest.store.insert_embeddings") as insert_embeddings,
                patch("core.ingest.record_pending_files"),
                redirect_stdout(io.StringIO()) as output,
            ):
                ingest.main()

        get_threads.assert_called_once()
        self.assertGreaterEqual(get_session_ids.call_count, 1)
        load_chunks.assert_called_once_with([7])
        get_with_embedding.assert_called_once_with([7])
        embed.assert_called_once_with(["hello"], 32)
        insert_embeddings.assert_called_once()
        self.assertIn("目标 JSON 关联 1 个会话、1 个会话块", output.getvalue())
        self.assertIn("复用目标范围会话分块：1 个块", output.getvalue())

    def test_core_ingest_vector_only_does_not_generate_missing_summaries(self) -> None:
        from core import ingest

        with TemporaryDirectory() as td:
            path = Path(td) / "chat.json"
            path.write_text("{}", encoding="utf-8")
            args = Namespace(
                targets=[str(path)],
                skip_import=True,
                force_import=False,
                force_rebuild=False,
                force_fts=False,
                force_chunks=False,
                force_summary=False,
                force_embeddings=True,
                no_summary=False,
                summary_workers=1,
                summary_batch_size=1,
                summary_max_chars=3000,
                summary_fallback_chars=1200,
                embed_workers=1,
                embed_batch_size=32,
                progress_every=1,
                progress_interval=0.1,
                keep_going=False,
            )

            with (
                patch.dict(os.environ, {"SUMMARY_MODEL": "summary-model"}, clear=False),
                patch("core.ingest.parse_args", return_value=args),
                patch("core.ingest.chat_config_status", return_value={"configured": True, "missing": [], "model": "summary-model"}),
                patch("core.ingest.embed_configured", return_value=True),
                patch("core.ingest.store.has_vec", return_value=True),
                patch("core.ingest.store.get_threads_for_message_id_prefixes", return_value=["目标群"]),
                patch("core.ingest.store.get_session_ids_for_message_id_prefixes", return_value=[7]),
                patch(
                    "core.ingest.store.get_session_index_status",
                    return_value={"total": 1, "missing_summary": 1, "missing_embedding": 1},
                ),
                patch("core.ingest.store.get_session_ids_without_embedding", return_value=[7]),
                patch("core.ingest.store.count_missing_fts_for_ingest_targets", return_value=0),
                patch("core.ingest.store.count_messages_missing_seq_for_ingest_targets", return_value=0),
                patch(
                    "core.ingest.load_existing_chunks",
                    return_value=([SimpleNamespace(text="hello")], [7], {}),
                ),
                patch("core.ingest.summarize_batch") as summarize_batch,
                patch("core.ingest.store.set_summaries") as set_summaries,
                patch("core.ingest.store.get_session_ids_with_embedding", return_value=set()),
                patch("core.ingest.embed", return_value=[[0.1, 0.2]]) as embed,
                patch("core.ingest.store.ensure_vector_table_dimension", return_value=False),
                patch("core.ingest.store.insert_embeddings"),
                patch("core.ingest.record_pending_files"),
                redirect_stdout(io.StringIO()) as output,
            ):
                ingest.main()

        summarize_batch.assert_not_called()
        set_summaries.assert_not_called()
        embed.assert_called_once_with(["hello"], 32)
        self.assertNotIn("准备生成 1 个摘要", output.getvalue())

    def test_core_ingest_incremental_repair_scopes_missing_vectors_to_target_json(self) -> None:
        from core import ingest

        with TemporaryDirectory() as td:
            path = Path(td) / "chat.json"
            path.write_text("{}", encoding="utf-8")
            args = Namespace(
                targets=[str(path)],
                skip_import=False,
                force_import=False,
                force_rebuild=False,
                force_fts=False,
                force_chunks=False,
                force_summary=False,
                force_embeddings=False,
                no_summary=False,
                summary_workers=1,
                summary_batch_size=1,
                summary_max_chars=3000,
                summary_fallback_chars=1200,
                embed_workers=1,
                embed_batch_size=32,
                progress_every=1,
                progress_interval=0.1,
                keep_going=False,
            )

            with (
                patch("core.ingest.parse_args", return_value=args),
                patch("core.ingest.ingest_files", return_value=(0, 0, 0, set(), 1, 0)),
                patch("core.ingest.embed_configured", return_value=True),
                patch("core.ingest.store.has_vec", return_value=True),
                patch("core.ingest.store.get_threads_for_message_id_prefixes", return_value=["目标群"]),
                patch("core.ingest.store.get_session_ids_for_message_id_prefixes", return_value=[7]) as get_session_ids,
                patch("core.ingest.store.count_sessions_missing_summary", return_value=0),
                patch("core.ingest.store.get_all_session_ids_without_embedding", return_value=[7, 8]),
                patch("core.ingest.store.count_missing_fts", return_value=0),
                patch("core.ingest.store.count_messages_missing_seq", return_value=0),
                patch(
                    "core.ingest.load_existing_chunks",
                    return_value=([SimpleNamespace(text="target chunk")], [7], {}),
                ) as load_chunks,
                patch("core.ingest.store.get_session_ids_with_embedding", return_value=set()) as get_with_embedding,
                patch("core.ingest.embed", return_value=[[0.1, 0.2]]) as embed,
                patch("core.ingest.store.ensure_vector_table_dimension", return_value=False),
                patch("core.ingest.store.insert_embeddings") as insert_embeddings,
                patch("core.ingest.record_pending_files"),
                redirect_stdout(io.StringIO()) as output,
            ):
                ingest.main()

        self.assertGreaterEqual(get_session_ids.call_count, 1)
        load_chunks.assert_called_once_with([7])
        get_with_embedding.assert_called_once_with([7])
        embed.assert_called_once_with(["target chunk"], 32)
        insert_embeddings.assert_called_once()
        self.assertIn("复用目标范围会话分块：1 个块", output.getvalue())

    def test_core_ingest_incremental_repair_scopes_missing_seq_to_target_json_threads(self) -> None:
        from core import ingest

        with TemporaryDirectory() as td:
            path = Path(td) / "chat.json"
            path.write_text("{}", encoding="utf-8")
            args = Namespace(
                targets=[str(path)],
                skip_import=False,
                force_import=False,
                force_rebuild=False,
                force_fts=False,
                force_chunks=False,
                force_summary=False,
                force_embeddings=False,
                no_summary=False,
                summary_workers=1,
                summary_batch_size=1,
                summary_max_chars=3000,
                summary_fallback_chars=1200,
                embed_workers=1,
                embed_batch_size=32,
                progress_every=1,
                progress_interval=0.1,
                keep_going=False,
            )

            with (
                patch.dict(os.environ, {"SUMMARY_MODEL": ""}, clear=False),
                patch("core.ingest.parse_args", return_value=args),
                patch("core.ingest.ingest_files", return_value=(0, 0, 0, set(), 1, 0)),
                patch("core.ingest.index_scope_for_files", return_value=(["目标群"], [7])),
                patch("core.ingest.embed_configured", return_value=False),
                patch("core.ingest.store.has_vec", return_value=False),
                patch("core.ingest.store.count_sessions_missing_summary", return_value=0),
                patch("core.ingest.store.count_missing_fts_for_ingest_targets", return_value=0),
                patch("core.ingest.store.count_messages_missing_seq_for_ingest_targets", return_value=2) as scoped_missing_seq,
                patch("core.ingest.store.count_messages_missing_seq") as global_missing_seq,
                patch("core.ingest.store.recompute_message_sequence") as recompute_sequence,
                patch("core.ingest.store.sync_missing_fts_for_ingest_targets", return_value=0),
                patch("core.ingest.store.get_all_session_ids_without_embedding", return_value=[]),
                patch("core.ingest.record_pending_files"),
                redirect_stdout(io.StringIO()) as output,
            ):
                ingest.main()

        scoped_missing_seq.assert_called_once_with(
            [str(path.resolve())],
            [f"{ingest.file_scope_for_path(path)}:"],
        )
        global_missing_seq.assert_not_called()
        recompute_sequence.assert_called_once_with(["目标群"])
        self.assertIn("seq 缺失 2 条", output.getvalue())

    def test_core_ingest_summary_only_fails_when_summary_is_unconfigured(self) -> None:
        from core import ingest

        with TemporaryDirectory() as td:
            path = Path(td) / "chat.json"
            path.write_text("{}", encoding="utf-8")
            args = Namespace(
                targets=[str(path)],
                skip_import=True,
                force_import=False,
                force_rebuild=False,
                force_fts=False,
                force_chunks=False,
                force_summary=True,
                force_embeddings=False,
                no_summary=False,
            )

            with (
                patch("core.ingest.parse_args", return_value=args),
                patch.dict(os.environ, {"SUMMARY_MODEL": ""}, clear=False),
                patch("core.ingest.embed_configured", return_value=False),
                patch("core.ingest.store.get_threads_for_message_id_prefixes", return_value=["目标群"]),
                patch("core.ingest.store.get_session_ids_for_message_id_prefixes", return_value=[1]),
                patch("core.ingest.store.count_missing_fts", return_value=0),
                patch("core.ingest.store.count_messages_missing_seq", return_value=0),
                patch("core.ingest.load_existing_chunks", return_value=([object()], [1], {})) as load_chunks,
                redirect_stdout(io.StringIO()) as output,
                self.assertRaises(SystemExit) as exc,
            ):
                ingest.main()

        self.assertEqual(exc.exception.code, 1)
        load_chunks.assert_called_once_with([1])
        self.assertIn("未配置 SUMMARY_MODEL", output.getvalue())

    def test_core_ingest_records_imported_file_before_model_error_exit(self) -> None:
        from core import ingest

        with TemporaryDirectory() as td:
            path = Path(td) / "chat.json"
            path.write_text("{}", encoding="utf-8")
            stat = path.stat()
            record = (str(path.resolve()), stat.st_size, stat.st_mtime_ns, 1, 1, 1)
            args = Namespace(
                targets=[str(path)],
                skip_import=False,
                force_import=False,
                force_rebuild=False,
                force_fts=False,
                force_chunks=False,
                force_summary=False,
                force_embeddings=False,
                no_summary=False,
                summary_workers=1,
                summary_batch_size=1,
                summary_max_chars=3000,
                summary_fallback_chars=1200,
                embed_workers=1,
                embed_batch_size=32,
                progress_every=1,
                progress_interval=0.1,
                keep_going=False,
            )

            def ingest_files_side_effect(_files, pending_file_records, force_import=False):
                pending_file_records.append(record)
                return 1, 1, 0, {"目标群"}, 1, 0

            with ExitStack() as stack:
                stack.enter_context(
                    patch.dict(
                        os.environ,
                        {
                            "CHAT_BASE_URL": "https://llm.local/v1",
                            "CHAT_API_KEY": "sk-summary-test",
                            "SUMMARY_MODEL": "summary-model",
                        },
                        clear=True,
                    )
                )
                stack.enter_context(patch("core.ingest.parse_args", return_value=args))
                stack.enter_context(patch("core.ingest.ingest_files", side_effect=ingest_files_side_effect))
                stack.enter_context(patch("core.ingest.index_scope_for_files", return_value=(["目标群"], [7])))
                stack.enter_context(patch("core.ingest.embed_configured", return_value=True))
                stack.enter_context(patch("core.ingest.store.has_vec", return_value=True))
                stack.enter_context(
                    patch(
                        "core.ingest.store.get_session_index_status",
                        return_value={"total": 1, "missing_summary": 1, "missing_embedding": 0},
                    )
                )
                stack.enter_context(patch("core.ingest.store.get_session_ids_without_embedding", return_value=[]))
                stack.enter_context(patch("core.ingest.store.count_missing_fts_for_ingest_targets", return_value=0))
                stack.enter_context(patch("core.ingest.store.count_messages_missing_seq_for_ingest_targets", return_value=0))
                stack.enter_context(patch("core.ingest.store.recompute_message_sequence"))
                stack.enter_context(patch("core.ingest.store.sync_missing_fts_for_ingest_targets", return_value=1))
                stack.enter_context(patch("core.ingest.rebuild_chunks_scoped"))
                stack.enter_context(patch("core.ingest.store.stats", return_value={"total_messages": 1}))
                stack.enter_context(
                    patch(
                        "core.ingest.load_existing_chunks",
                        return_value=([SimpleNamespace(text="hello")], [7], {}),
                    )
                )
                stack.enter_context(patch("core.ingest.store.get_session_ids_with_embedding", return_value={7}))
                stack.enter_context(
                    patch(
                        "core.ingest.summarize_batch",
                        side_effect=RuntimeError("api_key=sk-summary-secret-123456 failed"),
                    )
                )
                stack.enter_context(patch("core.ingest.store.set_summaries"))
                record_pending = stack.enter_context(patch("core.ingest.record_pending_files"))
                output = stack.enter_context(redirect_stdout(io.StringIO()))
                with self.assertRaises(SystemExit) as exc:
                    ingest.main()

        self.assertEqual(exc.exception.code, 1)
        record_pending.assert_called_once()
        self.assertEqual(record_pending.call_args.args[0], [record])
        self.assertIn("摘要生成 失败", output.getvalue())
        self.assertIn("[redacted]", output.getvalue())
        self.assertNotIn("sk-summary-secret", output.getvalue())

    def test_core_ingest_summary_uses_explicit_summary_model_without_default_chat_model(self) -> None:
        from core import ingest

        with TemporaryDirectory() as td:
            path = Path(td) / "chat.json"
            path.write_text("{}", encoding="utf-8")
            args = Namespace(
                targets=[str(path)],
                skip_import=True,
                force_import=False,
                force_rebuild=False,
                force_fts=False,
                force_chunks=False,
                force_summary=True,
                force_embeddings=False,
                no_summary=False,
                summary_workers=1,
                summary_batch_size=1,
                summary_max_chars=3000,
                summary_fallback_chars=1200,
                embed_workers=1,
                embed_batch_size=32,
                progress_every=1,
                progress_interval=0.1,
                keep_going=False,
            )

            with (
                patch.dict(
                    os.environ,
                    {
                        "CHAT_BASE_URL": "https://llm.local/v1",
                        "CHAT_API_KEY": "sk-summary-test",
                        "SUMMARY_MODEL": "summary-model",
                    },
                    clear=True,
                ),
                patch("core.ingest.parse_args", return_value=args),
                patch("core.ingest.embed_configured", return_value=False),
                patch("core.ingest.store.get_threads_for_message_id_prefixes", return_value=["目标群"]),
                patch("core.ingest.store.get_session_ids_for_message_id_prefixes", return_value=[1]),
                patch("core.ingest.store.count_sessions_missing_summary", return_value=0),
                patch("core.ingest.store.count_missing_fts", return_value=0),
                patch("core.ingest.store.count_messages_missing_seq", return_value=0),
                patch("core.ingest.load_existing_chunks", return_value=([SimpleNamespace(text="hello")], [1], {})) as load_chunks,
                patch("core.ingest.summarize_batch", return_value=({0: "summary"}, {}, {})) as summarize_batch,
                patch("core.ingest.store.set_summaries") as set_summaries,
                patch("core.ingest.record_pending_files"),
                redirect_stdout(io.StringIO()) as output,
            ):
                ingest.main()

        load_chunks.assert_called_once_with([1])
        summarize_batch.assert_called_once()
        self.assertEqual(summarize_batch.call_args.args[0], "summary-model")
        set_summaries.assert_any_call([(1, "summary")])
        self.assertIn("生成摘要前缀（summary-model", output.getvalue())

    def test_collect_json_files_recurses_and_returns_stable_json_only_order(self) -> None:
        from core import ingest

        with TemporaryDirectory() as td:
            root = Path(td)
            (root / "b").mkdir()
            (root / "a").mkdir()
            nested = root / "a" / "nested.json"
            top = root / "top.JSON"
            ignored = root / "b" / "ignored.txt"
            nested.write_text("{}", encoding="utf-8")
            top.write_text("{}", encoding="utf-8")
            ignored.write_text("{}", encoding="utf-8")

            files = ingest.collect_json_files(root)

        self.assertEqual([path.name for path in files], ["nested.json", "top.JSON"])

    def test_collect_json_files_skips_paths_resolving_outside_target_directory(self) -> None:
        from core import ingest

        with TemporaryDirectory() as td:
            root = Path(td) / "local"
            root.mkdir()
            good = root / "good.json"
            outside_link = root / "outside.json"
            outside_target = Path(td) / "outside.json"
            good.write_text("{}", encoding="utf-8")
            outside_link.write_text("{}", encoding="utf-8")
            outside_target.write_text("{}", encoding="utf-8")
            original_resolve = Path.resolve

            def resolve_side_effect(path: Path, *args, **kwargs):
                if path == outside_link:
                    return outside_target
                return original_resolve(path, *args, **kwargs)

            with patch.object(Path, "resolve", resolve_side_effect):
                files = ingest.collect_json_files(root)

        self.assertEqual(files, [good])

    def _run_ingest_files_smoke(self, ingest_module, progress_enabled: bool) -> str:
        class WriteResult:
            inserted = 1
            updated = 0
            changed = 1
            threads = {"mock-thread"}

        class ParseResult:
            total = 2
            included = 2
            thread = "mock-thread"
            messages = []
            skipped_by_type = {}

        with TemporaryDirectory() as td:
            root = Path(td)
            files = []
            for index in range(2):
                path = root / f"chat-{index + 1}.json"
                encoding = "utf-8-sig" if index == 0 else "utf-8"
                path.write_text(json.dumps({"weflow": {"id": index}}), encoding=encoding)
                files.append(path)

            env = {"INGEST_PROGRESS_JSON": "true"} if progress_enabled else {}
            with patch.dict(os.environ, env, clear=False):
                if not progress_enabled:
                    os.environ.pop("INGEST_PROGRESS_JSON", None)
                with (
                    patch("core.ingest.store.ingest_file_unchanged", return_value=False),
                    patch("core.ingest.is_weflow_export", return_value=True),
                    patch("core.ingest.parse_weflow", return_value=ParseResult()),
                    patch("core.ingest.store.upsert_messages", return_value=WriteResult()),
                    redirect_stdout(io.StringIO()) as output,
                ):
                    ingest_module.ingest_files(files, [])
                    return output.getvalue()


if __name__ == "__main__":
    unittest.main()
