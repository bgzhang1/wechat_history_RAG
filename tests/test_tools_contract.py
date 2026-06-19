from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from pydantic import ValidationError

from core import agent, store, tools
from core.parser import NormMessage


class ToolsContractTests(unittest.TestCase):
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

    def test_search_tool_returns_json_and_escapes_like_wildcards(self) -> None:
        percent = json.loads(tools.search_messages.invoke({"query": "100%", "limit": 10}))
        underscore = json.loads(tools.search_messages.invoke({"query": "Road_", "limit": 10}))

        self.assertEqual(percent["total_count"], 1)
        self.assertEqual(percent["messages"][0]["message_id"], "m1")
        self.assertEqual(underscore["total_count"], 1)
        self.assertEqual(underscore["messages"][0]["message_id"], "m3")

    def test_browse_tool_respects_date_boundaries_and_sender_self_alias(self) -> None:
        result = json.loads(
            tools.browse_by_time.invoke(
                {
                    "after": "2024-02-02T00:00:00Z",
                    "before": "2024-02-02 23:59:59+08:00",
                    "sender": " 自己 ",
                    "limit": 10,
                }
            )
        )

        self.assertEqual(result["total_count"], 1)
        self.assertEqual(result["messages"][0]["message_id"], "m3")
        self.assertEqual(result["messages"][0]["sender"], "Me(我)")

        search = json.loads(tools.search_messages.invoke({"query": "Road", "sender": "me", "limit": 10}))
        self.assertEqual(search["total_count"], 1)
        self.assertEqual(search["messages"][0]["message_id"], "m3")

    def test_time_args_accept_and_normalize_common_iso_timezone_suffixes(self) -> None:
        args = tools.BrowseArgs.model_validate(
            {
                "after": "2024-02-02T00:00:00.000Z",
                "before": "2024-02-02T23:59:59.999+08:00",
            }
        )

        self.assertEqual(args.after, "2024-02-02T00:00:00")
        self.assertEqual(args.before, "2024-02-02T23:59:59")

        date_only = tools.BrowseArgs.model_validate({"after": "2024-02-02", "before": "2024-02-02"})
        self.assertEqual(date_only.after, "2024-02-02T00:00:00")
        self.assertEqual(date_only.before, "2024-02-02T23:59:59")

        minute_only = tools.SearchArgs.model_validate({"query": "Road", "after": "2024-02-02 10:00"})
        self.assertEqual(minute_only.after, "2024-02-02T10:00:00")

    def test_context_tool_includes_quoted_message_when_reply_target_exists(self) -> None:
        result = json.loads(tools.get_context.invoke({"message_id": "m2", "before": 0, "after": 0}))

        self.assertEqual(result["center_message_id"], "m2")
        self.assertEqual([message["message_id"] for message in result["messages"]], ["m2"])
        self.assertEqual(result["quoted_message"]["message_id"], "m1")

    def test_tool_arg_schema_rejects_invalid_time_before_database_work(self) -> None:
        with self.assertRaises(ValidationError):
            tools.SearchArgs.model_validate({"query": "budget", "after": "yesterday"})

        with self.assertRaises(ValidationError):
            tools.BrowseArgs.model_validate({"after": "2024-02-01", "before": "02/02/2024"})

        with self.assertRaises(ValidationError):
            tools.SearchArgs.model_validate({"query": "budget", "after": "2024-99-01"})

        with self.assertRaises(ValidationError):
            tools.BrowseArgs.model_validate({"after": "2024-02-03", "before": "2024-02-02"})

    def test_tool_arg_schema_rejects_unbounded_text_inputs_before_database_work(self) -> None:
        with self.assertRaises(ValidationError):
            tools.SearchArgs.model_validate({"query": "x" * (tools.MAX_TOOL_QUERY_CHARS + 1)})

        with self.assertRaises(ValidationError):
            tools.SemanticArgs.model_validate({"query": "x" * (tools.MAX_SEMANTIC_QUERY_CHARS + 1)})

        with self.assertRaises(ValidationError):
            tools.SearchArgs.model_validate({"query": "budget", "sender": "x" * (tools.MAX_FILTER_CHARS + 1)})

        with self.assertRaises(ValidationError):
            tools.ContextArgs.model_validate({"message_id": "m" * (tools.MAX_MESSAGE_ID_CHARS + 1)})

    def test_context_arg_schema_allows_realistic_long_scoped_message_ids(self) -> None:
        long_message_id = f"{'nested account path/' * 14}chat file.json:platform id  with spaces"
        self.assertGreater(len(long_message_id), 240)
        self.assertLessEqual(len(long_message_id), tools.MAX_MESSAGE_ID_CHARS)

        args = tools.ContextArgs.model_validate(
            {"message_id": f"  {long_message_id}  ", "before": 0, "after": 0}
        )

        self.assertEqual(args.message_id, long_message_id)
        self.assertIn("id  with", args.message_id)

    def test_tool_arg_schema_rejects_unbounded_result_windows_before_database_work(self) -> None:
        with self.assertRaises(ValidationError):
            tools.SearchArgs.model_validate({"query": "budget", "limit": 101})

        with self.assertRaises(ValidationError):
            tools.SemanticArgs.model_validate({"query": "budget planning", "limit": 21})

        with self.assertRaises(ValidationError):
            tools.ContextArgs.model_validate({"message_id": "m1", "before": 51})

        with self.assertRaises(ValidationError):
            tools.BrowseArgs.model_validate({"after": "2024-02-01", "before": "2024-02-02", "limit": 201})

    def test_tool_arg_schema_normalizes_blank_and_spaced_text_inputs(self) -> None:
        with self.assertRaises(ValidationError):
            tools.SearchArgs.model_validate({"query": "   \t  "})

        with self.assertRaises(ValidationError):
            tools.ContextArgs.model_validate({"message_id": "   "})

        args = tools.SearchArgs.model_validate(
            {
                "query": "  Project    100%  ",
                "sender": "   ",
                "thread": "  Project   100%  ",
            }
        )
        self.assertEqual(args.query, "Project 100%")
        self.assertIsNone(args.sender)
        self.assertEqual(args.thread, "Project 100%")

        result = json.loads(tools.search_messages.invoke(args.model_dump(exclude_none=True) | {"limit": 10}))
        self.assertEqual(result["total_count"], 1)
        self.assertEqual(result["messages"][0]["message_id"], "m1")

    def test_optional_time_filters_treat_blank_strings_as_unset(self) -> None:
        search_args = tools.SearchArgs.model_validate({"query": "Project", "after": " ", "before": ""})
        semantic_args = tools.SemanticArgs.model_validate({"query": "project planning", "after": "", "before": " "})

        self.assertIsNone(search_args.after)
        self.assertIsNone(search_args.before)
        self.assertIsNone(semantic_args.after)
        self.assertIsNone(semantic_args.before)

        result = json.loads(tools.search_messages.invoke(search_args.model_dump(exclude_none=True) | {"limit": 10}))
        self.assertEqual(result["total_count"], 1)
        self.assertEqual(result["messages"][0]["message_id"], "m1")

    def test_agent_tool_error_includes_safe_actionable_validation_detail(self) -> None:
        result = agent._run_tool_call(
            {
                "id": "tool-call-1",
                "name": "browse_by_time",
                "args": {"after": "2024-02-03", "before": "2024-02-02"},
            }
        )

        self.assertIn("工具执行错误：ValidationError", str(result.content))
        self.assertIn("起始时间不能晚于结束时间", str(result.content))

    def test_agent_tool_error_explains_non_object_json_tool_args(self) -> None:
        result = agent._run_tool_call(
            {
                "id": "tool-call-array",
                "name": "search_messages",
                "args": '["Project"]',
            }
        )

        self.assertIn("工具执行错误：ValueError", str(result.content))
        self.assertIn("工具参数必须是 JSON 对象", str(result.content))

    def test_agent_tool_error_redacts_secret_like_details(self) -> None:
        message = agent._safe_error_detail(RuntimeError("api_key=sk-tool-secret-123456 bearer live-token-value"))

        self.assertIn("[redacted]", message)
        self.assertNotIn("sk-tool-secret", message)
        self.assertNotIn("live-token-value", message)

    def test_agent_tool_args_preview_redacts_secret_like_details(self) -> None:
        preview = agent._tool_args_preview(
            {
                "query": "bearer live-preview-token",
                "access_token": "plain-access-token",
            }
        )

        self.assertIn("[redacted]", preview)
        self.assertNotIn("live-preview-token", preview)
        self.assertNotIn("plain-access-token", preview)

    def test_disabled_tool_call_is_rejected_at_execution_layer(self) -> None:
        old_enabled = list(agent.ENABLED_TOOLS)
        agent.ENABLED_TOOLS = ["get_stats"]
        try:
            result = agent._run_tool_call(
                {
                    "id": "tool-call-disabled",
                    "name": "search_messages",
                    "args": {"query": "Project", "limit": 10},
                }
            )
        finally:
            agent.ENABLED_TOOLS = old_enabled

        self.assertIn("当前未启用", str(result.content))
        self.assertIn("search_messages", str(result.content))

    def test_runtime_system_prompt_reflects_enabled_and_disabled_tools(self) -> None:
        old_enabled = list(agent.ENABLED_TOOLS)
        agent.ENABLED_TOOLS = ["search_messages", "get_context"]
        try:
            prompt = agent.build_system_prompt()
        finally:
            agent.ENABLED_TOOLS = old_enabled

        self.assertIn("当前启用工具：search_messages, get_context", prompt)
        self.assertIn("当前停用工具：semantic_search", prompt)
        self.assertIn("优先级高于上方提示词", prompt)
        self.assertIn("只能调用当前启用工具", prompt)

    def test_run_agent_sends_runtime_tool_policy_to_model(self) -> None:
        fake_model = _PromptCapturingChatModel()
        old_enabled = list(agent.ENABLED_TOOLS)
        old_prompt = agent.SYSTEM_PROMPT
        agent.ENABLED_TOOLS = ["search_messages"]
        agent.SYSTEM_PROMPT = "Use semantic_search when the question is fuzzy."

        try:
            with patch("core.agent.chat_model", return_value=fake_model):
                answer = agent.run_agent("Who mentioned Project?", [], verbose=False)
        finally:
            agent.ENABLED_TOOLS = old_enabled
            agent.SYSTEM_PROMPT = old_prompt

        self.assertEqual(answer, "ok")
        self.assertEqual([tool.name for tool in fake_model.bound_tools], ["search_messages"])
        self.assertIsInstance(fake_model.messages[0], SystemMessage)
        system_prompt = str(fake_model.messages[0].content)
        self.assertIn("Use semantic_search when the question is fuzzy.", system_prompt)
        self.assertIn("当前启用工具：search_messages", system_prompt)
        self.assertIn("当前停用工具：semantic_search", system_prompt)
        self.assertIn("优先级高于上方提示词", system_prompt)
        self.assertIn("只能调用当前启用工具", system_prompt)

    def test_agent_rejects_blank_question_before_model_initialization(self) -> None:
        with patch("core.agent.chat_model") as chat_model:
            with self.assertRaises(ValueError):
                agent.run_agent("   ", [], verbose=False)

        chat_model.assert_not_called()

    def test_agent_rejects_non_string_question_before_model_initialization(self) -> None:
        with patch("core.agent.chat_model") as chat_model:
            with self.assertRaises(ValueError):
                agent.run_agent(None, [], verbose=False)  # type: ignore[arg-type]

        chat_model.assert_not_called()

    def test_trim_history_removes_orphaned_non_user_messages_after_limit(self) -> None:
        old_limit = agent.MAX_HISTORY_MESSAGES
        history = [
            HumanMessage(content="q1"),
            AIMessage(content="a1"),
            HumanMessage(content="q2"),
            AIMessage(content="a2"),
            ToolMessage(content="stale tool result", tool_call_id="old-tool"),
        ]
        agent.MAX_HISTORY_MESSAGES = 3
        try:
            agent.trim_history(history)
        finally:
            agent.MAX_HISTORY_MESSAGES = old_limit

        self.assertEqual([type(message) for message in history], [HumanMessage, AIMessage])
        self.assertEqual([str(message.content) for message in history], ["q2", "a2"])

    def _seed_data(self) -> None:
        messages = [
            NormMessage(
                id="m1",
                sender="Alice",
                is_self=0,
                timestamp="2024-02-01T09:00:00",
                content="Project is 100% ready",
                msg_type="text",
                thread="Project 100%",
                reply_to=None,
            ),
            NormMessage(
                id="m2",
                sender="Bob",
                is_self=0,
                timestamp="2024-02-01T09:01:00",
                content="Replying to the launch note",
                msg_type="text",
                thread="Project 100%",
                reply_to="m1",
            ),
            NormMessage(
                id="m3",
                sender="Me",
                is_self=1,
                timestamp="2024-02-02T10:00:00",
                content="Road_map checkpoint",
                msg_type="text",
                thread="Road_map",
                reply_to=None,
            ),
        ]
        store.upsert_messages(messages)
        store.recompute_message_sequence(["Project 100%", "Road_map"])
        store.rebuild_fts()


class _PromptCapturingChatModel:
    def __init__(self) -> None:
        self.bound_tools: list[object] = []
        self.messages: list[object] = []

    def bind_tools(self, tools_arg: object) -> "_PromptCapturingChatModel":
        self.bound_tools = list(tools_arg)  # type: ignore[arg-type]
        return self

    def invoke(self, messages: object) -> AIMessage:
        self.messages = list(messages)  # type: ignore[arg-type]
        return AIMessage(content="ok")


if __name__ == "__main__":
    unittest.main()
