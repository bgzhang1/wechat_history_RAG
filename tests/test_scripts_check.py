from __future__ import annotations

import io
import os
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from core import chunker, cli, llm, store
from core.parser import NormMessage
from core.scripts import check
from core.scripts import smoke


class CheckScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self._old_chat_state = {
            "model_override": llm.CHAT_MODEL_OVERRIDE,
            "timeout_override": llm.CHAT_TIMEOUT_OVERRIDE,
            "temperature": llm.CHAT_TEMPERATURE,
        }
        llm._chat_model_cached.cache_clear()
        llm._embed_model_cached.cache_clear()

    def tearDown(self) -> None:
        llm.CHAT_MODEL_OVERRIDE = self._old_chat_state["model_override"]
        llm.CHAT_TIMEOUT_OVERRIDE = self._old_chat_state["timeout_override"]
        llm.CHAT_TEMPERATURE = self._old_chat_state["temperature"]
        llm._chat_model_cached.cache_clear()
        llm._embed_model_cached.cache_clear()

    def test_public_exception_message_redacts_secrets(self) -> None:
        message = check.public_exception_message(
            "chat 端点失败",
            RuntimeError("Authorization: Bearer live-token-value api_key=sk-check-secret-123456"),
        )

        self.assertIn("[redacted]", message)
        self.assertNotIn("live-token-value", message)
        self.assertNotIn("sk-check-secret", message)

    def test_cli_error_formatter_redacts_secret_like_exception_text(self) -> None:
        message = cli._format_cli_error(
            RuntimeError("Authorization: Bearer cli-token-value api_key=sk-cli-secret-123456"),
        )

        self.assertIn("[redacted]", message)
        self.assertNotIn("cli-token-value", message)
        self.assertNotIn("sk-cli-secret", message)

    def test_explicit_chat_model_can_be_used_for_summary_without_default_chat_model(self) -> None:
        env = {
            "CHAT_BASE_URL": "https://llm.local/v1",
            "CHAT_API_KEY": "sk-test-secret",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(llm.chat_configured())
            status = llm.chat_config_status("summary-model")
            self.assertTrue(status["configured"])
            self.assertTrue(status["using_explicit_model"])

            with patch("core.llm._chat_model_cached", return_value="client") as cached:
                self.assertEqual(llm.chat_model("summary-model"), "client")

        cached.assert_called_once_with(
            "summary-model",
            "https://llm.local/v1",
            "sk-test-secret",
            300.0,
            llm.CHAT_TEMPERATURE,
            3,
        )

    def test_default_chat_model_still_requires_chat_model_name(self) -> None:
        env = {
            "CHAT_BASE_URL": "https://llm.local/v1",
            "CHAT_API_KEY": "sk-test-secret",
        }
        with patch.dict(os.environ, env, clear=True):
            self.assertFalse(llm.chat_configured())
            with self.assertRaises(RuntimeError) as exc:
                llm.chat_model()

        self.assertIn("CHAT_MODEL", str(exc.exception))

    def test_env_example_placeholder_values_are_treated_as_unconfigured(self) -> None:
        env = {
            "CHAT_BASE_URL": "https://example.com/v1",
            "CHAT_API_KEY": "sk-...",
            "CHAT_MODEL": "your-chat-model",
            "EMBED_BASE_URL": "https://example.com/v1",
            "EMBED_API_KEY": "sk-...",
            "EMBED_MODEL": "your-embedding-model",
        }

        with patch.dict(os.environ, env, clear=True):
            chat_status = llm.chat_config_status()
            embed_status = llm.embed_config_status()

        self.assertFalse(chat_status["configured"])
        self.assertEqual(
            chat_status["missing"],
            ["CHAT_BASE_URL", "CHAT_API_KEY", "CHAT_MODEL"],
        )
        self.assertFalse(embed_status["configured"])
        self.assertEqual(
            embed_status["missing"],
            ["EMBED_BASE_URL", "EMBED_API_KEY", "EMBED_MODEL"],
        )

    def test_env_placeholder_variants_are_treated_as_unconfigured(self) -> None:
        env = {
            "CHAT_BASE_URL": " https://example.com/v1/ ",
            "CHAT_API_KEY": "changeme",
            "CHAT_MODEL": " YOUR-CHAT-MODEL ",
            "EMBED_BASE_URL": "https://api.example.com/v1",
            "EMBED_API_KEY": "...",
            "EMBED_MODEL": "your-embedding-model",
        }

        with patch.dict(os.environ, env, clear=True):
            chat_status = llm.chat_config_status()
            embed_status = llm.embed_config_status()

        self.assertFalse(chat_status["configured"])
        self.assertEqual(
            chat_status["missing"],
            ["CHAT_BASE_URL", "CHAT_API_KEY", "CHAT_MODEL"],
        )
        self.assertFalse(embed_status["configured"])
        self.assertEqual(
            embed_status["missing"],
            ["EMBED_BASE_URL", "EMBED_API_KEY", "EMBED_MODEL"],
        )

    def test_non_example_domain_containing_example_text_is_not_treated_as_placeholder(self) -> None:
        env = {
            "CHAT_BASE_URL": "https://notexample.com/v1",
            "CHAT_API_KEY": "sk-real-chat-key",
            "CHAT_MODEL": "real-chat-model",
            "EMBED_BASE_URL": "https://notexample.com/embeddings",
            "EMBED_API_KEY": "sk-real-embed-key",
            "EMBED_MODEL": "real-embed-model",
        }

        with patch.dict(os.environ, env, clear=True):
            chat_status = llm.chat_config_status()
            embed_status = llm.embed_config_status()

        self.assertTrue(chat_status["configured"])
        self.assertEqual(chat_status["missing"], [])
        self.assertTrue(embed_status["configured"])
        self.assertEqual(embed_status["missing"], [])

    def test_real_env_config_values_are_trimmed_before_client_creation(self) -> None:
        env = {
            "CHAT_BASE_URL": " https://llm.local/v1 ",
            "CHAT_API_KEY": " sk-real-chat-key ",
            "CHAT_MODEL": " real-chat-model ",
            "CHAT_TIMEOUT": "12",
            "EMBED_BASE_URL": " https://embed.local/v1 ",
            "EMBED_API_KEY": " sk-real-embed-key ",
            "EMBED_MODEL": " real-embed-model ",
        }

        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(llm.chat_config_status()["model"], "real-chat-model")
            self.assertEqual(llm.embed_config_status()["model"], "real-embed-model")
            with patch("core.llm._chat_model_cached", return_value="chat-client") as chat_cached:
                self.assertEqual(llm.chat_model(), "chat-client")
            with patch("core.llm._embed_model_cached", return_value="embed-client") as embed_cached:
                self.assertEqual(llm.embed_model(), "embed-client")

        chat_cached.assert_called_once_with(
            "real-chat-model",
            "https://llm.local/v1",
            "sk-real-chat-key",
            12.0,
            llm.CHAT_TEMPERATURE,
            3,
        )
        embed_cached.assert_called_once_with(
            "real-embed-model",
            "https://embed.local/v1",
            "sk-real-embed-key",
            90.0,
            0,
        )

    def test_sdk_clients_receive_trimmed_endpoint_and_key_values(self) -> None:
        env = {
            "CHAT_BASE_URL": " https://llm.local/v1 ",
            "CHAT_API_KEY": " sk-real-chat-key ",
            "CHAT_MODEL": " real-chat-model ",
            "CHAT_MAX_RETRIES": "4",
            "EMBED_BASE_URL": " https://embed.local/v1 ",
            "EMBED_API_KEY": " sk-real-embed-key ",
            "EMBED_MODEL": " real-embed-model ",
            "EMBED_TIMEOUT": "45",
            "EMBED_MAX_RETRIES": "2",
        }

        with patch.dict(os.environ, env, clear=True):
            llm._chat_model_cached.cache_clear()
            llm._embed_model_cached.cache_clear()
            with patch("core.llm.ChatOpenAI", return_value="chat-client") as chat_openai:
                self.assertEqual(llm.chat_model(), "chat-client")
            with patch("core.llm.OpenAIEmbeddings", return_value="embed-client") as embeddings:
                self.assertEqual(llm.embed_model(), "embed-client")

        self.assertEqual(chat_openai.call_args.kwargs["base_url"], "https://llm.local/v1")
        self.assertEqual(chat_openai.call_args.kwargs["api_key"], "sk-real-chat-key")
        self.assertEqual(chat_openai.call_args.kwargs["max_retries"], 4)
        self.assertEqual(embeddings.call_args.kwargs["base_url"], "https://embed.local/v1")
        self.assertEqual(embeddings.call_args.kwargs["api_key"], "sk-real-embed-key")
        self.assertEqual(embeddings.call_args.kwargs["timeout"], 45.0)
        self.assertEqual(embeddings.call_args.kwargs["max_retries"], 2)

    def test_chat_client_cache_key_includes_endpoint_key_and_retry_config(self) -> None:
        first_env = {
            "CHAT_BASE_URL": "https://llm-a.local/v1",
            "CHAT_API_KEY": "sk-chat-a",
            "CHAT_MODEL": "same-chat-model",
            "CHAT_MAX_RETRIES": "3",
        }
        second_env = {
            **first_env,
            "CHAT_BASE_URL": "https://llm-b.local/v1",
            "CHAT_API_KEY": "sk-chat-b",
            "CHAT_MAX_RETRIES": "4",
        }

        with patch("core.llm.ChatOpenAI", side_effect=["client-a", "client-b"]) as chat_openai:
            with patch.dict(os.environ, first_env, clear=True):
                self.assertEqual(llm.chat_model(), "client-a")
            with patch.dict(os.environ, second_env, clear=True):
                self.assertEqual(llm.chat_model(), "client-b")

        self.assertEqual(chat_openai.call_count, 2)
        self.assertEqual(chat_openai.call_args_list[0].kwargs["base_url"], "https://llm-a.local/v1")
        self.assertEqual(chat_openai.call_args_list[1].kwargs["base_url"], "https://llm-b.local/v1")
        self.assertEqual(chat_openai.call_args_list[1].kwargs["api_key"], "sk-chat-b")
        self.assertEqual(chat_openai.call_args_list[1].kwargs["max_retries"], 4)

    def test_embedding_client_cache_key_includes_endpoint_key_timeout_and_retry_config(self) -> None:
        first_env = {
            "EMBED_BASE_URL": "https://embed-a.local/v1",
            "EMBED_API_KEY": "sk-embed-a",
            "EMBED_MODEL": "same-embed-model",
            "EMBED_TIMEOUT": "45",
            "EMBED_MAX_RETRIES": "0",
        }
        second_env = {
            **first_env,
            "EMBED_BASE_URL": "https://embed-b.local/v1",
            "EMBED_API_KEY": "sk-embed-b",
            "EMBED_TIMEOUT": "60",
            "EMBED_MAX_RETRIES": "2",
        }

        with patch("core.llm.OpenAIEmbeddings", side_effect=["embed-a", "embed-b"]) as embeddings:
            with patch.dict(os.environ, first_env, clear=True):
                self.assertEqual(llm.embed_model(), "embed-a")
            with patch.dict(os.environ, second_env, clear=True):
                self.assertEqual(llm.embed_model(), "embed-b")

        self.assertEqual(embeddings.call_count, 2)
        self.assertEqual(embeddings.call_args_list[0].kwargs["base_url"], "https://embed-a.local/v1")
        self.assertEqual(embeddings.call_args_list[1].kwargs["base_url"], "https://embed-b.local/v1")
        self.assertEqual(embeddings.call_args_list[1].kwargs["api_key"], "sk-embed-b")
        self.assertEqual(embeddings.call_args_list[1].kwargs["timeout"], 60.0)
        self.assertEqual(embeddings.call_args_list[1].kwargs["max_retries"], 2)

    def test_embed_empty_input_returns_without_initializing_client(self) -> None:
        with patch("core.llm.embed_model", side_effect=AssertionError("client should not be initialized")):
            self.assertEqual(llm.embed([], batch_size=0), [])

    def test_embed_invalid_batch_size_falls_back_to_default(self) -> None:
        class FakeEmbeddings:
            def __init__(self) -> None:
                self.batches: list[list[str]] = []

            def embed_documents(self, batch: list[str]) -> list[list[float]]:
                self.batches.append(list(batch))
                return [[float(len(item))] for item in batch]

        fake = FakeEmbeddings()
        with patch("core.llm.embed_model", return_value=fake):
            vectors = llm.embed(["alpha", "beta"], batch_size=0)

        self.assertEqual(vectors, [[5.0], [4.0]])
        self.assertEqual(fake.batches, [["alpha", "beta"]])

    def test_embed_retry_callable_binds_each_batch(self) -> None:
        class FakeEmbeddings:
            def __init__(self) -> None:
                self.batches: list[list[str]] = []

            def embed_documents(self, batch: list[str]) -> list[list[float]]:
                self.batches.append(list(batch))
                return [[float(len(item))] for item in batch]

        delayed_calls = []
        fake = FakeEmbeddings()

        def capture_call(call, _attempts, _retry_sleep):
            delayed_calls.append(call)
            return []

        with (
            patch("core.llm.embed_model", return_value=fake),
            patch("core.llm._remote_api_call", side_effect=capture_call),
        ):
            vectors = llm.embed(["alpha", "beta", "gamma"], batch_size=2)

        self.assertEqual(vectors, [])
        self.assertEqual(len(delayed_calls), 2)
        self.assertEqual([call() for call in delayed_calls], [[[5.0], [4.0]], [[5.0]]])
        self.assertEqual(fake.batches, [["alpha", "beta"], ["gamma"]])


class CliScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self._old_db_path = store.DB_PATH
        store.close_current_connection()
        store.DB_PATH = str(Path(self._tmp.name) / "chat.db")

    def tearDown(self) -> None:
        store.close_current_connection()
        store.DB_PATH = self._old_db_path
        self._tmp.cleanup()

    def test_cli_exits_with_actionable_message_for_empty_database(self) -> None:
        store.db()

        with patch("core.cli.chat_configured", return_value=True):
            with self.assertRaises(SystemExit) as exc:
                cli.main()

        message = str(exc.exception)
        self.assertIn("暂无已导入消息", message)
        self.assertIn("python -m core.ingest local/data", message)

    def test_cli_warns_when_messages_exist_without_session_chunks(self) -> None:
        store.upsert_messages(
            [
                NormMessage(
                    id="cli-m1",
                    sender="Alice",
                    is_self=0,
                    timestamp="2024-02-01T09:00:00",
                    content="CLI 启动检查",
                    msg_type="text",
                    thread="项目群",
                    reply_to=None,
                )
            ]
        )
        output = io.StringIO()

        with patch("core.cli.chat_configured", return_value=True), patch("builtins.input", side_effect=EOFError):
            with redirect_stdout(output):
                cli.main()

        self.assertIn("已索引 1 条消息 / 0 个会话块", output.getvalue())
        self.assertIn("语义检索效果会受限", output.getvalue())
        self.assertIn("--skip-import --force-chunks", output.getvalue())
        self.assertIn("仅分块构建", output.getvalue())


class SmokeScriptTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self._old_db_path = store.DB_PATH
        store.close_current_connection()
        store.DB_PATH = str(Path(self._tmp.name) / "chat.db")

    def tearDown(self) -> None:
        store.close_current_connection()
        store.DB_PATH = self._old_db_path
        self._tmp.cleanup()

    def test_empty_database_prints_actionable_message(self) -> None:
        output = io.StringIO()

        with redirect_stdout(output):
            exit_code = smoke.main()

        self.assertEqual(exit_code, 1)
        self.assertIn("暂无已导入消息", output.getvalue())
        self.assertIn("python -m core.ingest local/data", output.getvalue())

    def test_smoke_returns_success_when_core_retrieval_chain_is_ready(self) -> None:
        messages = [
            NormMessage(
                id="m1",
                sender="Alice",
                is_self=0,
                timestamp="2024-02-01T09:00:00",
                content="项目进度确认 alpha beta gamma",
                msg_type="text",
                thread="项目群",
                reply_to=None,
            ),
            NormMessage(
                id="m2",
                sender="Bob",
                is_self=0,
                timestamp="2024-02-01T09:01:00",
                content="收到，今天完成冒烟检查",
                msg_type="text",
                thread="项目群",
                reply_to=None,
            ),
        ]
        store.upsert_messages(messages)
        store.recompute_message_sequence(["项目群"])
        store.rebuild_fts()
        grouped = store.get_all_messages_by_thread(["项目群"])
        store.replace_sessions(chunker.chunk_thread("项目群", grouped["项目群"]), ["项目群"])

        output = io.StringIO()
        with redirect_stdout(output):
            exit_code = smoke.main()

        self.assertEqual(exit_code, 0, output.getvalue())
        self.assertIn("【search 短词", output.getvalue())
        self.assertIn("【semantic_search】", output.getvalue())

    def test_query_terms_skip_long_check_for_very_short_messages(self) -> None:
        self.assertEqual(smoke._query_terms(" 好 "), ("好", ""))
        self.assertEqual(smoke._query_terms("OK"), ("OK", ""))
        self.assertEqual(smoke._query_terms("项目进度确认"), ("项目", "项目进度确认"))
        self.assertEqual(smoke._query_terms("项目进度确认   alpha beta"), ("项目", "项目进度确认 alpha beta"))

    def test_smoke_script_does_not_depend_on_private_fixture_terms(self) -> None:
        source = Path(smoke.__file__).read_text(encoding="utf-8")

        for private_term in ("晚安", "老婆老婆我看看", "2026-06-12", "房顶"):
            self.assertNotIn(private_term, source)


class DocumentationContractTests(unittest.TestCase):
    def test_health_diagnostics_docs_match_vector_only_recovery_action(self) -> None:
        docs = (Path(__file__).resolve().parents[1] / "backend" / "API_DOCS.md").read_text(encoding="utf-8")

        self.assertIn("仅向量构建", docs)
        self.assertNotIn('"action": "请重新运行导入以生成缺失向量。"', docs)

    def test_parser_version_docs_explain_field_ownership_changes(self) -> None:
        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text(encoding="utf-8")
        api_docs = (root / "backend" / "API_DOCS.md").read_text(encoding="utf-8")

        self.assertIn("发送人归属", readme)
        self.assertIn("发送人归属", api_docs)
        self.assertIn("字段归属", api_docs)

    def test_ingest_mode_docs_do_not_overpromise_full_rebuild(self) -> None:
        root = Path(__file__).resolve().parents[1]
        readme = (root / "README.md").read_text(encoding="utf-8")
        api_docs = (root / "backend" / "API_DOCS.md").read_text(encoding="utf-8")
        technical = (root / "docs" / "TECHNICAL.md").read_text(encoding="utf-8")

        self.assertIn("只有文件变化或解析规则过期时才重新解析", readme)
        self.assertIn("只有文件变化或解析规则过期时才重新解析", api_docs)
        self.assertIn("按目标 JSON 关联范围强制重建 RAG 元数据", technical)
        self.assertNotIn("强行彻底清洗重做所有的 RAG 元数据", technical)


if __name__ == "__main__":
    unittest.main()
