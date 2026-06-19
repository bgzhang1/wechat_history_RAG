from __future__ import annotations

import asyncio
import json
import logging
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.requests import Request

from backend.errors import error_payload, http_exception_handler, validation_exception_handler
from backend import logging_utils
from backend.main import _default_origins, _diagnostics, health_check
from backend.redaction import public_exception_message, redact_data, redact_text


class LoggingAndRedactionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self._old_log_path = logging_utils.LOG_PATH
        logging_utils.LOG_PATH = Path(self._tmp.name) / "backend.log.jsonl"

    def tearDown(self) -> None:
        logging_utils.LOG_PATH = self._old_log_path
        self._tmp.cleanup()

    def test_read_recent_logs_returns_newest_first_and_redacts_public_payload(self) -> None:
        records = [
            {
                "timestamp": "2026-01-01T00:00:00Z",
                "level": "info",
                "message": "HTTP exception",
                "details": {
                    "taskName": "Task-123",
                    "CHAT_API_KEY": "sk-super-secret-123456",
                    "nested": {"authorization": "Bearer token-value"},
                },
            },
            {
                "timestamp": "2026-01-01T00:00:01Z",
                "level": "error",
                "message": "failed with api_key=sk-another-secret-123456",
                "traceback": "Authorization: Bearer raw-token-value\nboom",
            },
        ]
        logging_utils.LOG_PATH.write_text(
            "\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n",
            encoding="utf-8",
        )

        public = logging_utils.read_recent_logs(level="info", limit=10)

        self.assertEqual([item["timestamp"] for item in public], ["2026-01-01T00:00:01Z", "2026-01-01T00:00:00Z"])
        serialized = json.dumps(public, ensure_ascii=False)
        self.assertNotIn("sk-super-secret", serialized)
        self.assertNotIn("sk-another-secret", serialized)
        self.assertNotIn("token-value", serialized)
        self.assertNotIn("raw-token-value", serialized)
        self.assertNotIn("taskName", serialized)
        self.assertIn("[redacted]", serialized)
        self.assertNotEqual(public[1]["message"], "HTTP exception")

    def test_read_recent_logs_skips_malformed_lines_and_validates_level(self) -> None:
        logging_utils.LOG_PATH.write_text(
            (
                '{"level":"warning","message":"kept"}\n'
                'not-json\n'
                '[]\n'
                '"valid-json-but-not-object"\n'
                '{"level":"debug","message":"ignored"}\n'
            ),
            encoding="utf-8",
        )

        public = logging_utils.read_recent_logs(level="warning", limit=100)

        self.assertEqual(len(public), 1)
        self.assertEqual(public[0]["message"], "kept")
        with self.assertRaises(ValueError):
            logging_utils.read_recent_logs(level="verbose", limit=1)
        with self.assertRaises(ValueError):
            logging_utils.read_recent_logs(level=None, limit=1)  # type: ignore[arg-type]

    def test_read_recent_logs_keeps_only_latest_matching_records(self) -> None:
        logging_utils.LOG_PATH.write_text(
            "\n".join(
                json.dumps({"level": "error", "message": f"error-{index}"}, ensure_ascii=False)
                for index in range(20)
            )
            + "\n",
            encoding="utf-8",
        )

        public = logging_utils.read_recent_logs(level="error", limit=3)

        self.assertEqual([item["message"] for item in public], ["error-19", "error-18", "error-17"])

    def test_read_recent_logs_continues_into_rotated_log_when_current_file_is_short(self) -> None:
        rotated_path = logging_utils.LOG_PATH.with_suffix(logging_utils.LOG_PATH.suffix + ".1")
        logging_utils.LOG_PATH.write_text(
            json.dumps({"level": "error", "message": "current-error"}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        rotated_path.write_text(
            "\n".join(
                [
                    json.dumps({"level": "error", "message": "rotated-old"}, ensure_ascii=False),
                    json.dumps({"level": "error", "message": "rotated-new"}, ensure_ascii=False),
                ]
            )
            + "\n",
            encoding="utf-8",
        )

        public = logging_utils.read_recent_logs(level="error", limit=3)

        self.assertEqual(
            [item["message"] for item in public],
            ["current-error", "rotated-new", "rotated-old"],
        )

    def test_log_line_reader_reads_newest_first_across_small_utf8_chunks(self) -> None:
        logging_utils.LOG_PATH.write_text(
            "旧日志\n中文日志\nlatest\n",
            encoding="utf-8",
        )

        lines = list(logging_utils._iter_log_lines_newest_first(logging_utils.LOG_PATH, chunk_size=5))

        self.assertEqual(lines, ["latest", "中文日志", "旧日志"])

    def test_read_recent_logs_tolerates_invalid_direct_limit(self) -> None:
        logging_utils.LOG_PATH.write_text(
            "\n".join(
                json.dumps({"level": "error", "message": f"error-{index}"}, ensure_ascii=False)
                for index in range(3)
            )
            + "\n",
            encoding="utf-8",
        )

        public = logging_utils.read_recent_logs(level="error", limit=object())  # type: ignore[arg-type]

        self.assertEqual([item["message"] for item in public], ["error-2", "error-1", "error-0"])

    def test_json_log_handler_redacts_secrets_before_writing_to_disk(self) -> None:
        handler = logging_utils.JsonLineHandler(logging_utils.LOG_PATH)
        record = logging.LogRecord(
            name="wechat_backend",
            level=logging.ERROR,
            pathname=__file__,
            lineno=1,
            msg="request failed with api_key=%s",
            args=("sk-file-secret-123456",),
            exc_info=None,
            func="test",
        )
        record.CHAT_API_KEY = "sk-extra-secret-123456"
        try:
            raise RuntimeError("Authorization: Bearer raw-log-token")
        except RuntimeError as exc:
            record.exc_info = (type(exc), exc, exc.__traceback__)

        handler.emit(record)

        raw = logging_utils.LOG_PATH.read_text(encoding="utf-8")
        self.assertNotIn("sk-file-secret", raw)
        self.assertNotIn("sk-extra-secret", raw)
        self.assertNotIn("raw-log-token", raw)
        self.assertIn("[redacted]", raw)

    def test_redaction_helpers_cover_nested_data_and_public_exception_text(self) -> None:
        data = {
            "api_key": "sk-nested-secret-123456",
            "messages": ["Authorization: Bearer abcdefghijklmnop", {"token": "plain-token"}],
        }

        redacted = redact_data(data)
        exception_text = public_exception_message("失败", RuntimeError("secret=top-secret-token"))

        serialized = json.dumps(redacted, ensure_ascii=False)
        self.assertNotIn("sk-nested-secret", serialized)
        self.assertNotIn("abcdefghijklmnop", serialized)
        self.assertNotIn("plain-token", serialized)
        self.assertIn("[redacted]", serialized)
        self.assertNotIn("top-secret-token", exception_text)
        self.assertIn("[redacted]", redact_text("bearer live-token-value"))

    def test_redaction_helpers_cover_json_style_secret_strings(self) -> None:
        text = (
            '{"access_token":"plain-access-token","secret":"plain-secret-value",'
            '"nested":{"refresh_token":"plain-refresh-token"}} token=plain-inline-token'
        )

        redacted = redact_text(text)

        self.assertIn("[redacted]", redacted)
        self.assertNotIn("plain-access-token", redacted)
        self.assertNotIn("plain-secret-value", redacted)
        self.assertNotIn("plain-refresh-token", redacted)
        self.assertNotIn("plain-inline-token", redacted)

    def test_redaction_helpers_cover_quoted_secret_values_with_spaces(self) -> None:
        text = (
            '{"password":"multi word password value",'
            '"api_key":"sk-value with spaces",'
            "'refresh_token': 'refresh token with spaces', "
            'secret="escaped \\" quote value"} '
            'Authorization: Bearer "quoted bearer token" '
            "bearer 'another bearer token'"
        )

        redacted = redact_text(text, limit=1000)

        self.assertIn("[redacted]", redacted)
        self.assertNotIn("multi word password value", redacted)
        self.assertNotIn("sk-value with spaces", redacted)
        self.assertNotIn("refresh token with spaces", redacted)
        self.assertNotIn('escaped \\" quote value', redacted)
        self.assertNotIn("quoted bearer token", redacted)
        self.assertNotIn("another bearer token", redacted)

    def test_redaction_helpers_cover_url_credentials_and_preserve_safe_query_params(self) -> None:
        text = (
            "GET https://user:plain-password@example.com/v1?"
            "api_key=sk-url-secret-123456&safe=value password=plain-inline-password"
        )

        redacted = redact_text(text, limit=1000)

        self.assertIn("[redacted]", redacted)
        self.assertIn("&safe=value", redacted)
        self.assertNotIn("user:plain-password", redacted)
        self.assertNotIn("sk-url-secret", redacted)
        self.assertNotIn("plain-inline-password", redacted)

    def test_validation_handler_logs_only_redacted_details(self) -> None:
        request = Request({"type": "http", "method": "POST", "path": "/api/test", "headers": []})
        exc = RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("body", "CHAT_API_KEY"),
                    "msg": "bad token=plain-secret-value",
                    "input": "sk-validation-secret-123456",
                }
            ]
        )

        with patch("backend.errors.logger.info") as logger_info:
            response = asyncio.run(validation_exception_handler(request, exc))

        body = json.loads(response.body)
        logged = logger_info.call_args.kwargs["extra"]
        serialized = json.dumps({"body": body, "logged": logged}, ensure_ascii=False)
        self.assertNotIn("sk-validation-secret", serialized)
        self.assertNotIn("plain-secret-value", serialized)
        self.assertIn("[redacted]", serialized)
        self.assertIn("[omitted]", serialized)

    def test_validation_handler_omits_private_input_but_serializes_safe_context(self) -> None:
        request = Request({"type": "http", "method": "POST", "path": "/api/test", "headers": []})
        exc = RequestValidationError(
            [
                {
                    "type": "value_error",
                    "loc": ("body", "field"),
                    "msg": "Value error",
                    "input": "private user question that should not be echoed",
                    "ctx": {"error": ValueError("api_key=sk-context-secret-123456")},
                }
            ]
        )

        response = asyncio.run(validation_exception_handler(request, exc))

        body = json.loads(response.body)
        serialized = json.dumps(body, ensure_ascii=False)
        self.assertNotIn("private user question", serialized)
        self.assertNotIn("sk-context-secret", serialized)
        self.assertIn("[omitted]", serialized)
        self.assertIn("[redacted]", serialized)

    def test_error_payload_redacts_message_action_and_details_centrally(self) -> None:
        payload = error_payload(
            status_code=400,
            code="BAD_CONFIG",
            message="bad api_key=sk-message-secret-123456",
            error_type="http_error",
            recoverable=True,
            action="retry with token=plain-action-token",
            details={"authorization": "Bearer detail-token-value"},
        )

        serialized = json.dumps(payload, ensure_ascii=False)
        self.assertNotIn("sk-message-secret", serialized)
        self.assertNotIn("plain-action-token", serialized)
        self.assertNotIn("detail-token-value", serialized)
        self.assertIn("[redacted]", serialized)

    def test_http_exception_handler_redacts_structured_action_before_frontend_toast(self) -> None:
        request = Request({"type": "http", "method": "POST", "path": "/api/test", "headers": []})
        exc = HTTPException(
            status_code=400,
            detail={
                "message": "upload failed",
                "action": "check api_key=sk-action-secret-123456 then retry",
            },
        )

        response = asyncio.run(http_exception_handler(request, exc))

        body = json.loads(response.body)
        serialized = json.dumps(body, ensure_ascii=False)
        self.assertNotIn("sk-action-secret", serialized)
        self.assertIn("[redacted]", serialized)


