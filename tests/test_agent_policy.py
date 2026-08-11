from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from core import agent


class AgentPolicyTests(unittest.TestCase):
    def test_resolve_effort_uses_explicit_preset(self) -> None:
        level, max_rounds, max_nudges = agent._resolve_effort("high")
        preset = agent.SEARCH_EFFORT_PRESETS["high"]

        self.assertEqual(level, "high")
        self.assertEqual(max_rounds, preset["max_rounds"])
        self.assertEqual(max_nudges, preset["max_nudges"])

    def test_set_search_effort_rejects_unknown_level(self) -> None:
        with self.assertRaises(ValueError):
            agent.set_search_effort("turbo")

    def test_system_prompt_lists_enabled_and_disabled_tools(self) -> None:
        original_enabled = list(agent.ENABLED_TOOLS)
        try:
            agent.ENABLED_TOOLS[:] = ["search_messages", "get_context"]
            with patch.object(agent, "build_data_overview", return_value="# 数据概况\n- test"):
                prompt = agent.build_system_prompt("low")
        finally:
            agent.ENABLED_TOOLS[:] = original_enabled

        self.assertIn("当前启用工具：search_messages, get_context", prompt)
        self.assertIn("semantic_search", prompt)
        self.assertIn("low（快速模式）", prompt)

    def test_duplicate_tool_call_is_not_executed_twice(self) -> None:
        fake_tool = MagicMock()
        fake_tool.invoke.return_value = '{"ok": true}'
        signatures: set[str] = set()
        call = {
            "id": "call-1",
            "name": "search_messages",
            "args": {"query": "项目"},
        }

        with patch.dict(agent.TOOLS_BY_NAME, {"search_messages": fake_tool}, clear=True):
            with patch.object(agent, "ENABLED_TOOLS", ["search_messages"]):
                first = agent._run_tool_call(call, signatures)
                second = agent._run_tool_call(call, signatures)

        self.assertEqual(first.content, '{"ok": true}')
        self.assertIn("重复", str(second.content))
        fake_tool.invoke.assert_called_once_with({"query": "项目"})


if __name__ == "__main__":
    unittest.main()
