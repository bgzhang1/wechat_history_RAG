from __future__ import annotations

import json
import unittest
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from pydantic import ValidationError

from backend.routers import ingest


class IngestRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.local_root = Path(self._tmp.name) / "local"
        self.upload_root = self.local_root / "uploads"
        self.local_root.mkdir()
        self.upload_root.mkdir()
        self._old_local_root = ingest.LOCAL_ROOT
        self._old_upload_root = ingest.UPLOAD_ROOT
        ingest.LOCAL_ROOT = self.local_root
        ingest.UPLOAD_ROOT = self.upload_root
        with ingest._tasks_lock:
            self._old_tasks = dict(ingest._tasks)
            ingest._tasks.clear()

    def tearDown(self) -> None:
        with ingest._tasks_lock:
            ingest._tasks.clear()
            ingest._tasks.update(self._old_tasks)
        ingest.LOCAL_ROOT = self._old_local_root
        ingest.UPLOAD_ROOT = self._old_upload_root
        self._tmp.cleanup()

    def test_resolve_local_file_id_rejects_traversal_and_non_json(self) -> None:
        good = self.local_root / "data.json"
        good.write_text("{}", encoding="utf-8")
        txt = self.local_root / "notes.txt"
        txt.write_text("not json", encoding="utf-8")
        json_dir = self.local_root / "folder.json"
        json_dir.mkdir()

        self.assertEqual(ingest._resolve_local_file_id("data.json"), good.resolve())

        with self.assertRaises(HTTPException) as traversal:
            ingest._resolve_local_file_id("../outside.json")
        self.assertEqual(traversal.exception.status_code, 400)

        with self.assertRaises(HTTPException) as non_json:
            ingest._resolve_local_file_id("notes.txt")
        self.assertEqual(non_json.exception.status_code, 400)

        with self.assertRaises(HTTPException) as directory:
            ingest._resolve_local_file_id("folder.json")
        self.assertEqual(directory.exception.status_code, 400)
        self.assertIn("文件", str(directory.exception.detail))

    def test_resolve_legacy_local_path_allows_directories_but_rejects_non_json_files(self) -> None:
        data_dir = self.local_root / "data"
        data_dir.mkdir()
        txt = self.local_root / "notes.txt"
        txt.write_text("not json", encoding="utf-8")

        self.assertEqual(ingest._resolve_local_path("data"), data_dir.resolve())

        with self.assertRaises(HTTPException) as non_json:
            ingest._resolve_local_path("notes.txt")
        self.assertEqual(non_json.exception.status_code, 400)

    def test_start_ingest_rejects_empty_directory_before_creating_task(self) -> None:
        data_dir = self.local_root / "data"
        data_dir.mkdir()

        with self.assertRaises(HTTPException) as exc:
            ingest.start_ingest(ingest.IngestStartRequest(file_path="data", mode="full"))

        self.assertEqual(exc.exception.status_code, 400)
        self.assertIn(".json", str(exc.exception.detail))
        self.assertEqual(ingest._tasks, {})

    def test_parse_progress_event_clamps_and_truncates(self) -> None:
        event = ingest._parse_progress_event(
            f"{ingest.PROGRESS_PREFIX}"
            '{"stage":"embedding","progress":150,"message":"'
            + "x" * 600
            + '"}'
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event["stage"], "embedding")
        self.assertEqual(event["progress"], 100)
        self.assertEqual(len(event["message"]), 500)
        self.assertIsNone(ingest._parse_progress_event("__INGEST_PROGRESS__ not json"))

    def test_full_mode_forces_json_reparse_without_rebuild_flags(self) -> None:
        self.assertEqual(ingest._mode_args("full"), ["--force-import"])
        self.assertEqual(ingest._mode_args("incremental"), [])
        self.assertEqual(ingest._mode_args("rebuild"), ["--force-rebuild"])
        self.assertIn("按需解析", ingest._start_message("incremental", self.local_root / "chat.json"))
        self.assertIn("重新解析该 JSON", ingest._start_message("full", self.local_root / "chat.json"))
        self.assertIn("强制重建任务已启动", ingest._start_message("rebuild", self.local_root / "chat.json"))
        self.assertIn("该 JSON", ingest._start_message("rebuild", self.local_root / "chat.json"))
        self.assertIn("关联索引", ingest._start_message("rebuild", self.local_root / "chat.json"))
        self.assertIn("该 JSON 关联消息", ingest._start_message("fts", self.local_root / "chat.json"))
        self.assertIn("不会调用模型或 embedding", ingest._start_message("fts", self.local_root / "chat.json"))

    def test_upload_file_persists_safe_json_and_list_uses_original_display_name(self) -> None:
        upload = UploadFile(
            file=BytesIO(b'{"weflow":{},"session":{},"messages":[]}'),
            filename="../My Chat Export.json",
        )

        response = ingest.upload_file(upload)

        stored = self.upload_root / f"{response.upload_id}.json"
        self.assertTrue(stored.exists())
        self.assertEqual(stored.read_bytes(), b'{"weflow":{},"session":{},"messages":[]}')
        self.assertEqual(response.filename, "My Chat Export.json")
        self.assertEqual(list(self.upload_root.glob("*.uploading")), [])

        with (
            patch("backend.routers.ingest.store.get_ingest_file_records", return_value={}),
            patch(
                "backend.routers.ingest._index_status_for_file",
                return_value={"total": 3, "missing_summary": 1, "missing_embedding": 2},
            ),
        ):
            listed = ingest.list_files(limit=100, offset=0)

        self.assertEqual(listed["total_count"], 1)
        item = listed["items"][0]
        self.assertEqual(item.filename, "My Chat Export.json")
        self.assertEqual(item.source, "upload")
        self.assertEqual(item.upload_id, response.upload_id)
        self.assertEqual(item.ingest_status, "never")
        self.assertIsNone(item.session_chunks)
        self.assertIsNone(item.missing_summary_chunks)
        self.assertIsNone(item.missing_vector_chunks)

    def test_upload_file_accepts_utf8_bom_weflow_json(self) -> None:
        payload = b'\xef\xbb\xbf{"weflow":{},"session":{},"messages":[]}'
        upload = UploadFile(file=BytesIO(payload), filename="bom.json")

        response = ingest.upload_file(upload)

        stored = self.upload_root / f"{response.upload_id}.json"
        self.assertEqual(stored.read_bytes(), payload)
        self.assertEqual(response.filename, "bom.json")

    def test_upload_file_accepts_utf16_bom_weflow_json_like_cli_ingest(self) -> None:
        payload = '{"weflow":{},"session":{},"messages":[]}'.encode("utf-16")
        upload = UploadFile(file=BytesIO(payload), filename="utf16.json")

        response = ingest.upload_file(upload)

        stored = self.upload_root / f"{response.upload_id}.json"
        self.assertEqual(stored.read_bytes(), payload)
        self.assertEqual(response.filename, "utf16.json")

    def test_upload_file_validates_json_without_reading_entire_temp_file_as_bytes(self) -> None:
        upload = UploadFile(
            file=BytesIO(b'{"weflow":{},"session":{},"messages":[]}'),
            filename="large-chat.json",
        )

        with patch.object(Path, "read_bytes", side_effect=AssertionError("read_bytes should not validate uploads")):
            response = ingest.upload_file(upload)

        stored = self.upload_root / f"{response.upload_id}.json"
        self.assertTrue(stored.exists())
        self.assertEqual(response.filename, "large-chat.json")

    def test_upload_file_accepts_common_top_level_message_list_alias(self) -> None:
        payload = b'{"weflow":{},"session":{},"messageList":[]}'
        upload = UploadFile(file=BytesIO(payload), filename="message-list.json")

        response = ingest.upload_file(upload)

        stored = self.upload_root / f"{response.upload_id}.json"
        self.assertEqual(stored.read_bytes(), payload)
        self.assertEqual(response.filename, "message-list.json")

    def test_upload_file_records_stable_scope_for_reuploaded_same_chat(self) -> None:
        first_payload = json.dumps(
            {
                "weflow": {},
                "session": {"displayName": "项目群", "wxid": "room-123@chatroom"},
                "messages": [
                    {
                        "type": "文本消息",
                        "content": "first export",
                        "createTime": 1_700_000_000,
                        "platformMessageId": "m-1",
                    }
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8")
        second_payload = json.dumps(
            {
                "weflow": {},
                "session": {"displayName": "项目群 renamed", "wxid": "room-123@chatroom"},
                "messages": [
                    {
                        "type": "文本消息",
                        "content": "first export",
                        "createTime": 1_700_000_000,
                        "platformMessageId": "m-1",
                    },
                    {
                        "type": "文本消息",
                        "content": "new message",
                        "createTime": 1_700_000_001,
                        "platformMessageId": "m-2",
                    },
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8")

        first = ingest.upload_file(UploadFile(file=BytesIO(first_payload), filename="project-a.json"))
        second = ingest.upload_file(UploadFile(file=BytesIO(second_payload), filename="project-renamed.json"))

        first_meta = json.loads((self.upload_root / f"{first.upload_id}.json.meta").read_text(encoding="utf-8"))
        second_meta = json.loads((self.upload_root / f"{second.upload_id}.json.meta").read_text(encoding="utf-8"))

        self.assertNotEqual(first.upload_id, second.upload_id)
        self.assertTrue(first_meta["scope"].startswith("uploads/"))
        self.assertEqual(first_meta["scope"], second_meta["scope"])

    def test_upload_file_records_stable_scope_for_appended_export_without_wxid(self) -> None:
        def payload(message_id: str, content: str) -> bytes:
            return json.dumps(
                {
                    "weflow": {},
                    "session": {"displayName": "无 wxid 项目群"},
                    "messages": [
                        {
                            "type": "文本消息",
                            "content": content,
                            "createTime": 1_700_000_000,
                            "platformMessageId": message_id,
                        }
                    ],
                },
                ensure_ascii=False,
            ).encode("utf-8")

        first = ingest.upload_file(UploadFile(file=BytesIO(payload("m-1", "first")), filename="first.json"))
        appended = ingest.upload_file(UploadFile(file=BytesIO(payload("m-99", "appended")), filename="renamed.json"))

        first_meta = json.loads((self.upload_root / f"{first.upload_id}.json.meta").read_text(encoding="utf-8"))
        appended_meta = json.loads((self.upload_root / f"{appended.upload_id}.json.meta").read_text(encoding="utf-8"))

        self.assertNotEqual(first.upload_id, appended.upload_id)
        self.assertEqual(first_meta["scope"], appended_meta["scope"])

    def test_upload_file_records_different_scopes_for_different_chats(self) -> None:
        def payload(wxid: str) -> bytes:
            return json.dumps(
                {
                    "weflow": {},
                    "session": {"displayName": "项目群", "wxid": wxid},
                    "messages": [
                        {
                            "type": "文本消息",
                            "content": "same platform id in different chats",
                            "createTime": 1_700_000_000,
                            "platformMessageId": "m-1",
                        }
                    ],
                },
                ensure_ascii=False,
            ).encode("utf-8")

        first = ingest.upload_file(UploadFile(file=BytesIO(payload("room-a@chatroom")), filename="chat.json"))
        second = ingest.upload_file(UploadFile(file=BytesIO(payload("room-b@chatroom")), filename="chat.json"))

        first_meta = json.loads((self.upload_root / f"{first.upload_id}.json.meta").read_text(encoding="utf-8"))
        second_meta = json.loads((self.upload_root / f"{second.upload_id}.json.meta").read_text(encoding="utf-8"))

        self.assertNotEqual(first_meta["scope"], second_meta["scope"])

    def test_upload_file_preserves_json_suffix_when_display_name_is_long(self) -> None:
        upload = UploadFile(
            file=BytesIO(b'{"weflow":{},"session":{},"messages":[]}'),
            filename=f"{'long-name-' * 40}.json",
        )

        response = ingest.upload_file(upload)

        self.assertLessEqual(len(response.filename), 240)
        self.assertTrue(response.filename.endswith(".json"))
        self.assertTrue((self.upload_root / f"{response.upload_id}.json").exists())

    def test_upload_file_rejects_empty_or_invalid_json_without_leaving_files(self) -> None:
        cases = [
            UploadFile(file=BytesIO(b""), filename="empty.json"),
            UploadFile(file=BytesIO(b"{not-json"), filename="broken.json"),
            UploadFile(file=BytesIO(b"\xff\xfe\x00\x00"), filename="bad-encoding.json"),
            UploadFile(file=BytesIO(b'{"sessions": []}'), filename="not-weflow.json"),
        ]

        for upload in cases:
            with self.subTest(filename=upload.filename):
                with self.assertRaises(HTTPException) as exc:
                    ingest.upload_file(upload)

                self.assertEqual(exc.exception.status_code, 400)

        self.assertEqual(list(self.upload_root.glob("*.json")), [])
        self.assertEqual(list(self.upload_root.glob("*.meta")), [])
        self.assertEqual(list(self.upload_root.glob("*.uploading")), [])

    def test_upload_file_cleans_json_when_display_meta_write_fails(self) -> None:
        upload = UploadFile(
            file=BytesIO(b'{"weflow":{},"session":{},"messages":[]}'),
            filename="chat.json",
        )

        with (
            patch("backend.routers.ingest._write_upload_meta", side_effect=OSError("disk full")),
            self.assertRaises(HTTPException) as exc,
        ):
            ingest.upload_file(upload)

        self.assertEqual(exc.exception.status_code, 500)
        self.assertIn("上传文件保存失败", str(exc.exception.detail))
        self.assertEqual(list(self.upload_root.glob("*.json")), [])
        self.assertEqual(list(self.upload_root.glob("*.meta")), [])
        self.assertEqual(list(self.upload_root.glob("*.uploading")), [])

    def test_list_files_ignores_in_progress_upload_temp_files(self) -> None:
        temp_upload = self.upload_root / "11111111-1111-1111-1111-111111111111.json.uploading"
        temp_upload.write_text('{"weflow":{},"session":{},"messages":[]}', encoding="utf-8")
        stable = self.local_root / "stable.json"
        stable.write_text('{"weflow":{},"session":{},"messages":[]}', encoding="utf-8")

        with patch("backend.routers.ingest.store.get_ingest_file_records", return_value={}):
            listed = ingest.list_files(limit=100, offset=0)

        self.assertEqual(listed["total_count"], 1)
        self.assertEqual(listed["returned"], 1)
        self.assertEqual(listed["items"][0].file_id, "stable.json")

    def test_resolve_upload_rejects_file_that_resolves_outside_local_root(self) -> None:
        upload_id = "11111111-1111-1111-1111-111111111111"
        uploaded = self.upload_root / f"{upload_id}.json"
        outside = Path(self._tmp.name) / "outside.json"
        uploaded.write_text("{}", encoding="utf-8")
        outside.write_text("{}", encoding="utf-8")
        original_resolve = Path.resolve

        def resolve_side_effect(path: Path, *args, **kwargs):
            if path == uploaded:
                return outside
            return original_resolve(path, *args, **kwargs)

        with patch.object(Path, "resolve", resolve_side_effect):
            with self.assertRaises(HTTPException) as exc:
                ingest._resolve_upload(upload_id)

        self.assertEqual(exc.exception.status_code, 400)
        self.assertIn("无效", str(exc.exception.detail))

    def test_list_files_skips_json_files_that_disappear_during_scan(self) -> None:
        good = self.local_root / "good.json"
        bad = self.local_root / "bad.json"
        good.write_text("{}", encoding="utf-8")
        bad.write_text("{}", encoding="utf-8")
        original_is_file = Path.is_file
        original_stat = Path.stat

        def is_file_side_effect(path: Path) -> bool:
            if path.name == "bad.json":
                return True
            return original_is_file(path)

        def stat_side_effect(path: Path, *args, **kwargs):
            if path.name == "bad.json":
                raise OSError("file disappeared")
            return original_stat(path, *args, **kwargs)

        with (
            patch.object(Path, "is_file", is_file_side_effect),
            patch.object(Path, "stat", stat_side_effect),
            patch("backend.routers.ingest.store.get_ingest_file_records", return_value={}),
        ):
            listed = ingest.list_files(limit=100, offset=0)

        self.assertEqual(listed["total_count"], 1)
        self.assertEqual(listed["returned"], 1)
        self.assertEqual(listed["items"][0].file_id, "good.json")

    def test_list_files_skips_paths_that_resolve_outside_local_root(self) -> None:
        good = self.local_root / "good.json"
        outside_link = self.local_root / "outside.json"
        outside_target = Path(self._tmp.name) / "outside.json"
        good.write_text("{}", encoding="utf-8")
        outside_link.write_text("{}", encoding="utf-8")
        outside_target.write_text("{}", encoding="utf-8")
        original_resolve = Path.resolve

        def resolve_side_effect(path: Path, *args, **kwargs):
            if path == outside_link:
                return outside_target
            return original_resolve(path, *args, **kwargs)

        with (
            patch.object(Path, "resolve", resolve_side_effect),
            patch("backend.routers.ingest.store.get_ingest_file_records", return_value={}),
        ):
            listed = ingest.list_files(limit=100, offset=0)

        self.assertEqual(listed["total_count"], 1)
        self.assertEqual(listed["returned"], 1)
        self.assertEqual(listed["items"][0].file_id, "good.json")

    def test_start_ingest_rejects_index_only_mode_for_unsynced_file(self) -> None:
        path = self.local_root / "chat.json"
        path.write_text("{}", encoding="utf-8")

        with self.assertRaises(HTTPException) as exc:
            ingest.start_ingest(ingest.IngestStartRequest(file_id="chat.json", mode="fts"))

        self.assertEqual(exc.exception.status_code, 400)
        self.assertIn("单项索引构建", str(exc.exception.detail))
        self.assertEqual(ingest._tasks, {})

    def test_start_ingest_rejects_index_only_mode_for_stale_parser_record(self) -> None:
        path = self.local_root / "chat.json"
        path.write_text("{}", encoding="utf-8")
        stat = path.stat()
        file_key = str(path.resolve())

        with (
            patch(
                "backend.routers.ingest.store.get_ingest_file_records",
                return_value={
                    file_key: {
                        "path": file_key,
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "parser_version": ingest.PARSER_VERSION - 1,
                    }
                },
            ),
            self.assertRaises(HTTPException) as exc,
        ):
            ingest.start_ingest(ingest.IngestStartRequest(file_id="chat.json", mode="fts"))

        self.assertEqual(exc.exception.status_code, 400)
        self.assertIn("单项索引构建", str(exc.exception.detail))
        self.assertEqual(ingest._tasks, {})

    def test_start_ingest_rejects_index_only_mode_without_known_file_scope(self) -> None:
        path = self.local_root / "chat.json"
        path.write_text("{}", encoding="utf-8")
        stat = path.stat()
        file_key = str(path.resolve())

        with (
            patch(
                "backend.routers.ingest.store.get_ingest_file_records",
                return_value={
                    file_key: {
                        "path": file_key,
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "parser_version": ingest.PARSER_VERSION,
                    }
                },
            ),
            patch("backend.routers.ingest.store.ingest_file_message_mapping_exists", return_value=False),
            patch("backend.routers.ingest.store.get_threads_for_message_id_prefixes", return_value=[]),
            self.assertRaises(HTTPException) as exc,
        ):
            ingest.start_ingest(ingest.IngestStartRequest(file_id="chat.json", mode="summary"))

        self.assertEqual(exc.exception.status_code, 400)
        self.assertIn("来源映射", str(exc.exception.detail))
        self.assertEqual(ingest._tasks, {})

    def test_start_ingest_rejects_summary_or_vector_mode_without_session_chunks(self) -> None:
        path = self.local_root / "chat.json"
        path.write_text("{}", encoding="utf-8")
        stat = path.stat()
        file_key = str(path.resolve())

        with (
            patch(
                "backend.routers.ingest.store.get_ingest_file_records",
                return_value={
                    file_key: {
                        "path": file_key,
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "parser_version": ingest.PARSER_VERSION,
                    }
                },
            ),
            patch("backend.routers.ingest.store.ingest_file_message_mapping_exists", return_value=True),
            patch(
                "backend.routers.ingest._index_status_for_file",
                return_value={"total": 0, "missing_summary": 0, "missing_embedding": 0},
            ),
            self.assertRaises(HTTPException) as exc,
        ):
            ingest.start_ingest(ingest.IngestStartRequest(file_id="chat.json", mode="embeddings"))

        self.assertEqual(exc.exception.status_code, 400)
        self.assertIn("会话分块", str(exc.exception.detail))
        self.assertEqual(ingest._tasks, {})

    def test_start_ingest_requires_exactly_one_target(self) -> None:
        path = self.local_root / "chat.json"
        path.write_text("{}", encoding="utf-8")

        with self.assertRaises(HTTPException) as missing:
            ingest.start_ingest(ingest.IngestStartRequest(mode="full"))
        self.assertEqual(missing.exception.status_code, 400)
        self.assertIn("只提供", str(missing.exception.detail))

        with self.assertRaises(HTTPException) as multiple:
            ingest.start_ingest(ingest.IngestStartRequest(file_id="chat.json", file_path="chat.json", mode="full"))
        self.assertEqual(multiple.exception.status_code, 400)
        self.assertIn("只提供", str(multiple.exception.detail))
        self.assertEqual(ingest._tasks, {})

    def test_start_ingest_treats_blank_targets_as_missing(self) -> None:
        with self.assertRaises(HTTPException) as missing:
            ingest.start_ingest(ingest.IngestStartRequest(file_id="   ", mode="full"))

        self.assertEqual(missing.exception.status_code, 400)
        self.assertIn("只提供", str(missing.exception.detail))
        self.assertEqual(ingest._tasks, {})

    def test_start_ingest_rejects_unbounded_target_fields_before_path_resolution(self) -> None:
        with self.assertRaises(ValidationError):
            ingest.IngestStartRequest(file_id="x" * (ingest.MAX_INGEST_TARGET_CHARS + 1), mode="full")

        with self.assertRaises(ValidationError):
            ingest.IngestStartRequest(file_path="x" * (ingest.MAX_INGEST_TARGET_CHARS + 1), mode="full")

        req = ingest.IngestStartRequest(file_id=f" {'x' * ingest.MAX_INGEST_TARGET_CHARS} ", mode="full")
        self.assertEqual(req.file_id, "x" * ingest.MAX_INGEST_TARGET_CHARS)

    def test_start_ingest_trims_target_fields_before_resolving(self) -> None:
        path = self.local_root / "chat.json"
        path.write_text("{}", encoding="utf-8")

        with patch("backend.routers.ingest.threading.Thread") as thread_cls:
            thread_cls.return_value.start.return_value = None
            response = ingest.start_ingest(ingest.IngestStartRequest(file_id="  chat.json  ", mode="full"))

        self.assertIn(response["task_id"], ingest._tasks)
        self.assertEqual(ingest._tasks[response["task_id"]]["file_id"], "chat.json")
        thread_cls.return_value.start.assert_called_once()

    def test_start_ingest_trims_legacy_query_file_path_before_resolving(self) -> None:
        data_dir = self.local_root / "data"
        data_dir.mkdir()
        (data_dir / "chat.json").write_text("{}", encoding="utf-8")

        with patch("backend.routers.ingest.threading.Thread") as thread_cls:
            thread_cls.return_value.start.return_value = None
            response = ingest.start_ingest(file_path="  data  ")

        self.assertIn(response["task_id"], ingest._tasks)
        self.assertEqual(ingest._tasks[response["task_id"]]["file_id"], "data")
        thread_cls.return_value.start.assert_called_once()

    def test_start_ingest_treats_blank_legacy_query_file_path_as_missing(self) -> None:
        with self.assertRaises(HTTPException) as missing:
            ingest.start_ingest(file_path="   ")

        self.assertEqual(missing.exception.status_code, 400)
        self.assertIn("只提供", str(missing.exception.detail))
        self.assertEqual(ingest._tasks, {})

    def test_start_ingest_queues_full_mode_for_existing_file(self) -> None:
        path = self.local_root / "chat.json"
        path.write_text("{}", encoding="utf-8")

        with patch("backend.routers.ingest.threading.Thread") as thread_cls:
            thread_cls.return_value.start.return_value = None
            response = ingest.start_ingest(ingest.IngestStartRequest(file_id="chat.json", mode="full"))

        self.assertEqual(response["mode"], "full")
        self.assertIn("全流程导入任务已启动", response["message"])
        self.assertIn("补齐必要索引、摘要和向量", response["message"])
        self.assertIn(response["task_id"], ingest._tasks)
        self.assertEqual(ingest._tasks[response["task_id"]]["file_id"], "chat.json")
        thread_cls.return_value.start.assert_called_once()

    def test_start_ingest_marks_task_error_when_worker_thread_fails_to_start(self) -> None:
        path = self.local_root / "chat.json"
        path.write_text("{}", encoding="utf-8")

        with patch("backend.routers.ingest.threading.Thread") as thread_cls:
            thread_cls.return_value.start.side_effect = RuntimeError("thread pool exhausted")
            with self.assertRaises(HTTPException) as exc:
                ingest.start_ingest(ingest.IngestStartRequest(file_id="chat.json", mode="full"))

        self.assertEqual(exc.exception.status_code, 500)
        self.assertIn("导入任务启动失败", str(exc.exception.detail))
        self.assertEqual(len(ingest._tasks), 1)
        task = next(iter(ingest._tasks.values()))
        self.assertEqual(task["status"], "error")
        self.assertIn("导入任务启动失败", task["error"])
        self.assertFalse(any(item["status"] == "running" for item in ingest._tasks.values()))

        with patch("backend.routers.ingest.threading.Thread") as retry_thread_cls:
            retry_thread_cls.return_value.start.return_value = None
            retry_response = ingest.start_ingest(ingest.IngestStartRequest(file_id="chat.json", mode="full"))

        self.assertIn(retry_response["task_id"], ingest._tasks)
        self.assertEqual(ingest._tasks[retry_response["task_id"]]["status"], "running")

    def test_start_ingest_rejects_retry_while_previous_process_lock_is_releasing(self) -> None:
        path = self.local_root / "chat.json"
        path.write_text("{}", encoding="utf-8")

        acquired = ingest._process_lock.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            with self.assertRaises(HTTPException) as exc:
                ingest.start_ingest(ingest.IngestStartRequest(file_id="chat.json", mode="full"))
        finally:
            ingest._process_lock.release()

        self.assertEqual(exc.exception.status_code, 409)
        self.assertIn("收尾", str(exc.exception.detail))
        self.assertEqual(ingest._tasks, {})

    def test_start_ingest_allows_legacy_directory_path_for_import_and_index_modes(self) -> None:
        data_dir = self.local_root / "data"
        data_dir.mkdir()
        (data_dir / "chat.json").write_text("{}", encoding="utf-8")

        with patch("backend.routers.ingest.threading.Thread") as thread_cls:
            thread_cls.return_value.start.return_value = None
            full_response = ingest.start_ingest(
                ingest.IngestStartRequest(file_path="data", mode="full")
            )
            ingest._tasks[full_response["task_id"]]["status"] = "completed"
            with (
                patch("backend.routers.ingest.store.get_threads_for_ingest_file_paths", return_value=["项目群"]),
                patch("backend.routers.ingest.store.get_threads_for_message_id_prefixes", return_value=[]),
                patch("backend.routers.ingest.store.get_session_ids_for_ingest_file_paths", return_value=[]),
                patch("backend.routers.ingest.store.get_session_ids_for_message_id_prefixes", return_value=[]),
            ):
                fts_response = ingest.start_ingest(
                    ingest.IngestStartRequest(file_path="data", mode="fts")
                )

        self.assertEqual(full_response["mode"], "full")
        self.assertEqual(fts_response["mode"], "fts")
        self.assertEqual(ingest._tasks[full_response["task_id"]]["file_id"], "data")
        self.assertEqual(ingest._tasks[fts_response["task_id"]]["file_id"], "data")

    def test_start_ingest_rejects_directory_index_mode_without_known_scope(self) -> None:
        data_dir = self.local_root / "data"
        data_dir.mkdir()
        (data_dir / "chat.json").write_text("{}", encoding="utf-8")

        with (
            patch("backend.routers.ingest.store.get_threads_for_ingest_file_paths", return_value=[]),
            patch("backend.routers.ingest.store.get_threads_for_message_id_prefixes", return_value=[]),
            patch("backend.routers.ingest.store.get_session_ids_for_ingest_file_paths", return_value=[]),
            patch("backend.routers.ingest.store.get_session_ids_for_message_id_prefixes", return_value=[]),
            self.assertRaises(HTTPException) as exc,
        ):
            ingest.start_ingest(ingest.IngestStartRequest(file_path="data", mode="fts"))

        self.assertEqual(exc.exception.status_code, 400)
        self.assertIn("入库消息范围", str(exc.exception.detail))
        self.assertEqual(ingest._tasks, {})

    def test_start_ingest_rejects_directory_summary_mode_without_session_chunks(self) -> None:
        data_dir = self.local_root / "data"
        data_dir.mkdir()
        (data_dir / "chat.json").write_text("{}", encoding="utf-8")

        with (
            patch("backend.routers.ingest.store.get_threads_for_ingest_file_paths", return_value=["项目群"]),
            patch("backend.routers.ingest.store.get_threads_for_message_id_prefixes", return_value=[]),
            patch("backend.routers.ingest.store.get_session_ids_for_ingest_file_paths", return_value=[]),
            patch("backend.routers.ingest.store.get_session_ids_for_message_id_prefixes", return_value=[]),
            patch("backend.routers.ingest.store.get_session_index_status", return_value={"total": 0, "missing_summary": 0, "missing_embedding": 0}),
            self.assertRaises(HTTPException) as exc,
        ):
            ingest.start_ingest(ingest.IngestStartRequest(file_path="data", mode="summary"))

        self.assertEqual(exc.exception.status_code, 400)
        self.assertIn("会话分块", str(exc.exception.detail))
        self.assertEqual(ingest._tasks, {})

    def test_start_ingest_allows_current_file_for_index_only_modes(self) -> None:
        path = self.local_root / "chat.json"
        path.write_text("{}", encoding="utf-8")

        with (
            patch("backend.routers.ingest._is_file_record_current", return_value=True),
            patch("backend.routers.ingest._has_file_index_scope", return_value=True),
            patch(
                "backend.routers.ingest._index_status_for_file",
                return_value={"total": 3, "missing_summary": 0, "missing_embedding": 1},
            ),
            patch("backend.routers.ingest._embedding_available", return_value=True),
            patch("backend.routers.ingest.threading.Thread") as thread_cls,
        ):
            thread_cls.return_value.start.return_value = None
            response = ingest.start_ingest(ingest.IngestStartRequest(file_id="chat.json", mode="embeddings"))

        self.assertEqual(response["mode"], "embeddings")
        self.assertIn("仅向量任务已启动", response["message"])
        self.assertIn("embedding API", response["message"])
        task = ingest._tasks[response["task_id"]]
        self.assertEqual(task["mode"], "embeddings")
        self.assertEqual(task["file_id"], "chat.json")
        thread_cls.assert_called_once()

    def test_start_ingest_rejects_summary_mode_when_summary_model_is_unavailable(self) -> None:
        path = self.local_root / "chat.json"
        path.write_text("{}", encoding="utf-8")

        with (
            patch("backend.routers.ingest._is_file_record_current", return_value=True),
            patch("backend.routers.ingest._has_file_index_scope", return_value=True),
            patch(
                "backend.routers.ingest._index_status_for_file",
                return_value={"total": 3, "missing_summary": 1, "missing_embedding": 0},
            ),
            patch("backend.routers.ingest._summary_available", return_value=False),
            self.assertRaises(HTTPException) as exc,
        ):
            ingest.start_ingest(ingest.IngestStartRequest(file_id="chat.json", mode="summary"))

        self.assertEqual(exc.exception.status_code, 400)
        self.assertIn("SUMMARY_MODEL", str(exc.exception.detail))
        self.assertEqual(ingest._tasks, {})

    def test_start_ingest_rejects_vector_mode_when_embedding_is_unavailable(self) -> None:
        path = self.local_root / "chat.json"
        path.write_text("{}", encoding="utf-8")

        with (
            patch("backend.routers.ingest._is_file_record_current", return_value=True),
            patch("backend.routers.ingest._has_file_index_scope", return_value=True),
            patch(
                "backend.routers.ingest._index_status_for_file",
                return_value={"total": 3, "missing_summary": 0, "missing_embedding": 1},
            ),
            patch("backend.routers.ingest._embedding_available", return_value=False),
            self.assertRaises(HTTPException) as exc,
        ):
            ingest.start_ingest(ingest.IngestStartRequest(file_id="chat.json", mode="vector"))

        self.assertEqual(exc.exception.status_code, 400)
        self.assertIn("EMBED_BASE_URL", str(exc.exception.detail))
        self.assertEqual(ingest._tasks, {})

    def test_start_ingest_rejects_directory_summary_mode_when_summary_model_is_unavailable(self) -> None:
        data_dir = self.local_root / "data"
        data_dir.mkdir()
        (data_dir / "chat.json").write_text("{}", encoding="utf-8")

        with (
            patch("backend.routers.ingest.store.get_threads_for_ingest_file_paths", return_value=["项目群"]),
            patch("backend.routers.ingest.store.get_threads_for_message_id_prefixes", return_value=[]),
            patch("backend.routers.ingest.store.get_session_ids_for_ingest_file_paths", return_value=[1]),
            patch("backend.routers.ingest.store.get_session_ids_for_message_id_prefixes", return_value=[]),
            patch(
                "backend.routers.ingest.store.get_session_index_status",
                return_value={"total": 1, "missing_summary": 1, "missing_embedding": 0},
            ),
            patch("backend.routers.ingest._summary_available", return_value=False),
            self.assertRaises(HTTPException) as exc,
        ):
            ingest.start_ingest(ingest.IngestStartRequest(file_path="data", mode="summary"))

        self.assertEqual(exc.exception.status_code, 400)
        self.assertIn("SUMMARY_MODEL", str(exc.exception.detail))
        self.assertEqual(ingest._tasks, {})

    def test_list_files_reflects_latest_live_task_status(self) -> None:
        path = self.local_root / "chat.json"
        path.write_text("{}", encoding="utf-8")
        task_id = "live-task"
        with ingest._tasks_lock:
            ingest._tasks[task_id] = {
                "status": "running",
                "logs": [],
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:01Z",
                "error": None,
                "file_id": "chat.json",
                "mode": "summary",
                "process": None,
            }

        with patch("backend.routers.ingest.store.get_ingest_file_records", return_value={}):
            listed = ingest.list_files(limit=100, offset=0)

        item = listed["items"][0]
        self.assertEqual(item.file_id, "chat.json")
        self.assertEqual(item.ingest_status, "running")
        self.assertEqual(item.task_id, task_id)
        self.assertEqual(item.task_status, "running")
        self.assertEqual(item.task_mode, "summary")

    def test_latest_task_for_file_uses_millisecond_timestamps_and_stable_tiebreakers(self) -> None:
        self.assertRegex(ingest._now(), r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$")

        with ingest._tasks_lock:
            ingest._tasks["old-task"] = {
                "status": "error",
                "logs": [],
                "created_at": "2024-01-01T00:00:00.001Z",
                "updated_at": "2024-01-01T00:00:00.002Z",
                "error": "failed",
                "file_id": "chat.json",
                "mode": "full",
                "process": None,
            }
            ingest._tasks["new-task"] = {
                "status": "completed",
                "logs": [],
                "created_at": "2024-01-01T00:00:00.001Z",
                "updated_at": "2024-01-01T00:00:00.003Z",
                "error": None,
                "file_id": "chat.json",
                "mode": "embeddings",
                "process": None,
            }

        latest = ingest._latest_task_for_file("chat.json")

        self.assertIsNotNone(latest)
        assert latest is not None
        self.assertEqual(latest[0], "new-task")
        self.assertEqual(latest[1]["mode"], "embeddings")

    def test_list_files_keeps_file_sync_status_when_latest_task_failed(self) -> None:
        path = self.local_root / "chat.json"
        path.write_text("{}", encoding="utf-8")
        stat = path.stat()
        file_key = str(path.resolve())
        task_id = "failed-task"
        with ingest._tasks_lock:
            ingest._tasks[task_id] = {
                "status": "error",
                "logs": [],
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:01Z",
                "error": "embedding failed",
                "file_id": "chat.json",
                "mode": "embeddings",
                "process": None,
            }

        with patch(
            "backend.routers.ingest.store.get_ingest_file_records",
            return_value={
                file_key: {
                    "path": file_key,
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "total": 10,
                    "included": 10,
                    "changed": 4,
                    "inserted": 3,
                    "parser_version": ingest.PARSER_VERSION,
                    "updated_at": "2024-01-01 00:00:00",
                }
            },
        ):
            listed = ingest.list_files(limit=100, offset=0)

        item = listed["items"][0]
        self.assertEqual(item.ingest_status, "up_to_date")
        self.assertIsNone(item.ingest_status_reason)
        self.assertEqual(item.ingest_changed, 4)
        self.assertEqual(item.ingest_inserted, 3)
        self.assertEqual(item.task_status, "error")
        self.assertEqual(item.task_mode, "embeddings")

    def test_list_files_batches_index_status_for_recorded_page_files(self) -> None:
        first = self.local_root / "first.json"
        second = self.local_root / "second.json"
        first.write_text("{}", encoding="utf-8")
        second.write_text("{}", encoding="utf-8")
        first_stat = first.stat()
        second_stat = second.stat()
        first_key = str(first.resolve())
        second_key = str(second.resolve())

        records = {
            first_key: {
                "path": first_key,
                "size": first_stat.st_size,
                "mtime_ns": first_stat.st_mtime_ns,
                "total": 10,
                "included": 9,
                "changed": 1,
                "inserted": 1,
                "parser_version": ingest.PARSER_VERSION,
                "updated_at": "2024-01-01 00:00:00",
            },
            second_key: {
                "path": second_key,
                "size": second_stat.st_size,
                "mtime_ns": second_stat.st_mtime_ns,
                "total": 20,
                "included": 18,
                "changed": 2,
                "inserted": 2,
                "parser_version": ingest.PARSER_VERSION,
                "updated_at": "2024-01-01 00:00:00",
            },
        }

        with (
            patch("backend.routers.ingest.store.get_ingest_file_records", return_value=records),
            patch(
                "backend.routers.ingest._index_statuses_for_files",
                return_value={
                    first_key: {"total": 3, "missing_summary": 1, "missing_embedding": 2},
                    second_key: {"total": 4, "missing_summary": 0, "missing_embedding": None},
                },
            ) as index_statuses,
            patch("backend.routers.ingest._summary_available", return_value=True),
            patch("backend.routers.ingest._embedding_available", return_value=True),
        ):
            listed = ingest.list_files(limit=100, offset=0)

        index_statuses.assert_called_once()
        indexed = {path.name for path in index_statuses.call_args.args[0]}
        self.assertEqual(indexed, {"first.json", "second.json"})
        items = {item.file_id: item for item in listed["items"]}
        self.assertEqual(items["first.json"].session_chunks, 3)
        self.assertEqual(items["first.json"].missing_summary_chunks, 1)
        self.assertEqual(items["second.json"].session_chunks, 4)
        self.assertIsNone(items["second.json"].missing_vector_chunks)

    def test_list_files_hides_index_gaps_that_current_configuration_cannot_repair(self) -> None:
        path = self.local_root / "chat.json"
        path.write_text("{}", encoding="utf-8")
        stat = path.stat()
        file_key = str(path.resolve())

        with (
            patch(
                "backend.routers.ingest.store.get_ingest_file_records",
                return_value={
                    file_key: {
                        "path": file_key,
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "total": 10,
                        "included": 10,
                        "changed": 0,
                        "inserted": 0,
                        "parser_version": ingest.PARSER_VERSION,
                        "updated_at": "2024-01-01 00:00:00",
                    }
                },
            ),
            patch(
                "backend.routers.ingest._index_statuses_for_files",
                return_value={file_key: {"total": 3, "missing_summary": 2, "missing_embedding": 1}},
            ),
            patch("backend.routers.ingest._summary_available", return_value=False),
            patch("backend.routers.ingest._embedding_available", return_value=False),
        ):
            listed = ingest.list_files(limit=100, offset=0)

        item = listed["items"][0]
        self.assertEqual(item.session_chunks, 3)
        self.assertIsNone(item.missing_summary_chunks)
        self.assertIsNone(item.missing_vector_chunks)

    def test_index_statuses_fall_back_to_message_prefix_when_mapping_has_no_sessions(self) -> None:
        path = self.local_root / "legacy.json"
        path.write_text("{}", encoding="utf-8")
        file_key = str(path.resolve())
        prefix = f"{ingest.file_scope_for_path(path)}:"

        with (
            patch("backend.routers.ingest.store.get_ingest_file_message_mapping_paths", return_value={file_key}),
            patch("backend.routers.ingest.store.get_session_ids_by_ingest_file_paths", return_value={}),
            patch("backend.routers.ingest.store.get_session_ids_by_message_id_prefixes", return_value={prefix: [7]}),
            patch(
                "backend.routers.ingest.store.get_session_index_statuses",
                return_value={file_key: {"total": 1, "missing_summary": 0, "missing_embedding": None}},
            ) as status_for_keys,
        ):
            statuses = ingest._index_statuses_for_files([path])

        status_for_keys.assert_called_once_with({file_key: [7]})
        self.assertEqual(statuses[file_key]["total"], 1)

    def test_list_files_marks_current_file_changed_when_parser_version_is_stale(self) -> None:
        path = self.local_root / "chat.json"
        path.write_text("{}", encoding="utf-8")
        stat = path.stat()
        file_key = str(path.resolve())

        with patch(
            "backend.routers.ingest.store.get_ingest_file_records",
            return_value={
                file_key: {
                    "path": file_key,
                    "size": stat.st_size,
                    "mtime_ns": stat.st_mtime_ns,
                    "total": 10,
                    "included": 10,
                    "changed": 0,
                    "inserted": 0,
                    "parser_version": ingest.PARSER_VERSION - 1,
                    "updated_at": "2024-01-01 00:00:00",
                }
            },
        ):
            listed = ingest.list_files(limit=100, offset=0)

        self.assertEqual(listed["items"][0].ingest_status, "changed")
        self.assertEqual(listed["items"][0].ingest_status_reason, "parser_version_stale")

    def test_list_files_does_not_report_zero_index_status_for_never_imported_file(self) -> None:
        path = self.local_root / "chat.json"
        path.write_text("{}", encoding="utf-8")

        with (
            patch("backend.routers.ingest.store.get_ingest_file_records", return_value={}),
            patch(
                "backend.routers.ingest._index_status_for_file",
                return_value={"total": 0, "missing_summary": 0, "missing_embedding": 0},
            ) as index_status,
        ):
            listed = ingest.list_files(limit=100, offset=0)

        item = listed["items"][0]
        self.assertEqual(item.ingest_status, "never")
        self.assertIsNone(item.ingest_status_reason)
        self.assertIsNone(item.session_chunks)
        self.assertIsNone(item.missing_summary_chunks)
        self.assertIsNone(item.missing_vector_chunks)
        index_status.assert_not_called()

    def test_list_files_does_not_parse_json_to_backfill_missing_mapping(self) -> None:
        path = self.local_root / "large.json"
        path.write_text("{not-json", encoding="utf-8")
        stat = path.stat()
        file_key = str(path.resolve())

        with (
            patch(
                "backend.routers.ingest.store.get_ingest_file_records",
                return_value={
                    file_key: {
                        "path": file_key,
                        "size": stat.st_size,
                        "mtime_ns": stat.st_mtime_ns,
                        "total": 10,
                        "included": 10,
                        "changed": 0,
                        "inserted": 0,
                        "parser_version": ingest.PARSER_VERSION,
                        "updated_at": "2024-01-01 00:00:00",
                    }
                },
            ),
            patch("backend.routers.ingest.store.get_session_ids_for_ingest_file_paths", return_value=[]),
            patch("backend.routers.ingest.store.get_session_ids_for_message_id_prefixes", return_value=[]),
            patch("backend.routers.ingest.store.ingest_file_message_mapping_exists", return_value=False),
            patch("backend.routers.ingest.json.loads", side_effect=AssertionError("list_files must not parse JSON")),
        ):
            listed = ingest.list_files(limit=100, offset=0)

        item = listed["items"][0]
        self.assertEqual(item.ingest_status, "up_to_date")
        self.assertIsNone(item.session_chunks)
        self.assertIsNone(item.missing_summary_chunks)
        self.assertIsNone(item.missing_vector_chunks)

    def test_list_files_reports_changed_file_even_if_latest_task_failed(self) -> None:
        path = self.local_root / "chat.json"
        path.write_text("{}", encoding="utf-8")
        file_key = str(path.resolve())
        with ingest._tasks_lock:
            ingest._tasks["failed-task"] = {
                "status": "error",
                "logs": [],
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:01Z",
                "error": "parse failed",
                "file_id": "chat.json",
                "mode": "full",
                "process": None,
            }

        with patch(
            "backend.routers.ingest.store.get_ingest_file_records",
            return_value={
                file_key: {
                    "path": file_key,
                    "size": 1,
                    "mtime_ns": 1,
                    "total": 10,
                    "included": 10,
                    "inserted": 0,
                    "updated_at": "2024-01-01 00:00:00",
                }
            },
        ):
            listed = ingest.list_files(limit=100, offset=0)

        item = listed["items"][0]
        self.assertEqual(item.ingest_status, "changed")
        self.assertEqual(item.ingest_status_reason, "file_changed")
        self.assertEqual(item.task_status, "error")

    def test_list_tasks_is_paginated_newest_first(self) -> None:
        with ingest._tasks_lock:
            for index in range(3):
                ingest._tasks[f"task-{index}"] = {
                    "status": "completed",
                    "logs": [],
                    "created_at": f"2024-01-01T00:00:0{index}Z",
                    "updated_at": f"2024-01-01T00:00:0{index}Z",
                    "error": None,
                    "file_id": f"chat-{index}.json",
                    "mode": "full",
                    "process": None,
                }

        first_page = ingest.list_tasks(limit=2, offset=0)
        second_page = ingest.list_tasks(limit=2, offset=2)

        self.assertEqual(first_page["total_count"], 3)
        self.assertEqual(first_page["returned"], 2)
        self.assertEqual([task.task_id for task in first_page["items"]], ["task-2", "task-1"])
        self.assertEqual(second_page["returned"], 1)
        self.assertEqual(second_page["items"][0].task_id, "task-0")

    def test_list_tasks_prioritizes_live_tasks_before_finished_tasks(self) -> None:
        with ingest._tasks_lock:
            ingest._tasks["old-running"] = {
                "status": "running",
                "logs": [],
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:01Z",
                "error": None,
                "file_id": "running.json",
                "mode": "full",
                "process": None,
            }
            ingest._tasks["new-completed"] = {
                "status": "completed",
                "logs": [],
                "created_at": "2024-01-01T00:10:00Z",
                "updated_at": "2024-01-01T00:10:01Z",
                "error": None,
                "file_id": "done.json",
                "mode": "full",
                "process": None,
            }

        first_page = ingest.list_tasks(limit=1, offset=0)

        self.assertEqual(first_page["total_count"], 2)
        self.assertEqual(first_page["items"][0].task_id, "old-running")

    def test_list_tasks_uses_recent_log_tail_without_joining_full_logs(self) -> None:
        class TailOnlyLogs:
            def __init__(self, lines: list[str]) -> None:
                self._lines = lines

            def __iter__(self):
                raise AssertionError("task list should not join full ingest logs")

            def __reversed__(self):
                return iter(self._lines[::-1])

        with ingest._tasks_lock:
            ingest._tasks["long-log-task"] = {
                "status": "running",
                "logs": TailOnlyLogs(["very old log\n", "embedding 完成 5/10\n"]),
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:01Z",
                "error": None,
                "file_id": "chat.json",
                "mode": "embeddings",
                "process": None,
            }

        page = ingest.list_tasks(limit=1, offset=0)
        task = page["items"][0]

        self.assertEqual(task.task_id, "long-log-task")
        self.assertEqual(task.logs, "")
        self.assertEqual(task.log_tail, "")
        self.assertEqual(task.stage, "embedding")
        self.assertEqual(task.progress, 85)

    def test_shutdown_running_tasks_cancels_processes_and_queued_tasks(self) -> None:
        running_process = object()
        with ingest._tasks_lock:
            ingest._tasks["running-task"] = {
                "status": "running",
                "logs": [],
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "error": None,
                "file_id": "chat.json",
                "mode": "full",
                "process": running_process,
            }
            ingest._tasks["queued-task"] = {
                "status": "running",
                "logs": [],
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "error": None,
                "file_id": "later.json",
                "mode": "full",
                "process": None,
            }
            ingest._tasks["completed-task"] = {
                "status": "completed",
                "logs": [],
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "error": None,
                "file_id": "done.json",
                "mode": "full",
                "process": None,
            }

        with patch("backend.routers.ingest._terminate_process") as terminate:
            ingest.shutdown_running_tasks()

        terminate.assert_called_once_with(running_process)
        self.assertEqual(ingest._tasks["running-task"]["status"], "cancelled")
        self.assertEqual(ingest._tasks["running-task"]["process"], None)
        self.assertIn("后端关闭", ingest._tasks["running-task"]["error"])
        self.assertEqual(ingest._tasks["queued-task"]["status"], "cancelled")
        self.assertIn("后端关闭", ingest._tasks["queued-task"]["error"])
        self.assertEqual(ingest._tasks["completed-task"]["status"], "completed")

    def test_cancelled_task_is_treated_as_cancel_requested_before_subprocess_start(self) -> None:
        with ingest._tasks_lock:
            ingest._tasks["cancelled-task"] = {
                "status": "cancelled",
                "logs": [],
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "error": "shutdown",
                "file_id": "chat.json",
                "mode": "full",
                "process": None,
            }

        self.assertTrue(ingest._requested_cancel("cancelled-task"))

    def test_run_ingest_task_leaves_already_cancelled_queued_task_cancelled_before_lock(self) -> None:
        path = self.local_root / "chat.json"
        path.write_text("{}", encoding="utf-8")
        task_id = "late-cancelled-task"
        with ingest._tasks_lock:
            ingest._tasks[task_id] = {
                "status": "cancelled",
                "logs": [],
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "error": None,
                "file_id": "chat.json",
                "mode": "full",
                "process": None,
            }

        acquired = ingest._process_lock.acquire(blocking=False)
        self.assertTrue(acquired)
        try:
            ingest._run_ingest_task(task_id, path, "full")
            self.assertTrue(ingest._process_lock.locked())
        finally:
            ingest._process_lock.release()

        self.assertEqual(ingest._tasks[task_id]["status"], "cancelled")
        self.assertIsNone(ingest._tasks[task_id]["error"])
        self.assertIsNone(ingest._tasks[task_id]["process"])

    def test_cancel_task_marks_processless_running_task_cancelled_immediately(self) -> None:
        with ingest._tasks_lock:
            ingest._tasks["queued-task"] = {
                "status": "running",
                "logs": [],
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "error": None,
                "file_id": "chat.json",
                "mode": "full",
                "process": None,
            }

        status = ingest.cancel_task("queued-task")

        self.assertEqual(status.status, "cancelled")
        self.assertFalse(status.can_cancel)
        self.assertEqual(ingest._tasks["queued-task"]["status"], "cancelled")
        self.assertIsNone(ingest._tasks["queued-task"]["process"])

    def test_cancel_task_marks_cancelled_after_process_termination(self) -> None:
        class FakeProcess:
            pid = 12345

            def __init__(self) -> None:
                self.terminated = False
                self.killed = False
                self.exited = False

            def poll(self) -> int | None:
                return 0 if self.exited else None

            def terminate(self) -> None:
                self.terminated = True

            def kill(self) -> None:
                self.killed = True

            def wait(self, timeout=None) -> int:
                self.exited = True
                return 0

        process = FakeProcess()
        with ingest._tasks_lock:
            ingest._tasks["running-task"] = {
                "status": "running",
                "logs": [],
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "error": None,
                "file_id": "chat.json",
                "mode": "full",
                "process": process,
            }

        status = ingest.cancel_task("running-task")

        self.assertTrue(process.terminated)
        self.assertFalse(process.killed)
        self.assertEqual(status.status, "cancelled")
        self.assertFalse(status.can_cancel)
        self.assertIsNone(ingest._tasks["running-task"]["process"])

    def test_cancel_task_preserves_completed_status_when_process_already_exited(self) -> None:
        class FinishedProcess:
            def poll(self) -> int:
                return 0

        with ingest._tasks_lock:
            ingest._tasks["finished-task"] = {
                "status": "running",
                "logs": [],
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "error": None,
                "file_id": "chat.json",
                "mode": "full",
                "process": FinishedProcess(),
            }

        status = ingest.cancel_task("finished-task")

        self.assertEqual(status.status, "completed")
        self.assertFalse(status.can_cancel)
        self.assertIsNone(status.error)
        self.assertIsNone(ingest._tasks["finished-task"]["process"])

    def test_cancel_task_preserves_failure_reason_when_process_already_failed(self) -> None:
        class FailedProcess:
            def poll(self) -> int:
                return 2

        with ingest._tasks_lock:
            ingest._tasks["failed-task"] = {
                "status": "running",
                "logs": [
                    '__INGEST_PROGRESS__ {"stage":"embedding","progress":80,"message":"old progress"}\n',
                    "RuntimeError: embedding endpoint rejected token=sk-cancel-secret-123456\n",
                ],
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "error": None,
                "file_id": "chat.json",
                "mode": "embeddings",
                "process": FailedProcess(),
            }

        status = ingest.cancel_task("failed-task")

        self.assertEqual(status.status, "error")
        self.assertFalse(status.can_cancel)
        self.assertIn("ingest exited with code 2", status.error or "")
        self.assertIn("embedding endpoint rejected", status.error or "")
        self.assertIn("[redacted]", status.error or "")
        self.assertNotIn("sk-cancel-secret", status.error or "")
        self.assertIsNone(ingest._tasks["failed-task"]["process"])

    def test_run_ingest_task_terminates_when_cancel_arrives_during_subprocess_start(self) -> None:
        path = self.local_root / "chat.json"
        path.write_text("{}", encoding="utf-8")
        task_id = "race-cancel-task"

        class FakeProcess:
            stdout = []
            pid = 12345

            def __init__(self) -> None:
                self.terminated = False
                self.killed = False

            def poll(self) -> None:
                return None

            def terminate(self) -> None:
                self.terminated = True

            def kill(self) -> None:
                self.killed = True

            def wait(self, timeout=None) -> int:
                return 0

        fake_process = FakeProcess()
        with ingest._tasks_lock:
            ingest._tasks[task_id] = {
                "status": "running",
                "logs": [],
                "created_at": "2024-01-01T00:00:00Z",
                "updated_at": "2024-01-01T00:00:00Z",
                "error": None,
                "file_id": "chat.json",
                "mode": "full",
                "process": None,
            }

        def popen_side_effect(*_args, **_kwargs):
            with ingest._tasks_lock:
                ingest._tasks[task_id]["status"] = "cancel_requested"
            return fake_process

        with patch("backend.routers.ingest.subprocess.Popen", side_effect=popen_side_effect):
            ingest._run_ingest_task(task_id, path, "full")

        self.assertTrue(fake_process.terminated)
        self.assertFalse(fake_process.killed)
        self.assertEqual(ingest._tasks[task_id]["status"], "cancelled")
        self.assertIsNone(ingest._tasks[task_id]["process"])


if __name__ == "__main__":
    unittest.main()