class HealthDiagnosticsTests(unittest.TestCase):
    def test_default_cors_origins_cover_vite_fallback_ports(self) -> None:
        self.assertIn("http://127.0.0.1:5173", _default_origins)
        self.assertIn("http://127.0.0.1:5174", _default_origins)
        self.assertIn("http://localhost:5180", _default_origins)

    def test_health_check_exposes_summary_and_vector_capability_fields(self) -> None:
        with patch(
            "backend.main._diagnostics",
            return_value={
                "overall": "ok",
                "checks": [],
                "db_stats": self._stats(total_messages=10, chunks=2),
                "chat_status": {"configured": True, "missing": [], "model": "chat"},
                "summary_status": {"configured": True, "missing": [], "model": "summary"},
                "embed_status": {"configured": True, "missing": [], "model": "embed"},
                "vector_index_available": True,
                "vector_search_available": False,
            },
        ):
            payload = health_check()

        self.assertTrue(payload["summary_model_configured"])
        self.assertEqual(payload["summary_model"], "summary")
        self.assertEqual(payload["summary_model_missing"], [])
        self.assertTrue(payload["embedding_configured"])
        self.assertTrue(payload["vector_index_available"])
        self.assertFalse(payload["vector_search_available"])

    def test_diagnostics_reports_usable_vector_search_when_models_and_vectors_are_ready(self) -> None:
        session_db = Mock()
        session_db.execute.return_value.fetchone.return_value = (1,)
        with (
            patch("backend.main.store.stats_summary", return_value=self._stats(total_messages=100, chunks=5)) as stats_summary,
            patch("backend.main.session_store.db", return_value=session_db),
            patch("backend.main.chat_config_status", return_value={"configured": True, "missing": [], "model": "chat"}),
            patch("backend.main.embed_config_status", return_value={"configured": True, "missing": [], "model": "embed"}),
            patch("backend.main.store.has_vec", return_value=True),
            patch("backend.main.store.count_sessions_without_embedding", return_value=0),
        ):
            diagnostics = _diagnostics()

        self.assertEqual(diagnostics["overall"], "ok")
        self.assertTrue(diagnostics["vector_search_available"])
        self.assertTrue(all(check["status"] == "ok" for check in diagnostics["checks"]))
        stats_summary.assert_called_once_with(include_message_types=False)

    def test_diagnostics_explains_missing_embedding_configuration_without_blocking_chat(self) -> None:
        session_db = Mock()
        session_db.execute.return_value.fetchone.return_value = (1,)
        with (
            patch("backend.main.store.stats_summary", return_value=self._stats(total_messages=100, chunks=5)),
            patch("backend.main.session_store.db", return_value=session_db),
            patch("backend.main.chat_config_status", return_value={"configured": True, "missing": [], "model": "chat"}),
            patch(
                "backend.main.embed_config_status",
                return_value={"configured": False, "missing": ["EMBED_API_KEY"], "model": ""},
            ),
            patch("backend.main.store.has_vec", return_value=True),
            patch("backend.main.store.count_sessions_without_embedding", return_value=5),
        ):
            diagnostics = _diagnostics()

        self.assertEqual(diagnostics["overall"], "degraded")
        self.assertFalse(diagnostics["vector_search_available"])
        embedding = self._check(diagnostics, "embedding_model")
        vector = self._check(diagnostics, "vector_index")
        self.assertIsNone(embedding["action_target"])
        self.assertEqual(vector["status"], "warning")
        self.assertIn("EMBED_API_KEY", vector["action"])
        self.assertIsNone(vector["action_target"])

    def test_diagnostics_routes_only_model_name_chat_config_to_settings(self) -> None:
        session_db = Mock()
        session_db.execute.return_value.fetchone.return_value = (1,)
        with (
            patch("backend.main.store.stats_summary", return_value=self._stats(total_messages=100, chunks=5)),
            patch("backend.main.session_store.db", return_value=session_db),
            patch(
                "backend.main.chat_config_status",
                return_value={"configured": False, "missing": ["CHAT_MODEL"], "model": ""},
            ),
            patch("backend.main.embed_config_status", return_value={"configured": True, "missing": [], "model": "embed"}),
            patch("backend.main.store.has_vec", return_value=True),
            patch("backend.main.store.count_sessions_without_embedding", return_value=0),
        ):
            diagnostics = _diagnostics()

        chat_model = self._check(diagnostics, "chat_model")
        self.assertEqual(chat_model["status"], "error")
        self.assertEqual(chat_model["action_target"], "settings")
        self.assertIn("设置页", chat_model["action"])

    def test_diagnostics_does_not_route_secret_chat_config_to_settings(self) -> None:
        session_db = Mock()
        session_db.execute.return_value.fetchone.return_value = (1,)
        with (
            patch("backend.main.store.stats_summary", return_value=self._stats(total_messages=100, chunks=5)),
            patch("backend.main.session_store.db", return_value=session_db),
            patch(
                "backend.main.chat_config_status",
                return_value={"configured": False, "missing": ["CHAT_BASE_URL", "CHAT_API_KEY"], "model": "chat"},
            ),
            patch("backend.main.embed_config_status", return_value={"configured": True, "missing": [], "model": "embed"}),
            patch("backend.main.store.has_vec", return_value=True),
            patch("backend.main.store.count_sessions_without_embedding", return_value=0),
        ):
            diagnostics = _diagnostics()

        chat_model = self._check(diagnostics, "chat_model")
        self.assertEqual(chat_model["status"], "error")
        self.assertIsNone(chat_model["action_target"])
        self.assertIn("CHAT_API_KEY", chat_model["action"])

    def test_diagnostics_warns_when_no_chat_records_have_been_imported(self) -> None:
        session_db = Mock()
        session_db.execute.return_value.fetchone.return_value = (1,)
        with (
            patch("backend.main.store.stats_summary", return_value=self._stats(total_messages=0, chunks=0)),
            patch("backend.main.session_store.db", return_value=session_db),
            patch("backend.main.chat_config_status", return_value={"configured": True, "missing": [], "model": "chat"}),
            patch("backend.main.embed_config_status", return_value={"configured": True, "missing": [], "model": "embed"}),
            patch("backend.main.store.has_vec", return_value=True),
            patch("backend.main.store.count_sessions_without_embedding", return_value=0) as missing_vectors,
        ):
            diagnostics = _diagnostics()

        database = self._check(diagnostics, "database")
        missing_vectors.assert_not_called()
        self.assertEqual(diagnostics["overall"], "degraded")
        self.assertEqual(database["status"], "warning")
        self.assertTrue(database["recoverable"])
        self.assertIn("尚未导入聊天记录", database["detail"])
        self.assertIn("数据导入", database["action"])
        self.assertEqual(database["action_target"], "ingest")

    def test_diagnostics_warns_when_messages_exist_without_session_chunks(self) -> None:
        session_db = Mock()
        session_db.execute.return_value.fetchone.return_value = (1,)
        with (
            patch("backend.main.store.stats_summary", return_value=self._stats(total_messages=100, chunks=0)),
            patch("backend.main.session_store.db", return_value=session_db),
            patch("backend.main.chat_config_status", return_value={"configured": True, "missing": [], "model": "chat"}),
            patch("backend.main.embed_config_status", return_value={"configured": True, "missing": [], "model": "embed"}),
            patch("backend.main.store.has_vec", return_value=True),
            patch("backend.main.store.count_sessions_without_embedding", return_value=0) as missing_vectors,
        ):
            diagnostics = _diagnostics()

        vector = self._check(diagnostics, "vector_index")
        missing_vectors.assert_not_called()
        self.assertEqual(diagnostics["overall"], "degraded")
        self.assertFalse(diagnostics["vector_search_available"])
        self.assertEqual(vector["status"], "warning")
        self.assertIn("尚未构建会话块", vector["detail"])
        self.assertIn("仅分块", vector["action"])
        self.assertEqual(vector["action_target"], "ingest")

    def test_diagnostics_points_ready_missing_vectors_to_vector_only_ingest(self) -> None:
        session_db = Mock()
        session_db.execute.return_value.fetchone.return_value = (1,)
        with (
            patch("backend.main.store.stats_summary", return_value=self._stats(total_messages=100, chunks=5)),
            patch("backend.main.session_store.db", return_value=session_db),
            patch("backend.main.chat_config_status", return_value={"configured": True, "missing": [], "model": "chat"}),
            patch("backend.main.embed_config_status", return_value={"configured": True, "missing": [], "model": "embed"}),
            patch("backend.main.store.has_vec", return_value=True),
            patch("backend.main.store.count_sessions_without_embedding", return_value=5),
        ):
            diagnostics = _diagnostics()

        vector = self._check(diagnostics, "vector_index")
        self.assertEqual(diagnostics["overall"], "degraded")
        self.assertFalse(diagnostics["vector_search_available"])
        self.assertEqual(vector["status"], "warning")
        self.assertIn("0/5", vector["detail"])
        self.assertIn("仅向量", vector["action"])
        self.assertEqual(vector["action_target"], "ingest")

    def test_diagnostics_redacts_database_errors_and_marks_them_recoverable(self) -> None:
        session_db = Mock()
        session_db.execute.return_value.fetchone.return_value = (1,)
        with (
            patch("backend.main.store.stats_summary", side_effect=RuntimeError("api_key=sk-db-secret-123456")),
            patch("backend.main.session_store.db", return_value=session_db),
            patch("backend.main.chat_config_status", return_value={"configured": True, "missing": [], "model": "chat"}),
            patch("backend.main.embed_config_status", return_value={"configured": True, "missing": [], "model": "embed"}),
            patch("backend.main.store.has_vec", return_value=False),
        ):
            diagnostics = _diagnostics()

        database = self._check(diagnostics, "database")
        serialized = json.dumps(diagnostics, ensure_ascii=False)
        self.assertEqual(diagnostics["overall"], "error")
        self.assertEqual(database["status"], "error")
        self.assertTrue(database["recoverable"])
        self.assertIsNone(database["action_target"])
        self.assertNotIn("sk-db-secret", serialized)
        self.assertIn("[redacted]", serialized)

    @staticmethod
    def _stats(total_messages: int, chunks: int) -> dict:
        return {
            "total_messages": total_messages,
            "indexed_session_chunks": chunks,
            "thread_count": 3,
            "sender_count": 4,
            "time_span": {"earliest": "2025-01-01", "latest": "2025-01-02"},
        }

    @staticmethod
    def _check(diagnostics: dict, component: str) -> dict:
        return next(check for check in diagnostics["checks"] if check["component"] == component)


if __name__ == "__main__":
    unittest.main()
