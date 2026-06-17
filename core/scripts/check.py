from __future__ import annotations

from ..console import setup_utf8_console
from ..llm import EMBED_DIM, chat_configured, chat_model, embed, embed_configured


def main() -> None:
    setup_utf8_console()

    ok = True

    if chat_configured():
        try:
            res = chat_model().invoke("回复 OK 两个字母即可")
            print(f"ok chat 端点连通：{str(res.content).strip()}")
        except Exception as exc:
            ok = False
            print(f"x chat 端点失败：{exc}")
    else:
        ok = False
        print("x 未配置 CHAT_*（agent 对话必需）")

    if embed_configured():
        try:
            vector = embed(["连通性测试"])[0]
            dim_ok = len(vector) == EMBED_DIM
            suffix = "" if dim_ok else f"（与 EMBED_DIM={EMBED_DIM} 不同；ingest 会按实际维度自动重建向量表）"
            print(f"ok embeddings 端点连通，返回维度 {len(vector)}{suffix}")
        except Exception as exc:
            ok = False
            print(f"x embeddings 端点失败：{exc}")
    else:
        print("- 未配置 EMBED_*（可选；不配则语义检索退化为全文检索）")

    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
