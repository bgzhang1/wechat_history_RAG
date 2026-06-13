"""生成基准测试数据：
- bench/data/p95/chat_p95.json        : 原文件前 95% 消息（模拟旧一次的导出）
- bench/data/multi/chat_A.json (25k)  : 合成线程 A（模拟批量导入多个聊天）
- bench/data/multi/chat_B.json (25k)  : 合成线程 B
- bench/data/multi_add/chat_C.json(10k): 合成线程 C（后续追加导入）
"""
from __future__ import annotations

import copy
import glob
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = glob.glob(os.path.join(ROOT, "data", "*.json"))[0]


def write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    print(f"wrote {path} ({os.path.getsize(path) / 1e6:.1f} MB)")


def remap_ids(messages: list[dict], tag: str) -> list[dict]:
    out = []
    for msg in messages:
        msg = dict(msg)
        if msg.get("platformMessageId"):
            msg["platformMessageId"] = f"{tag}-{msg['platformMessageId']}"
        if msg.get("localId") is not None:
            msg["localId"] = f"{tag}-{msg['localId']}"
        if msg.get("replyToMessageId"):
            msg["replyToMessageId"] = f"{tag}-{msg['replyToMessageId']}"
        out.append(msg)
    return out


def synth_thread(data: dict, name: str, tag: str, messages: list[dict]) -> dict:
    payload = {key: copy.deepcopy(value) for key, value in data.items() if key != "messages"}
    payload["session"]["remark"] = name
    payload["session"]["displayName"] = name
    payload["session"]["nickname"] = name
    payload["messages"] = remap_ids(messages, tag)
    return payload


def main() -> None:
    data = json.loads(open(SRC, "rb").read())
    messages = data["messages"]
    n = len(messages)
    print(f"source: {n} messages")

    cut = int(n * 0.95)
    p95 = {key: value for key, value in data.items() if key != "messages"}
    p95["messages"] = messages[:cut]
    write_json(os.path.join(ROOT, "bench", "data", "p95", "chat_p95.json"), p95)
    print(f"p95: {cut} messages (delta when full file ingested: {n - cut})")

    write_json(
        os.path.join(ROOT, "bench", "data", "multi", "chat_A.json"),
        synth_thread(data, "测试群A", "A", messages[0:25000]),
    )
    write_json(
        os.path.join(ROOT, "bench", "data", "multi", "chat_B.json"),
        synth_thread(data, "测试群B", "B", messages[25000:50000]),
    )
    write_json(
        os.path.join(ROOT, "bench", "data", "multi_add", "chat_C.json"),
        synth_thread(data, "测试群C", "C", messages[50000:60000]),
    )


if __name__ == "__main__":
    main()
