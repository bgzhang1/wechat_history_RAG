from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.parser import file_scope_for_path, is_weflow_export, parse_weflow, stable_upload_scope, to_local_iso


class ParserTests(unittest.TestCase):
    def test_parse_weflow_normalizes_real_world_message_shapes(self) -> None:
        data = {
            "weflow": {"version": "test"},
            "session": {
                "remark": "",
                "displayName": "项目群",
                "nickname": "备用群名",
                "wxid": "peer-wxid",
            },
            "messages": [
                {
                    "type": "文本消息",
                    "content": {"title": "周五计划", "summary": "下午同步"},
                    "createTime": 1_700_000_000_000,
                    "isSend": 1,
                    "senderDisplayName": "我自己",
                    "platformMessageId": "platform-1",
                },
                {
                    "type": "引用消息",
                    "content": ["引用原文", {"text": "回复正文"}],
                    "createTime": "2024-01-02T03:04:05",
                    "isSend": 0,
                    "senderUsername": "peer-wxid",
                    "senderDisplayName": "",
                    "msgId": "msg-2",
                    "replyToMessageId": 123,
                },
                {
                    "type": "图片消息",
                    "content": "image",
                    "createTime": 1_700_000_001,
                },
                {
                    "type": ["文本消息"],
                    "content": "type is malformed",
                    "createTime": 1_700_000_001,
                },
                {
                    "type": {"name": "文本消息"},
                    "content": "type is malformed",
                    "createTime": 1_700_000_001,
                },
                {
                    "content": "type is missing",
                    "createTime": 1_700_000_001,
                },
                {
                    "type": "   ",
                    "content": "type is blank",
                    "createTime": 1_700_000_001,
                },
                "bad message",
                {
                    "type": "文本消息",
                    "content": "",
                    "createTime": 1_700_000_002,
                },
                {
                    "type": "文本消息",
                    "content": "时间坏了",
                    "createTime": "not-a-time",
                },
                {
                    "type": "文本消息",
                    "content": "布尔时间不应被当成 1970 年消息",
                    "createTime": True,
                },
                {
                    "type": "文本消息",
                    "content": "零时间不应污染时间线",
                    "createTime": 0,
                },
            ],
        }

        self.assertTrue(is_weflow_export(data))
        result = parse_weflow(data, Path("exports/chat.json"))

        self.assertEqual(result.thread, "项目群")
        self.assertEqual(result.total, 12)
        self.assertEqual(result.included, 2)
        self.assertEqual(result.skipped_by_type["图片消息(空内容)"], 1)
        self.assertEqual(result.skipped_by_type["非法消息类型"], 2)
        self.assertEqual(result.skipped_by_type["未知消息类型"], 2)
        self.assertEqual(result.skipped_by_type["非法消息结构"], 1)
        self.assertEqual(result.skipped_by_type["文本消息(空内容)"], 1)
        self.assertEqual(result.skipped_by_type["文本消息(无效时间)"], 3)

        first, second = result.messages
        self.assertEqual(first.id, "exports/chat.json:platform-1")
        self.assertEqual(first.sender, "我自己")
        self.assertEqual(first.is_self, 1)
        self.assertEqual(first.timestamp, to_local_iso(1_700_000_000))
        self.assertEqual(first.content, "周五计划\n下午同步")
        self.assertIsNone(first.reply_to)

        self.assertEqual(second.id, "exports/chat.json:msg-2")
        self.assertEqual(second.sender, "项目群")
        self.assertEqual(second.is_self, 0)
        self.assertEqual(second.timestamp, "2024-01-02T03:04:05")
        self.assertEqual(second.content, "引用原文\n回复正文")
        self.assertEqual(second.reply_to, "123")

    def test_parse_weflow_falls_back_to_file_based_ids(self) -> None:
        data = {
            "weflow": {},
            "session": {},
            "messages": [
                {
                    "type": "文本消息",
                    "content": "local id message",
                    "createTime": 1_700_000_000_000_000,
                    "localId": "local-1",
                },
                {
                    "type": "文本消息",
                    "content": "index fallback message",
                    "createTime": 1_700_000_001,
                    "replyToMessageId": "   ",
                },
            ],
        }

        result = parse_weflow(data, Path("fallback.json"))

        self.assertEqual(result.thread, "fallback")
        self.assertEqual(result.messages[0].id, "fallback.json:local-1")
        self.assertEqual(result.messages[0].timestamp, to_local_iso(1_700_000_000))
        self.assertEqual(result.messages[1].id, "fallback.json:1")
        self.assertIsNone(result.messages[1].reply_to)

    def test_parse_weflow_accepts_common_export_field_aliases(self) -> None:
        data = {
            "weflow": {},
            "session": {"displayName": "字段别名群"},
            "messages": [
                {
                    "msgType": "文本消息",
                    "text": "text alias",
                    "timestamp": 1_700_000_000,
                    "msgId": "alias-1",
                },
                {
                    "messageType": "文本消息",
                    "content": "",
                    "body": {"title": "body alias", "summary": "fallback content"},
                    "createdAt": "2024-01-02T03:04:05",
                    "msgId": "alias-2",
                },
                {
                    "msg_type": "引用消息",
                    "message": "message alias",
                    "quotedContent": "quoted alias",
                    "create_time": 1_700_000_001_000,
                    "messageId": "alias-3",
                    "reply_msg_id": "alias-2",
                },
            ],
        }

        result = parse_weflow(data, Path("aliases.json"))

        self.assertEqual(result.included, 3)
        self.assertEqual([message.msg_type for message in result.messages], ["文本消息", "文本消息", "引用消息"])
        self.assertEqual(result.messages[0].content, "text alias")
        self.assertEqual(result.messages[1].content, "body alias\nfallback content")
        self.assertEqual(result.messages[1].timestamp, "2024-01-02T03:04:05")
        self.assertEqual(result.messages[2].timestamp, to_local_iso(1_700_000_001))
        self.assertEqual(result.messages[2].content, "message alias\n[引用：quoted alias]")
        self.assertEqual(result.messages[2].id, "aliases.json:alias-3")
        self.assertEqual(result.messages[2].reply_to, "aliases.json:alias-2")

    def test_parse_weflow_accepts_common_top_level_message_list_aliases(self) -> None:
        for alias in ("messageList", "message_list", "msgList", "chatRecords", "records"):
            with self.subTest(alias=alias):
                data = {
                    "weflow": {},
                    "session": {"displayName": "列表别名群"},
                    alias: [
                        {
                            "type": "文本消息",
                            "content": f"{alias} content",
                            "createTime": 1_700_000_000,
                            "msgId": "alias-message",
                        }
                    ],
                }

                self.assertTrue(is_weflow_export(data))
                result = parse_weflow(data, Path(f"{alias}.json"))

                self.assertEqual(result.total, 1)
                self.assertEqual(result.included, 1)
                self.assertEqual(result.messages[0].content, f"{alias} content")
                self.assertEqual(result.messages[0].id, f"{alias}.json:alias-message")

    def test_parse_weflow_accepts_numeric_and_short_message_type_aliases(self) -> None:
        data = {
            "weflow": {},
            "session": {"displayName": "数字类型群"},
            "messages": [
                {
                    "type": 1,
                    "content": "numeric text",
                    "createTime": 1_700_000_000,
                    "msgId": "numeric-text",
                },
                {
                    "msgType": "3",
                    "content": "[图片]",
                    "ocrText": "numeric image ocr",
                    "createTime": 1_700_000_001,
                    "msgId": "numeric-image",
                },
                {
                    "messageType": 34.0,
                    "content": "[语音]",
                    "voiceText": "numeric voice transcript",
                    "createTime": 1_700_000_002,
                    "msgId": "numeric-voice",
                },
                {
                    "type": "text",
                    "content": "english text alias",
                    "createTime": 1_700_000_003,
                    "msgId": "english-text",
                },
                {
                    "type": "unknown-code",
                    "content": "unsupported type",
                    "createTime": 1_700_000_004,
                    "msgId": "unsupported",
                },
            ],
        }

        result = parse_weflow(data, Path("numeric-types.json"))

        self.assertEqual(result.included, 4)
        self.assertEqual(result.skipped_by_type["unknown-code"], 1)
        self.assertEqual(
            [message.msg_type for message in result.messages],
            ["文本消息", "图片消息", "语音消息", "文本消息"],
        )
        self.assertEqual(
            [message.content for message in result.messages],
            ["numeric text", "numeric image ocr", "numeric voice transcript", "english text alias"],
        )

    def test_parse_weflow_accepts_legacy_and_nested_export_aliases(self) -> None:
        data = {
            "weflow": {},
            "session": {"name": "兼容群", "userName": "peer-wxid"},
            "messages": [
                {
                    "typeName": "链接消息",
                    "msgContent": {
                        "title": "产品链接",
                        "desc": "上线说明",
                        "url": "https://example.invalid/launch",
                    },
                    "formattedTime": "2024-02-03 04:05:06",
                    "newMsgId": "server-1",
                    "fromUserName": "peer-wxid",
                },
                {
                    "msgTypeName": "文件消息",
                    "body": {"fileName": "报价单.pdf", "fileSize": 2048},
                    "sentAt": 1_700_000_000,
                    "clientMsgId": "client-2",
                },
                {
                    "message_type": "引用消息",
                    "plainText": "收到，按这个来",
                    "quote": {"senderName": "Alice", "msgContent": "原始安排"},
                    "msg_create_time": 1_700_000_001_000,
                    "localMsgId": "local-3",
                    "replyMessageId": "client-2",
                },
            ],
        }

        result = parse_weflow(data, Path("legacy-aliases.json"))

        self.assertEqual(result.included, 3)
        self.assertEqual([message.msg_type for message in result.messages], ["链接消息", "文件消息", "引用消息"])
        self.assertEqual(result.messages[0].sender, "兼容群")
        self.assertEqual(result.messages[0].timestamp, "2024-02-03T04:05:06")
        self.assertEqual(result.messages[0].id, "legacy-aliases.json:server-1")
        self.assertEqual(result.messages[0].content, "产品链接\n上线说明\nhttps://example.invalid/launch")
        self.assertEqual(result.messages[1].id, "legacy-aliases.json:client-2")
        self.assertEqual(result.messages[1].content, "报价单.pdf\n2048")
        self.assertEqual(result.messages[2].id, "legacy-aliases.json:local-3")
        self.assertEqual(result.messages[2].content, "收到，按这个来\n[引用 Alice：原始安排]")
        self.assertEqual(result.messages[2].reply_to, "legacy-aliases.json:client-2")

    def test_parse_weflow_falls_back_past_blank_or_null_primary_aliases(self) -> None:
        data = {
            "weflow": {},
            "session": {"displayName": "空字段兼容群"},
            "messages": [
                {
                    "type": None,
                    "msgTypeName": "文本消息",
                    "content": "主键为空时仍应读取备用类型和时间",
                    "createTime": "",
                    "sentAt": 1_700_000_000,
                    "isSend": "",
                    "fromMe": "yes",
                    "senderName": "本人别名",
                    "platformMessageId": "",
                    "msgSvrId": "server-target",
                },
                {
                    "type": "",
                    "message_type": "引用消息",
                    "content": "回复正文",
                    "quotedContent": "",
                    "quoteContent": "备用引用正文",
                    "createTime": None,
                    "msgCreateTime": 1_700_000_001_000,
                    "msgId": "reply-1",
                    "replyToMessageId": "",
                    "referMsgId": "server-target",
                },
            ],
        }

        result = parse_weflow(data, Path("blank-primary-aliases.json"))

        self.assertEqual(result.included, 2)
        first, second = result.messages
        self.assertEqual(first.msg_type, "文本消息")
        self.assertEqual(first.timestamp, to_local_iso(1_700_000_000))
        self.assertEqual(first.sender, "本人别名")
        self.assertEqual(first.is_self, 1)
        self.assertEqual(first.id, "blank-primary-aliases.json:server-target")
        self.assertEqual(second.msg_type, "引用消息")
        self.assertEqual(second.timestamp, to_local_iso(1_700_000_001))
        self.assertEqual(second.content, "回复正文\n[引用：备用引用正文]")
        self.assertEqual(second.reply_to, "blank-primary-aliases.json:server-target")

    def test_parse_weflow_falls_back_past_empty_nested_quote_objects(self) -> None:
        data = {
            "weflow": {},
            "session": {"displayName": "嵌套引用兼容群"},
            "messages": [
                {
                    "type": "引用消息",
                    "content": "回复正文",
                    "quote": {"senderName": "错误发送人"},
                    "refer": {},
                    "reply": {"senderName": "Alice", "msgContent": "真正引用正文"},
                    "createTime": 1_700_000_000,
                    "msgId": "nested-quote",
                },
            ],
        }

        result = parse_weflow(data, Path("nested-empty-quotes.json"))

        self.assertEqual(result.included, 1)
        self.assertEqual(result.messages[0].content, "回复正文\n[引用 Alice：真正引用正文]")

    def test_parse_weflow_uses_nested_quote_message_id_for_reply_link(self) -> None:
        data = {
            "weflow": {},
            "session": {"displayName": "嵌套引用 ID 群"},
            "messages": [
                {
                    "type": "文本消息",
                    "content": "原始安排",
                    "createTime": 1_700_000_000,
                    "msgId": "origin-1",
                },
                {
                    "type": "引用消息",
                    "content": "收到，照这个执行",
                    "quote": {
                        "msgId": "origin-1",
                        "senderName": "Alice",
                        "msgContent": "原始安排",
                    },
                    "createTime": 1_700_000_001,
                    "msgId": "reply-1",
                },
            ],
        }

        result = parse_weflow(data, Path("nested-quote-id.json"))

        self.assertEqual(result.included, 2)
        self.assertEqual(result.messages[1].content, "收到，照这个执行\n[引用 Alice：原始安排]")
        self.assertEqual(result.messages[1].reply_to, "nested-quote-id.json:origin-1")

    def test_parse_weflow_keeps_text_payloads_from_common_non_text_types(self) -> None:
        data = {
            "weflow": {},
            "session": {"displayName": "业务聊天"},
            "messages": [
                {
                    "type": "动画表情",
                    "content": "[表情包]",
                    "emojiCaption": "点头同意",
                    "createTime": 1_700_000_000,
                    "msgId": "emoji-1",
                },
                {
                    "type": "动画表情",
                    "content": "[表情包：哭]",
                    "createTime": 1_700_000_001,
                    "msgId": "emoji-2",
                },
                {
                    "type": "转账消息",
                    "content": "转账 88.00 元：午饭 AA",
                    "createTime": 1_700_000_002,
                    "msgId": "transfer-1",
                },
                {
                    "type": "链接消息",
                    "content": "产品文档",
                    "linkTitle": "需求说明",
                    "linkUrl": "https://example.invalid/spec",
                    "createTime": 1_700_000_003,
                    "msgId": "link-1",
                },
                {
                    "type": "位置消息",
                    "content": "深圳湾",
                    "address": "深圳市南山区",
                    "createTime": 1_700_000_004,
                    "msgId": "location-1",
                },
                {
                    "type": "动画表情",
                    "content": "[表情包]",
                    "createTime": 1_700_000_005,
                    "msgId": "emoji-placeholder",
                },
                {
                    "type": "图片消息",
                    "content": "image payload should stay skipped without OCR text",
                    "createTime": 1_700_000_006,
                    "msgId": "image-1",
                },
            ],
        }

        result = parse_weflow(data, Path("payloads.json"))

        self.assertEqual(result.included, 5)
        self.assertEqual(result.skipped_by_type["图片消息(空内容)"], 1)
        self.assertEqual(result.skipped_by_type["动画表情(空内容)"], 1)
        self.assertEqual(
            [message.msg_type for message in result.messages],
            ["动画表情", "动画表情", "转账消息", "链接消息", "位置消息"],
        )
        self.assertEqual(result.messages[0].content, "点头同意")
        self.assertEqual(result.messages[1].content, "[表情包：哭]")
        self.assertEqual(result.messages[2].content, "转账 88.00 元：午饭 AA")
        self.assertEqual(result.messages[3].content, "产品文档\n需求说明\nhttps://example.invalid/spec")
        self.assertEqual(result.messages[4].content, "深圳湾\n深圳市南山区")

    def test_parse_weflow_keeps_media_ocr_captions_and_transcripts_without_payload_noise(self) -> None:
        data = {
            "weflow": {},
            "session": {"displayName": "媒体业务群"},
            "messages": [
                {
                    "type": "图片消息",
                    "content": "[图片]",
                    "ocrText": "白板 OCR：Q3 合同金额 10 万",
                    "createTime": 1_700_000_000,
                    "msgId": "image-ocr",
                },
                {
                    "type": "图片消息",
                    "content": {"cdnUrl": "https://example.invalid/image.jpg", "ocr": {"text": "截图里写着周五上线"}},
                    "createTime": 1_700_000_001,
                    "msgId": "image-nested-ocr",
                },
                {
                    "type": "语音消息",
                    "content": "[语音]",
                    "transcription": "语音转文字：先约客户复盘",
                    "createTime": 1_700_000_002,
                    "msgId": "voice-asr",
                },
                {
                    "type": "视频消息",
                    "content": "video payload should not be indexed",
                    "captionText": "演示视频：新版流程",
                    "createTime": 1_700_000_003,
                    "msgId": "video-caption",
                },
                {
                    "type": "图片消息",
                    "content": "image payload should stay skipped without OCR text",
                    "createTime": 1_700_000_004,
                    "msgId": "image-payload",
                },
            ],
        }

        result = parse_weflow(data, Path("media-text.json"))

        self.assertEqual(result.included, 4)
        self.assertEqual(result.skipped_by_type["图片消息(空内容)"], 1)
        self.assertEqual(
            [message.content for message in result.messages],
            [
                "白板 OCR：Q3 合同金额 10 万",
                "截图里写着周五上线",
                "语音转文字：先约客户复盘",
                "演示视频：新版流程",
            ],
        )
        self.assertEqual(
            [message.msg_type for message in result.messages],
            ["图片消息", "图片消息", "语音消息", "视频消息"],
        )

    def test_parse_weflow_uses_stable_unique_ids_for_blank_or_duplicate_export_ids(self) -> None:
        data = {
            "weflow": {},
            "session": {},
            "messages": [
                {
                    "type": "文本消息",
                    "content": "blank platform id falls back",
                    "createTime": 1_700_000_000,
                    "platformMessageId": "   ",
                    "localId": "local-blank-platform",
                },
                {
                    "type": "文本消息",
                    "content": "first duplicate",
                    "createTime": 1_700_000_001,
                    "msgId": "duplicate-id",
                },
                {
                    "type": "文本消息",
                    "content": "second duplicate",
                    "createTime": 1_700_000_002,
                    "msgId": "duplicate-id",
                },
                {
                    "type": "文本消息",
                    "content": "collides with generated duplicate suffix",
                    "createTime": 1_700_000_003,
                    "msgId": "duplicate-id:dup-2",
                },
            ],
        }

        result = parse_weflow(data, Path("duplicate.json"))

        self.assertEqual(
            [message.id for message in result.messages],
            [
                "duplicate.json:local-blank-platform",
                "duplicate.json:duplicate-id",
                "duplicate.json:duplicate-id:dup-2",
                "duplicate.json:duplicate-id:dup-2:dup-2",
            ],
        )

    def test_parse_weflow_scopes_export_local_message_ids_by_file_to_avoid_cross_chat_collisions(self) -> None:
        base = {
            "weflow": {},
            "session": {"displayName": "项目群"},
            "messages": [
                {
                    "type": "文本消息",
                    "content": "same local id",
                    "createTime": 1_700_000_000,
                    "msgId": "1",
                }
            ],
        }

        first = parse_weflow(base, Path("first.json"))
        second = parse_weflow(base, Path("second.json"))

        self.assertEqual(first.messages[0].id, "first.json:1")
        self.assertEqual(second.messages[0].id, "second.json:1")
        self.assertNotEqual(first.messages[0].id, second.messages[0].id)

    def test_parse_weflow_scopes_platform_message_ids_by_file_to_avoid_cross_chat_collisions(self) -> None:
        base = {
            "weflow": {},
            "session": {"displayName": "项目群"},
            "messages": [
                {
                    "type": "文本消息",
                    "content": "same platform id",
                    "createTime": 1_700_000_000,
                    "platformMessageId": "platform-same",
                }
            ],
        }

        first = parse_weflow(base, Path("first.json"))
        second = parse_weflow(base, Path("second.json"))

        self.assertEqual(first.messages[0].id, "first.json:platform-same")
        self.assertEqual(second.messages[0].id, "second.json:platform-same")
        self.assertNotEqual(first.messages[0].id, second.messages[0].id)

    def test_parse_weflow_includes_relative_directories_in_file_scope_for_duplicate_filenames(self) -> None:
        base = {
            "weflow": {},
            "session": {"displayName": "项目群"},
            "messages": [
                {
                    "type": "文本消息",
                    "content": "same filename in different folders",
                    "createTime": 1_700_000_000,
                    "msgId": "1",
                }
            ],
        }

        first = parse_weflow(base, Path("account-a/chat.json"))
        second = parse_weflow(base, Path("account-b/chat.json"))

        self.assertEqual(first.messages[0].id, "account-a/chat.json:1")
        self.assertEqual(second.messages[0].id, "account-b/chat.json:1")
        self.assertNotEqual(first.messages[0].id, second.messages[0].id)

    def test_parse_weflow_uses_upload_meta_scope_for_reuploaded_chat_exports(self) -> None:
        data = {
            "weflow": {},
            "session": {"displayName": "项目群", "wxid": "room-123@chatroom"},
            "messages": [
                {
                    "type": "文本消息",
                    "content": "same chat uploaded again",
                    "createTime": 1_700_000_000,
                    "platformMessageId": "platform-same",
                }
            ],
        }
        scope = stable_upload_scope(data, "Project Chat.json")

        with TemporaryDirectory() as tmp:
            first = Path(tmp) / "uploads" / "first-random.json"
            second = Path(tmp) / "uploads" / "second-random.json"
            first.parent.mkdir()
            first.write_text("{}", encoding="utf-8")
            second.write_text("{}", encoding="utf-8")
            first.with_suffix(first.suffix + ".meta").write_text(
                json.dumps({"scope": scope}, ensure_ascii=False),
                encoding="utf-8",
            )
            second.with_suffix(second.suffix + ".meta").write_text(
                json.dumps({"scope": scope}, ensure_ascii=False),
                encoding="utf-8",
            )

            first_result = parse_weflow(data, first)
            second_result = parse_weflow(data, second)
            self.assertEqual(file_scope_for_path(first), scope)

        self.assertEqual(first_result.messages[0].id, f"{scope}:platform-same")
        self.assertEqual(second_result.messages[0].id, f"{scope}:platform-same")

    def test_stable_upload_scope_uses_session_name_when_export_lacks_wxid(self) -> None:
        first_export = {
            "weflow": {},
            "session": {"displayName": "没有 wxid 的群"},
            "messages": [
                {
                    "type": "文本消息",
                    "content": "older message",
                    "createTime": 1_700_000_000,
                    "platformMessageId": "older",
                }
            ],
        }
        appended_export = {
            "weflow": {},
            "session": {"displayName": "没有 wxid 的群"},
            "messages": [
                {
                    "type": "文本消息",
                    "content": "newer message only",
                    "createTime": 1_700_010_000,
                    "platformMessageId": "newer",
                }
            ],
        }
        other_chat = {
            "weflow": {},
            "session": {"displayName": "另一个没有 wxid 的群"},
            "messages": first_export["messages"],
        }

        self.assertEqual(
            stable_upload_scope(first_export, "first.json"),
            stable_upload_scope(appended_export, "renamed.json"),
        )
        self.assertNotEqual(
            stable_upload_scope(first_export, "first.json"),
            stable_upload_scope(other_chat, "first.json"),
        )

    def test_parse_weflow_accepts_string_or_boolean_self_flags(self) -> None:
        data = {
            "weflow": {},
            "session": {"displayName": "好友"},
            "messages": [
                {
                    "type": "文本消息",
                    "content": "string one flag",
                    "createTime": 1_700_000_000,
                    "isSend": "1",
                    "senderDisplayName": "我自己",
                    "msgId": "m1",
                },
                {
                    "type": "文本消息",
                    "content": "boolean true flag",
                    "createTime": 1_700_000_001,
                    "isSend": True,
                    "msgId": "m2",
                },
                {
                    "type": "文本消息",
                    "content": "string false flag",
                    "createTime": 1_700_000_002,
                    "isSend": "0",
                    "senderDisplayName": "好友备注",
                    "msgId": "m3",
                },
                {
                    "type": "文本消息",
                    "content": "snake case self flag",
                    "createTime": 1_700_000_003,
                    "is_self": True,
                    "senderName": "本人 snake",
                    "msgId": "m4",
                },
                {
                    "type": "文本消息",
                    "content": "from me alias",
                    "createTime": 1_700_000_004,
                    "fromMe": "yes",
                    "senderName": "本人 alias",
                    "msgId": "m5",
                },
            ],
        }

        result = parse_weflow(data, Path("self-flag.json"))

        self.assertEqual([message.is_self for message in result.messages], [1, 1, 0, 1, 1])
        self.assertEqual(
            [message.sender for message in result.messages],
            ["我自己", "我", "好友备注", "本人 snake", "本人 alias"],
        )

    def test_parse_weflow_uses_sender_fallbacks_before_thread_name_for_group_messages(self) -> None:
        data = {
            "weflow": {},
            "session": {"displayName": "项目群", "wxid": "group-wxid"},
            "messages": [
                {
                    "type": "文本消息",
                    "content": "remark sender",
                    "createTime": 1_700_000_000,
                    "isSend": 0,
                    "senderDisplayName": "",
                    "senderRemark": "张三备注",
                    "senderNickname": "张三昵称",
                    "senderUsername": "wxid-zhangsan",
                    "msgId": "m1",
                },
                {
                    "type": "文本消息",
                    "content": "nickname sender",
                    "createTime": 1_700_000_001,
                    "isSend": 0,
                    "senderDisplayName": " ",
                    "senderRemark": "",
                    "senderNickname": "李四昵称",
                    "senderUsername": "wxid-lisi",
                    "msgId": "m2",
                },
                {
                    "type": "文本消息",
                    "content": "username sender",
                    "createTime": 1_700_000_002,
                    "isSend": 0,
                    "senderDisplayName": "",
                    "senderRemark": "",
                    "senderNickname": "",
                    "senderUsername": "wxid-wangwu",
                    "msgId": "m3",
                },
            ],
        }

        result = parse_weflow(data, Path("group-senders.json"))

        self.assertEqual(
            [message.sender for message in result.messages],
            ["张三备注", "李四昵称", "wxid-wangwu"],
        )

    def test_parse_weflow_accepts_common_session_and_sender_name_aliases(self) -> None:
        data = {
            "weflow": {},
            "session": {"display_name": "", "title": "别名项目群", "user_name": "peer-wxid"},
            "messages": [
                {
                    "type": "文本消息",
                    "content": "self sender alias",
                    "createTime": 1_700_000_000,
                    "isSend": True,
                    "senderDisplayName": "",
                    "senderName": "本人别名",
                    "msgId": "m1",
                },
                {
                    "type": "文本消息",
                    "content": "group member aliases",
                    "createTime": 1_700_000_001,
                    "isSend": 0,
                    "sender_display_name": "",
                    "fromNickname": "群成员昵称",
                    "fromUserName": "wxid-member",
                    "msgId": "m2",
                },
                {
                    "type": "文本消息",
                    "content": "peer username should use session title",
                    "createTime": 1_700_000_002,
                    "isSend": 0,
                    "fromUserName": "peer-wxid",
                    "msgId": "m3",
                },
            ],
        }

        result = parse_weflow(data, Path("alias-senders.json"))

        self.assertEqual(result.thread, "别名项目群")
        self.assertEqual(
            [message.sender for message in result.messages],
            ["本人别名", "群成员昵称", "别名项目群"],
        )

    def test_parse_weflow_merges_separate_quoted_fields_into_reference_text(self) -> None:
        data = {
            "weflow": {},
            "session": {"displayName": "项目群"},
            "messages": [
                {
                    "type": "引用消息",
                    "content": "回复：这个方案可以",
                    "quotedSender": "Alice",
                    "quotedContent": "原文：周五前给预算",
                    "createTime": 1_700_000_000,
                    "msgId": "quote-1",
                },
                {
                    "type": "引用消息",
                    "content": "回复里已经包含 原文：不要重复",
                    "quotedSender": "Bob",
                    "quotedContent": "原文：不要重复",
                    "createTime": 1_700_000_001,
                    "msgId": "quote-2",
                },
                {
                    "type": "引用消息",
                    "content": "",
                    "quotedSender": "Carol",
                    "quotedContent": "只有引用也应该可检索",
                    "createTime": 1_700_000_002,
                    "msgId": "quote-3",
                },
                {
                    "type": "引用消息",
                    "content": "snake case reply",
                    "quoted_sender": "Dave",
                    "quoted_content": "snake case quote",
                    "createTime": 1_700_000_003,
                    "msgId": "quote-4",
                },
                {
                    "type": "引用消息",
                    "content": "camel quote alias reply",
                    "quoteSender": "Eve",
                    "quoteContent": "original camel alias quote",
                    "createTime": 1_700_000_004,
                    "msgId": "quote-5",
                },
            ],
        }

        result = parse_weflow(data, Path("quoted-fields.json"))

        self.assertEqual(result.included, 5)
        self.assertEqual(result.messages[0].content, "回复：这个方案可以\n[引用 Alice：原文：周五前给预算]")
        self.assertEqual(result.messages[1].content, "回复里已经包含 原文：不要重复")
        self.assertEqual(result.messages[2].content, "[引用 Carol：只有引用也应该可检索]")
        self.assertEqual(result.messages[3].content, "snake case reply\n[引用 Dave：snake case quote]")
        self.assertEqual(result.messages[4].content, "camel quote alias reply\n[引用 Eve：original camel alias quote]")


if __name__ == "__main__":
    unittest.main()
