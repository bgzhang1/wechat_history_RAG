"""OpenAI 兼容 mock 服务器：固定延迟返回摘要/embedding，用于公平对比管线编排效率。

用法: python bench/mock_llm.py [port]
环境变量: MOCK_CHAT_MS (默认 200), MOCK_EMBED_MS (默认 200), MOCK_DIM (默认 1024)
GET /stats 返回调用计数。POST /reset 清零计数。
"""
from __future__ import annotations

import base64
import json
import os
import struct
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CHAT_MS = int(os.getenv("MOCK_CHAT_MS", "200"))
EMBED_MS = int(os.getenv("MOCK_EMBED_MS", "200"))
DIM = int(os.getenv("MOCK_DIM", "1024"))

_lock = threading.Lock()
STATS = {"chat_calls": 0, "embed_calls": 0, "embed_inputs": 0}

_VEC = [((i % 7) + 1) * 0.01 for i in range(DIM)]
_VEC_B64 = base64.b64encode(struct.pack(f"<{DIM}f", *_VEC)).decode("ascii")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # 静音访问日志
        pass

    def _send(self, payload: dict, code: int = 200) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/stats":
            with _lock:
                self._send(dict(STATS))
        else:
            self._send({"error": "not found"}, 404)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length)
        try:
            req = json.loads(raw)
        except Exception:
            req = {}

        if self.path.endswith("/chat/completions"):
            time.sleep(CHAT_MS / 1000)
            with _lock:
                STATS["chat_calls"] += 1
            self._send({
                "id": "mock-chat", "object": "chat.completion", "created": 0,
                "model": req.get("model", "mock"),
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": "模拟摘要：测试会话内容概述"},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            })
        elif self.path.endswith("/embeddings"):
            time.sleep(EMBED_MS / 1000)
            inputs = req.get("input", [])
            if isinstance(inputs, str) or (inputs and isinstance(inputs[0], int)):
                count = 1
            else:
                count = len(inputs)
            with _lock:
                STATS["embed_calls"] += 1
                STATS["embed_inputs"] += count
            use_b64 = req.get("encoding_format") == "base64"
            data = [
                {"object": "embedding", "index": i,
                 "embedding": _VEC_B64 if use_b64 else _VEC}
                for i in range(count)
            ]
            self._send({
                "object": "list", "data": data,
                "model": req.get("model", "mock"),
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            })
        elif self.path == "/reset":
            with _lock:
                for key in STATS:
                    STATS[key] = 0
            self._send({"ok": True})
        else:
            self._send({"error": "not found"}, 404)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8765
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"mock LLM listening on 127.0.0.1:{port} (chat {CHAT_MS}ms, embed {EMBED_MS}ms, dim {DIM})", flush=True)
    server.serve_forever()
