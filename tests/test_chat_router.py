from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import HTTPException
from langchain_core.messages import AIMessage, SystemMessage
from pydantic import ValidationError

import core.agent as agent_module
from backend import session_store
from backend.agent_stream import ABORTED_ANSWER, _tool_args_preview, _tool_result_summary, stream_agent
from backend.routers import chat
from backend.routers.chat import AbortSessionRequest, BatchDeleteSessionsRequest
from backend.schemas import ChatRequest


class ChatRouterTests(unittest.TestCase):
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

    def test_stream_and_persist_saves_done_answer_and_finishes_session(self) -> None:
        session_store.get_or_create_session("done-session")
        self.assertTrue(session_store.try_begin("done-session"))

        with patch("backend.routers.chat.stream_agent", return_value=self._events(("text", {"chunk": "hello"}), ("done", {"answer": "hello"}))):
            payloads = list(chat._stream_and_persist("question", [], "done-session"))

        self.assertTrue(any("event: done" in payload for payload in payloads))
        messages = session_store.get_messages("done-session")
        self.assertEqual([row["role"] for row in messages], ["user", "assistant"])
        self.assertEqual(messages[-1]["content"], "hello")
        self.assertEqual(session_store.get_session("done-session")["status"], "idle")

    def test_disconnect_after_done_event_keeps_saved_answer_successful(self) -> None:
        session_store.get_or_create_session("done-close-session")
        self.assertTrue(session_store.try_begin("done-close-session"))

        with patch("backend.routers.chat.stream_agent", return_value=self._events(("done", {"answer": "hello"}))):
            generator = chat._stream_and_persist("question", [], "done-close-session")
            next(generator)
            done_payload = next(generator)
            self.assertIn("event: done", done_payload)
            generator.close()

        messages = session_store.get_messages("done-close-session")
        session = session_store.get_session("done-close-session")
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[-1]["content"], "hello")
        self.assertEqual(session["status"], "idle")
        self.assertIsNone(session["last_error"])

    def test_disconnect_after_error_event_keeps_session_error_status(self) -> None:
        session_store.get_or_create_session("error-close-session")
        self.assertTrue(session_store.try_begin("error-close-session"))

        with patch("backend.routers.chat.stream_agent", return_value=self._events(("error", {"detail": "model unavailable"}))):
            generator = chat._stream_and_persist("question", [], "error-close-session")
            next(generator)
            error_payload = next(generator)
            self.assertIn("event: error", error_payload)
            generator.close()

        session = session_store.get_session("error-close-session")
        self.assertEqual(session["status"], "error")
        self.assertEqual(session["last_error"], "model unavailable")
        self.assertEqual(session_store.count_messages("error-close-session"), 0)

    def test_stream_and_persist_does_not_duplicate_abort_exchange_saved_by_stop_request(self) -> None:
        session_store.get_or_create_session("abort-session")
        self.assertTrue(session_store.try_begin("abort-session"))
        chat.abort_session(
            "abort-session",
            AbortSessionRequest(question="question", partial_answer="partial answer"),
        )

        with patch("backend.routers.chat.stream_agent", return_value=self._events(("text", {"chunk": ABORTED_ANSWER}), ("done", {"answer": ABORTED_ANSWER}))):
            list(chat._stream_and_persist("question", [], "abort-session"))

        messages = session_store.get_messages("abort-session")
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["content"], "question")
        self.assertIn("partial answer", messages[1]["content"])
        self.assertIn(chat.STOPPED_MARKER, messages[1]["content"])
        self.assertEqual(session_store.get_session("abort-session")["status"], "idle")

    def test_stream_and_persist_ignores_late_done_after_stop_request_saved_partial(self) -> None:
        session_store.get_or_create_session("late-done-stop-session")
        self.assertTrue(session_store.try_begin("late-done-stop-session"))
        chat.abort_session(
            "late-done-stop-session",
            AbortSessionRequest(question="question", partial_answer="partial answer"),
        )

        with patch(
            "backend.routers.chat.stream_agent",
            return_value=self._events(("text", {"chunk": "late full answer"}), ("done", {"answer": "late full answer"})),
        ):
            list(chat._stream_and_persist("question", [], "late-done-stop-session"))

        messages = session_store.get_messages("late-done-stop-session")
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["content"], "question")
        self.assertIn("partial answer", messages[1]["content"])
        self.assertIn(chat.STOPPED_MARKER, messages[1]["content"])
        self.assertNotIn("late full answer", messages[1]["content"])
        self.assertEqual(session_store.get_session("late-done-stop-session")["status"], "idle")

    def test_stream_error_after_stop_request_finishes_idle_without_duplicate_exchange(self) -> None:
        session_store.get_or_create_session("late-error-stop-session")
        self.assertTrue(session_store.try_begin("late-error-stop-session"))
        chat.abort_session(
            "late-error-stop-session",
            AbortSessionRequest(question="question", partial_answer="partial answer"),
        )

        with patch(
            "backend.routers.chat.stream_agent",
            return_value=self._events(("error", {"detail": "model unavailable after stop"})),
        ):
            payloads = list(chat._stream_and_persist("question", [], "late-error-stop-session"))

        self.assertFalse(any("event: error" in payload for payload in payloads))
        messages = session_store.get_messages("late-error-stop-session")
        self.assertEqual(len(messages), 2)
        self.assertEqual(messages[0]["content"], "question")
        self.assertIn("partial answer", messages[1]["content"])
        self.assertIn(chat.STOPPED_MARKER, messages[1]["content"])
        session = session_store.get_session("late-error-stop-session")
        self.assertEqual(session["status"], "idle")
        self.assertIsNone(session["last_error"])

    def test_reasking_same_stopped_question_can_persist_new_answer(self) -> None:
        session_store.append_exchange("retry-stopped-session", "question", chat._stopped_answer("partial answer"))
        self.assertTrue(session_store.try_begin("retry-stopped-session"))

        with patch(
            "backend.routers.chat.stream_agent",
            return_value=self._events(("text", {"chunk": "fresh answer"}), ("done", {"answer": "fresh answer"})),
        ):
            list(chat._stream_and_persist("question", [], "retry-stopped-session"))

        messages = session_store.get_messages("retry-stopped-session")
        self.assertEqual(len(messages), 4)
        self.assertEqual(messages[-2]["content"], "question")
        self.assertEqual(messages[-1]["content"], "fresh answer")

    def test_stream_error_marks_session_error_without_writing_exchange(self) -> None:
        session_store.get_or_create_session("error-session")
        self.assertTrue(session_store.try_begin("error-session"))

        with patch("backend.routers.chat.stream_agent", return_value=self._events(("error", {"detail": "model unavailable"}))):
            payloads = list(chat._stream_and_persist("question", [], "error-session"))

        self.assertTrue(any("event: error" in payload for payload in payloads))
        session = session_store.get_session("error-session")
        self.assertEqual(session["status"], "error")
        self.assertEqual(session["last_error"], "model unavailable")
        self.assertEqual(session_store.count_messages("error-session"), 0)

    def test_stream_error_redacts_session_last_error(self) -> None:
        session_store.get_or_create_session("secret-error-session")
        self.assertTrue(session_store.try_begin("secret-error-session"))

        with patch(
            "backend.routers.chat.stream_agent",
            return_value=self._events(("error", {"detail": "api_key=sk-chat-secret-123456"})),
        ):
            list(chat._stream_and_persist("question", [], "secret-error-session"))

        session = session_store.get_session("secret-error-session")
        self.assertNotIn("sk-chat-secret", session["last_error"])
        self.assertIn("[redacted]", session["last_error"])

    def test_stream_error_redacts_error_payload_sent_to_frontend(self) -> None:
        session_store.get_or_create_session("secret-error-payload-session")
        self.assertTrue(session_store.try_begin("secret-error-payload-session"))

        with patch(
            "backend.routers.chat.stream_agent",
            return_value=self._events(("error", {"detail": "Authorization: Bearer raw-chat-token"})),
        ):
            payloads = list(chat._stream_and_persist("question", [], "secret-error-payload-session"))

        serialized = "".join(payloads)
        self.assertIn("event: error", serialized)
        self.assertIn("[redacted]", serialized)
        self.assertNotIn("raw-chat-token", serialized)
        session = session_store.get_session("secret-error-payload-session")
        self.assertNotIn("raw-chat-token", session["last_error"])

    def test_malformed_stream_error_payload_is_not_forwarded_to_frontend(self) -> None:
        session_store.get_or_create_session("malformed-error-payload-session")
        self.assertTrue(session_store.try_begin("malformed-error-payload-session"))

        with patch(
            "backend.routers.chat.stream_agent",
            return_value=["event: error\ndata: api_key=sk-malformed-error-secret-123456\n\n"],
        ):
            payloads = list(chat._stream_and_persist("question", [], "malformed-error-payload-session"))

        serialized = "".join(payloads)
        self.assertIn("event: error", serialized)
        self.assertIn("Agent 执行失败", serialized)
        self.assertNotIn("sk-malformed-error-secret", serialized)
        session = session_store.get_session("malformed-error-payload-session")
        self.assertEqual(session["last_error"], "Agent 执行失败")

    def test_generator_exit_after_disconnect_releases_running_session(self) -> None:
        session_store.get_or_create_session("disconnect-session")
        self.assertTrue(session_store.try_begin("disconnect-session"))

        generator = chat._stream_and_persist("question", [], "disconnect-session")
        next(generator)
        generator.close()

        session = session_store.get_session("disconnect-session")
        self.assertEqual(session["status"], "idle")
        self.assertEqual(session["last_error"], "client disconnected")

    def test_abort_after_disconnect_can_save_stopped_partial_answer_once(self) -> None:
        session_store.get_or_create_session("late-abort-session")
        session_store.finish("late-abort-session", "idle", "client disconnected")

        first = chat.abort_session(
            "late-abort-session",
            AbortSessionRequest(question="question", partial_answer="partial answer"),
        )
        second = chat.abort_session(
            "late-abort-session",
            AbortSessionRequest(question="question", partial_answer="partial answer"),
        )

        self.assertEqual(first["status"], "idle")
        self.assertEqual(second["status"], "idle")
        messages = session_store.get_messages("late-abort-session")
        self.assertEqual(len(messages), 2)
        self.assertIn("partial answer", messages[1]["content"])
        self.assertIn(chat.STOPPED_MARKER, messages[1]["content"])

    def test_abort_missing_session_returns_404(self) -> None:
        with self.assertRaises(HTTPException) as exc:
            chat.abort_session("missing", AbortSessionRequest(question="q", partial_answer="a"))

        self.assertEqual(exc.exception.status_code, 404)

    def test_chat_request_trims_session_id_and_rejects_blank_session_id(self) -> None:
        req = ChatRequest(question=" hello ", session_id="  session-1  ")

        self.assertEqual(req.question, "hello")
        self.assertEqual(req.session_id, "session-1")

        with self.assertRaises(ValidationError):
            ChatRequest(question="hello", session_id="   ")

    def test_chat_request_trims_before_length_validation(self) -> None:
        req = ChatRequest(question=f" {'x' * 8000} ", session_id=f" {'s' * 120} ")

        self.assertEqual(len(req.question), 8000)
        self.assertEqual(len(req.session_id or ""), 120)

    def test_batch_delete_rejects_blank_or_unbounded_session_ids(self) -> None:
        req = BatchDeleteSessionsRequest(session_ids=[" session-1 ", "session-2"])

        self.assertEqual(req.session_ids, ["session-1", "session-2"])

        with self.assertRaises(ValidationError):
            BatchDeleteSessionsRequest(session_ids=[""])

        with self.assertRaises(ValidationError):
            BatchDeleteSessionsRequest(session_ids=["x" * 121])

    def test_batch_delete_trims_before_session_id_length_validation(self) -> None:
        req = BatchDeleteSessionsRequest(session_ids=[f" {'s' * 120} "])

        self.assertEqual(req.session_ids, ["s" * 120])

    def test_abort_request_trims_question_and_rejects_blank_question(self) -> None:
        req = AbortSessionRequest(question=" question ", partial_answer="answer")

        self.assertEqual(req.question, "question")

        with self.assertRaises(ValidationError):
            AbortSessionRequest(question="   ", partial_answer="answer")

    def test_abort_request_trims_before_question_length_validation(self) -> None:
        req = AbortSessionRequest(question=f" {'x' * 8000} ", partial_answer="answer")

        self.assertEqual(len(req.question or ""), 8000)

    def test_abort_request_trims_and_caps_partial_answer_before_length_validation(self) -> None:
        req = AbortSessionRequest(question="question", partial_answer=f" {'x' * 25000} ")

        self.assertEqual(req.partial_answer, "x" * chat.MAX_PARTIAL_ANSWER_CHARS)

    def test_rename_request_trims_before_title_length_validation(self) -> None:
        req = chat.RenameSessionRequest(title=f" {'x' * 120} ")

        self.assertEqual(len(req.title), 120)

    def test_rename_active_session_returns_409(self) -> None:
        session_store.append_exchange("active-rename-route", "original question", "answer")
        self.assertTrue(session_store.try_begin("active-rename-route"))

        with self.assertRaises(HTTPException) as exc:
            chat.rename_session("active-rename-route", chat.RenameSessionRequest(title="new title"))

        self.assertEqual(exc.exception.status_code, 409)
        session = session_store.get_session("active-rename-route")
        self.assertIsNotNone(session)
        assert session is not None
        self.assertEqual(session["title"], "original question")
        self.assertEqual(session["status"], "running")

    def test_tool_args_preview_marks_truncated_payloads(self) -> None:
        preview = _tool_args_preview({"keyword": "a" * 80}, limit=40)

        self.assertLessEqual(len(preview), 40)
        self.assertTrue(preview.endswith("…"))

    def test_tool_args_preview_handles_non_json_values(self) -> None:
        preview = _tool_args_preview({"ids": {1, 2, 3}}, limit=80)

        self.assertIn("ids", preview)

    def test_tool_args_preview_redacts_secret_like_values(self) -> None:
        preview = _tool_args_preview(
            {
                "query": "Authorization: Bearer raw-live-token",
                "api_key": "sk-stream-secret-123456",
            },
            limit=200,
        )

        self.assertIn("[redacted]", preview)
        self.assertNotIn("raw-live-token", preview)
        self.assertNotIn("sk-stream-secret", preview)

    def test_tool_result_summary_surfaces_redacted_tool_errors(self) -> None:
        summary = _tool_result_summary("工具执行错误：RuntimeError：api_key=sk-tool-result-secret-123456 failed")

        self.assertIn("工具执行错误：RuntimeError", summary)
        self.assertIn("[redacted]", summary)
        self.assertNotIn("sk-tool-result-secret", summary)
        self.assertEqual(_tool_result_summary("plain non-json output"), "工具调用已完成")

    def test_tool_result_summary_marks_structured_json_errors_as_errors(self) -> None:
        summary = _tool_result_summary(
            '{"error":"查无此消息 id：missing-message api_key=sk-json-tool-secret-123456"}'
        )

        self.assertTrue(summary.startswith("工具执行错误："))
        self.assertIn("查无此消息", summary)
        self.assertIn("[redacted]", summary)
        self.assertNotIn("sk-json-tool-secret", summary)

    def test_stream_agent_handles_malformed_tool_call_without_crashing(self) -> None:
        fake_model = _FakeStreamingChatModel()

        with patch("backend.agent_stream.chat_model", return_value=fake_model):
            payloads = list(stream_agent(" question ", [], "malformed-tool-session"))

        event_names = [chat._parse_sse_event(payload)[0] for payload in payloads]
        self.assertIn("tool_call", event_names)
        self.assertIn("tool_result", event_names)
        self.assertIn("done", event_names)
        tool_payload = next(data for event, data in map(chat._parse_sse_event, payloads) if event == "tool_call")
        self.assertEqual(tool_payload["name"], "unknown_tool")

    def test_stream_agent_sends_runtime_tool_policy_to_model(self) -> None:
        fake_model = _PromptCapturingStreamModel()
        old_enabled = list(agent_module.ENABLED_TOOLS)
        old_prompt = agent_module.SYSTEM_PROMPT
        agent_module.ENABLED_TOOLS = ["search_messages"]
        agent_module.SYSTEM_PROMPT = "Use semantic_search when the question is fuzzy."

        try:
            with patch("backend.agent_stream.chat_model", return_value=fake_model):
                payloads = list(stream_agent(" question ", [], "prompt-policy-session"))
        finally:
            agent_module.ENABLED_TOOLS = old_enabled
            agent_module.SYSTEM_PROMPT = old_prompt

        event_names = [chat._parse_sse_event(payload)[0] for payload in payloads]
        self.assertIn("done", event_names)
        self.assertEqual([tool.name for tool in fake_model.bound_tools], ["search_messages"])
        self.assertIsInstance(fake_model.messages[0], SystemMessage)
        system_prompt = str(fake_model.messages[0].content)
        self.assertIn("Use semantic_search when the question is fuzzy.", system_prompt)
        self.assertIn("当前启用工具：search_messages", system_prompt)
        self.assertIn("当前停用工具：semantic_search", system_prompt)
        self.assertIn("优先级高于上方提示词", system_prompt)
        self.assertIn("只能调用当前启用工具", system_prompt)

    @staticmethod
    def _events(*events: tuple[str, dict[str, object]]):
        return [f"event: {event}\ndata: {chat.json.dumps(data, ensure_ascii=False)}\n\n" for event, data in events]


class _MalformedToolMessage:
    content = ""
    tool_calls = [{"id": "call-1", "args": {"query": "Project"}}]


class _FakeStreamingChatModel:
    def __init__(self) -> None:
        self.calls = 0

    def bind_tools(self, _tools: object) -> "_FakeStreamingChatModel":
        return self

    def invoke(self, _messages: object) -> object:
        self.calls += 1
        if self.calls == 1:
            return _MalformedToolMessage()
        return AIMessage(content="finished")


class _PromptCapturingStreamModel:
    def __init__(self) -> None:
        self.bound_tools: list[object] = []
        self.messages: list[object] = []

    def bind_tools(self, tools_arg: object) -> "_PromptCapturingStreamModel":
        self.bound_tools = list(tools_arg)  # type: ignore[arg-type]
        return self

    def invoke(self, messages: object) -> AIMessage:
        self.messages = list(messages)  # type: ignore[arg-type]
        return AIMessage(content="stream ok")


if __name__ == "__main__":
    unittest.main()
