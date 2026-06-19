from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

import core.agent as agent_module
import core.llm as llm_module
from core.tools import TOOLS_BY_NAME
from backend.routers import settings
from backend.schemas import SettingsModel


class SettingsRouterTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.settings_path = Path(self._tmp.name) / "backend_settings.json"
        self._old_settings_path = settings.SETTINGS_PATH
        self._old_agent_state = {
            "system_prompt": agent_module.SYSTEM_PROMPT,
            "max_rounds": agent_module.MAX_ROUNDS,
            "max_history_messages": agent_module.MAX_HISTORY_MESSAGES,
            "enabled_tools": list(agent_module.ENABLED_TOOLS),
        }
        self._old_llm_state = {
            "chat_model_override": llm_module.CHAT_MODEL_OVERRIDE,
            "chat_timeout_override": llm_module.CHAT_TIMEOUT_OVERRIDE,
            "chat_temperature": llm_module.CHAT_TEMPERATURE,
        }
        self._old_env = {name: os.environ.get(name) for name in ("CHAT_MODEL", "CHAT_TIMEOUT", "CHAT_API_KEY")}

        settings.SETTINGS_PATH = self.settings_path
        os.environ["CHAT_MODEL"] = "env-chat-model"
        os.environ["CHAT_TIMEOUT"] = "88.5"
        os.environ["CHAT_API_KEY"] = "sk-secret-value"
        llm_module.CHAT_MODEL_OVERRIDE = None
        llm_module.CHAT_TIMEOUT_OVERRIDE = None
        llm_module.CHAT_TEMPERATURE = 0.0

    def tearDown(self) -> None:
        settings.SETTINGS_PATH = self._old_settings_path
        agent_module.SYSTEM_PROMPT = self._old_agent_state["system_prompt"]
        agent_module.MAX_ROUNDS = self._old_agent_state["max_rounds"]
        agent_module.MAX_HISTORY_MESSAGES = self._old_agent_state["max_history_messages"]
        agent_module.ENABLED_TOOLS = list(self._old_agent_state["enabled_tools"])
        llm_module.CHAT_MODEL_OVERRIDE = self._old_llm_state["chat_model_override"]
        llm_module.CHAT_TIMEOUT_OVERRIDE = self._old_llm_state["chat_timeout_override"]
        llm_module.CHAT_TEMPERATURE = self._old_llm_state["chat_temperature"]
        llm_module._chat_model_cached.cache_clear()

        for name, value in self._old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        self._tmp.cleanup()

    def test_update_settings_applies_and_persists_non_secret_runtime_values(self) -> None:
        response = settings.update_settings(
            SettingsModel(
                system_prompt="answer from imported chat records only",
                max_rounds=7,
                max_history_messages=12,
                chat_model="deepseek-chat",
                chat_timeout=123.5,
                chat_temperature=0.4,
                enabled_tools=["search_messages", "semantic_search", "search_messages"],
            )
        )

        self.assertEqual(response.system_prompt, "answer from imported chat records only")
        self.assertEqual(agent_module.MAX_ROUNDS, 7)
        self.assertEqual(agent_module.MAX_HISTORY_MESSAGES, 12)
        self.assertEqual(agent_module.ENABLED_TOOLS, ["search_messages", "semantic_search"])
        self.assertEqual(llm_module.CHAT_MODEL_OVERRIDE, "deepseek-chat")
        self.assertEqual(llm_module.CHAT_TIMEOUT_OVERRIDE, 123.5)
        self.assertEqual(llm_module.CHAT_TEMPERATURE, 0.4)

        saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertEqual(saved["chat_model"], "deepseek-chat")
        self.assertEqual(saved["enabled_tools"], ["search_messages", "semantic_search"])
        self.assertNotIn("available_tools", saved)
        self.assertNotIn("chat_api_key", saved)
        self.assertNotIn("CHAT_API_KEY", json.dumps(saved))
        self.assertNotIn("sk-secret-value", json.dumps(saved))

    def test_settings_response_includes_backend_registered_available_tools(self) -> None:
        current = settings.get_settings()

        self.assertEqual(current.available_tools, list(TOOLS_BY_NAME))
        self.assertEqual(set(current.enabled_tools or []), set(agent_module.ENABLED_TOOLS))

    def test_settings_response_can_be_posted_back_without_persisting_read_only_fields(self) -> None:
        current = settings.get_settings()
        req = SettingsModel.model_validate(current.model_dump())

        response = settings.update_settings(req)

        self.assertEqual(response.available_tools, list(TOOLS_BY_NAME))
        saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertNotIn("available_tools", saved)

    def test_get_settings_uses_environment_when_no_runtime_override_exists(self) -> None:
        current = settings.get_settings()

        self.assertEqual(current.chat_model, "env-chat-model")
        self.assertEqual(current.chat_timeout, 88.5)

    def test_get_settings_trims_environment_model_display(self) -> None:
        os.environ["CHAT_MODEL"] = " env-chat-model-with-space "

        current = settings.get_settings()

        self.assertEqual(current.chat_model, "env-chat-model-with-space")

    def test_updating_unrelated_settings_does_not_persist_environment_model_as_override(self) -> None:
        response = settings.update_settings(SettingsModel(system_prompt="use imported records carefully"))

        self.assertEqual(response.chat_model, "env-chat-model")
        self.assertEqual(response.chat_timeout, 88.5)
        self.assertIsNone(llm_module.CHAT_MODEL_OVERRIDE)
        self.assertIsNone(llm_module.CHAT_TIMEOUT_OVERRIDE)

        saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertIsNone(saved["chat_model"])
        self.assertIsNone(saved["chat_timeout"])

    def test_full_form_save_matching_environment_clears_model_and_timeout_overrides(self) -> None:
        llm_module.CHAT_MODEL_OVERRIDE = "old-runtime-model"
        llm_module.CHAT_TIMEOUT_OVERRIDE = 12.0

        response = settings.update_settings(
            SettingsModel(
                system_prompt=agent_module.SYSTEM_PROMPT,
                max_rounds=agent_module.MAX_ROUNDS,
                max_history_messages=agent_module.MAX_HISTORY_MESSAGES,
                chat_model="env-chat-model",
                chat_timeout=88.5,
                chat_temperature=llm_module.CHAT_TEMPERATURE,
                enabled_tools=list(agent_module.ENABLED_TOOLS),
            )
        )

        self.assertEqual(response.chat_model, "env-chat-model")
        self.assertEqual(response.chat_timeout, 88.5)
        self.assertIsNone(llm_module.CHAT_MODEL_OVERRIDE)
        self.assertIsNone(llm_module.CHAT_TIMEOUT_OVERRIDE)

        saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertIsNone(saved["chat_model"])
        self.assertIsNone(saved["chat_timeout"])

    def test_explicit_null_model_and_timeout_clear_runtime_overrides(self) -> None:
        llm_module.CHAT_MODEL_OVERRIDE = "old-runtime-model"
        llm_module.CHAT_TIMEOUT_OVERRIDE = 12.0

        response = settings.update_settings(SettingsModel(chat_model=None, chat_timeout=None))

        self.assertEqual(response.chat_model, "env-chat-model")
        self.assertEqual(response.chat_timeout, 88.5)
        self.assertIsNone(llm_module.CHAT_MODEL_OVERRIDE)
        self.assertIsNone(llm_module.CHAT_TIMEOUT_OVERRIDE)

        saved = json.loads(self.settings_path.read_text(encoding="utf-8"))
        self.assertIsNone(saved["chat_model"])
        self.assertIsNone(saved["chat_timeout"])

    def test_invalid_enabled_tool_is_rejected_and_not_persisted(self) -> None:
        with self.assertRaises(HTTPException) as exc:
            settings.update_settings(SettingsModel(enabled_tools=["search_messages", "unknown_tool"]))

        self.assertEqual(exc.exception.status_code, 400)
        self.assertFalse(self.settings_path.exists())
        self.assertNotIn("unknown_tool", agent_module.ENABLED_TOOLS)

    def test_enabled_tool_names_trim_and_reject_unbounded_values(self) -> None:
        req = SettingsModel(enabled_tools=[" search_messages ", " semantic_search "])

        self.assertEqual(req.enabled_tools, ["search_messages", "semantic_search"])

        with self.assertRaises(ValidationError):
            SettingsModel(enabled_tools=["x" * 65])

    def test_empty_system_prompt_is_rejected_before_runtime_mutation(self) -> None:
        original_prompt = agent_module.SYSTEM_PROMPT

        with self.assertRaises(ValidationError):
            SettingsModel(system_prompt="   ")

        self.assertEqual(agent_module.SYSTEM_PROMPT, original_prompt)
        self.assertFalse(self.settings_path.exists())

    def test_settings_text_fields_trim_before_length_validation(self) -> None:
        model = SettingsModel(system_prompt=f" {'x' * 20000} ", chat_model=f" {'m' * 200} ")

        self.assertEqual(len(model.system_prompt or ""), 20000)
        self.assertEqual(len(model.chat_model or ""), 200)

    def test_invalid_update_does_not_partially_mutate_runtime(self) -> None:
        original_prompt = agent_module.SYSTEM_PROMPT
        original_rounds = agent_module.MAX_ROUNDS
        original_model = llm_module.CHAT_MODEL_OVERRIDE
        original_timeout = llm_module.CHAT_TIMEOUT_OVERRIDE

        with self.assertRaises(HTTPException):
            settings.update_settings(
                SettingsModel(
                    system_prompt="should not apply",
                    max_rounds=2,
                    chat_model="should-not-apply",
                    chat_timeout=9.0,
                    enabled_tools=["unknown_tool"],
                )
            )

        self.assertEqual(agent_module.SYSTEM_PROMPT, original_prompt)
        self.assertEqual(agent_module.MAX_ROUNDS, original_rounds)
        self.assertEqual(llm_module.CHAT_MODEL_OVERRIDE, original_model)
        self.assertEqual(llm_module.CHAT_TIMEOUT_OVERRIDE, original_timeout)
        self.assertFalse(self.settings_path.exists())

    def test_persist_failure_does_not_partially_mutate_runtime(self) -> None:
        original_prompt = agent_module.SYSTEM_PROMPT
        original_rounds = agent_module.MAX_ROUNDS
        original_model = llm_module.CHAT_MODEL_OVERRIDE
        original_timeout = llm_module.CHAT_TIMEOUT_OVERRIDE
        temp_path = self.settings_path.with_suffix(self.settings_path.suffix + ".tmp")

        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                settings.update_settings(
                    SettingsModel(
                        system_prompt="should not apply",
                        max_rounds=5,
                        chat_model="should-not-apply",
                        chat_timeout=12.0,
                        enabled_tools=["search_messages"],
                    )
                )

        self.assertEqual(agent_module.SYSTEM_PROMPT, original_prompt)
        self.assertEqual(agent_module.MAX_ROUNDS, original_rounds)
        self.assertEqual(llm_module.CHAT_MODEL_OVERRIDE, original_model)
        self.assertEqual(llm_module.CHAT_TIMEOUT_OVERRIDE, original_timeout)
        self.assertFalse(self.settings_path.exists())
        self.assertFalse(temp_path.exists())

    def test_persist_replace_failure_cleans_temp_file_and_does_not_mutate_runtime(self) -> None:
        original_prompt = agent_module.SYSTEM_PROMPT
        original_rounds = agent_module.MAX_ROUNDS
        original_model = llm_module.CHAT_MODEL_OVERRIDE
        original_timeout = llm_module.CHAT_TIMEOUT_OVERRIDE
        temp_path = self.settings_path.with_suffix(self.settings_path.suffix + ".tmp")

        with patch.object(Path, "replace", side_effect=OSError("replace denied")):
            with self.assertRaises(OSError):
                settings.update_settings(
                    SettingsModel(
                        system_prompt="should not apply",
                        max_rounds=6,
                        chat_model="replace-failed-model",
                        chat_timeout=13.0,
                        enabled_tools=["search_messages"],
                    )
                )

        self.assertEqual(agent_module.SYSTEM_PROMPT, original_prompt)
        self.assertEqual(agent_module.MAX_ROUNDS, original_rounds)
        self.assertEqual(llm_module.CHAT_MODEL_OVERRIDE, original_model)
        self.assertEqual(llm_module.CHAT_TIMEOUT_OVERRIDE, original_timeout)
        self.assertFalse(self.settings_path.exists())
        self.assertFalse(temp_path.exists())

    def test_reset_restores_defaults_and_deletes_persisted_files(self) -> None:
        settings.update_settings(
            SettingsModel(
                system_prompt="temporary prompt",
                max_rounds=3,
                chat_model="override-model",
                chat_timeout=5.0,
                chat_temperature=1.2,
                enabled_tools=["search_messages"],
            )
        )
        tmp_path = self.settings_path.with_suffix(self.settings_path.suffix + ".tmp")
        tmp_path.write_text("stale", encoding="utf-8")

        response = settings.reset_settings()

        self.assertEqual(response.system_prompt, settings._DEFAULT_SYSTEM_PROMPT)
        self.assertEqual(agent_module.MAX_ROUNDS, settings._DEFAULT_MAX_ROUNDS)
        self.assertEqual(agent_module.ENABLED_TOOLS, settings._DEFAULT_ENABLED_TOOLS)
        self.assertIsNone(llm_module.CHAT_MODEL_OVERRIDE)
        self.assertIsNone(llm_module.CHAT_TIMEOUT_OVERRIDE)
        self.assertEqual(llm_module.CHAT_TEMPERATURE, settings._DEFAULT_CHAT_TEMPERATURE)
        self.assertFalse(self.settings_path.exists())
        self.assertFalse(tmp_path.exists())

    def test_reset_persist_failure_does_not_report_fake_runtime_reset(self) -> None:
        settings.update_settings(
            SettingsModel(
                system_prompt="temporary prompt",
                max_rounds=3,
                chat_model="override-model",
                chat_timeout=5.0,
                chat_temperature=1.2,
                enabled_tools=["search_messages"],
            )
        )

        with patch.object(Path, "unlink", side_effect=OSError("delete denied")):
            with self.assertRaises(OSError):
                settings.reset_settings()

        self.assertEqual(agent_module.SYSTEM_PROMPT, "temporary prompt")
        self.assertEqual(agent_module.MAX_ROUNDS, 3)
        self.assertEqual(agent_module.ENABLED_TOOLS, ["search_messages"])
        self.assertEqual(llm_module.CHAT_MODEL_OVERRIDE, "override-model")
        self.assertEqual(llm_module.CHAT_TIMEOUT_OVERRIDE, 5.0)
        self.assertEqual(llm_module.CHAT_TEMPERATURE, 1.2)
        self.assertTrue(self.settings_path.exists())

    def test_reset_temp_delete_failure_keeps_persisted_settings_and_runtime(self) -> None:
        settings.update_settings(
            SettingsModel(
                system_prompt="temporary prompt",
                max_rounds=3,
                chat_model="override-model",
                chat_timeout=5.0,
                chat_temperature=1.2,
                enabled_tools=["search_messages"],
            )
        )
        tmp_path = self.settings_path.with_suffix(self.settings_path.suffix + ".tmp")
        tmp_path.write_text("stale", encoding="utf-8")
        original_unlink = Path.unlink

        def fail_for_tmp(path: Path, *args, **kwargs) -> None:
            if path == tmp_path:
                raise OSError("temp delete denied")
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", fail_for_tmp):
            with self.assertRaises(OSError):
                settings.reset_settings()

        self.assertEqual(agent_module.SYSTEM_PROMPT, "temporary prompt")
        self.assertEqual(agent_module.MAX_ROUNDS, 3)
        self.assertEqual(agent_module.ENABLED_TOOLS, ["search_messages"])
        self.assertEqual(llm_module.CHAT_MODEL_OVERRIDE, "override-model")
        self.assertEqual(llm_module.CHAT_TIMEOUT_OVERRIDE, 5.0)
        self.assertEqual(llm_module.CHAT_TEMPERATURE, 1.2)
        self.assertTrue(self.settings_path.exists())
        self.assertTrue(tmp_path.exists())

    def test_load_persisted_settings_ignores_invalid_file_without_mutating_runtime(self) -> None:
        agent_module.MAX_ROUNDS = 9
        self.settings_path.write_text(
            '{"max_rounds": 0, "enabled_tools": ["missing"], "CHAT_API_KEY": "sk-broken-secret-123456"}',
            encoding="utf-8",
        )

        with patch("backend.routers.settings.logger.warning") as logger_warning:
            settings._load_persisted_settings()

        self.assertEqual(agent_module.MAX_ROUNDS, 9)
        logger_warning.assert_called_once()
        self.assertEqual(logger_warning.call_args.args[0], "Runtime settings load failed")
        serialized = json.dumps(logger_warning.call_args.kwargs["extra"], ensure_ascii=False)
        self.assertIn("加载持久化设置失败", serialized)
        self.assertNotIn("sk-broken-secret", serialized)

    def test_load_persisted_null_model_and_timeout_clear_stale_runtime_overrides(self) -> None:
        llm_module.CHAT_MODEL_OVERRIDE = "stale-model"
        llm_module.CHAT_TIMEOUT_OVERRIDE = 12.0
        self.settings_path.write_text(
            json.dumps(
                {
                    "system_prompt": agent_module.SYSTEM_PROMPT,
                    "max_rounds": agent_module.MAX_ROUNDS,
                    "max_history_messages": agent_module.MAX_HISTORY_MESSAGES,
                    "chat_model": None,
                    "chat_timeout": None,
                    "chat_temperature": llm_module.CHAT_TEMPERATURE,
                    "enabled_tools": list(agent_module.ENABLED_TOOLS),
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        settings._load_persisted_settings()

        self.assertIsNone(llm_module.CHAT_MODEL_OVERRIDE)
        self.assertIsNone(llm_module.CHAT_TIMEOUT_OVERRIDE)


if __name__ == "__main__":
    unittest.main()
