from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from dotenv import load_dotenv

from . import store
from .chunker import Chunk, chunk_thread
from .console import setup_utf8_console
from .llm import chat_configured, chat_model, embed, embed_configured
from .parser import is_weflow_export, parse_weflow


load_dotenv()

SUMMARY_PROMPT = "用一句话（30字内）概括这段聊天讨论的主题和关键事项，直接输出概括，不要任何前缀。聊天内容：\n\n"


def collect_json_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return [path for path in target.iterdir() if path.is_file() and path.suffix.lower() == ".json"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导入 WeFlow 微信聊天 JSON，并构建 SQLite/FTS/向量索引。")
    parser.add_argument("targets", nargs="+", help="JSON 文件或目录，可传多个")
    parser.add_argument("--no-summary", action="store_true", help="跳过索引期摘要前缀")
    return parser.parse_args()


def main() -> None:
    setup_utf8_console()

    args = parse_args()

    files: list[Path] = []
    for raw in args.targets:
        target = Path(raw)
        if not target.exists():
            print(f"路径不存在，跳过：{target}")
            continue
        files.extend(collect_json_files(target))

    print(f"发现 {len(files)} 个 JSON 文件")

    total_included = 0
    for file in files:
        try:
            data = json.loads(file.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"  x {file}: JSON 解析失败（{exc}）")
            continue

        if not is_weflow_export(data):
            print(f"  x {file}: 非 WeFlow 导出格式（缺少顶层 weflow 键），跳过")
            continue

        result = parse_weflow(data, file)
        inserted = store.insert_messages(result.messages)
        total_included += result.included
        skipped = " ".join(f"{name}×{count}" for name, count in result.skipped_by_type.items())
        suffix = f"，跳过: {skipped}" if skipped else ""
        print(f"  ok [{result.thread}] 总 {result.total} 条，入库 {result.included} 条（新增 {inserted}）{suffix}")

    store.finalize_ingest()
    print(f"\n消息入库完成（共 {total_included} 条文本/引用消息），FTS 索引已重建")

    by_thread = store.get_all_messages_by_thread()
    chunks: list[Chunk] = []
    for thread, messages in by_thread.items():
        chunks.extend(chunk_thread(thread, messages))
    session_ids = store.replace_sessions(chunks)
    avg = total_included / max(len(chunks), 1)
    print(f"会话分块完成：{len(chunks)} 个块（平均 {avg:.1f} 条/块）")

    summary_model = os.getenv("SUMMARY_MODEL")
    summaries: dict[int, str] = {}
    if not args.no_summary and summary_model and chat_configured():
        print(f"生成摘要前缀（{summary_model}）...")
        model = chat_model(summary_model)
        for index, chunk in enumerate(chunks, start=1):
            try:
                res = model.invoke(SUMMARY_PROMPT + chunk.text[:3000])
                summary = str(res.content).strip()
                if summary:
                    session_id = session_ids[index - 1]
                    summaries[session_id] = summary
                    store.set_summary(session_id, summary)
            except Exception:
                pass
            if index % 50 == 0:
                print(f"  摘要 {index}/{len(chunks)}")
        print(f"摘要完成：{len(summaries)}/{len(chunks)}")
    else:
        reason = "--no-summary" if args.no_summary else "未配置 SUMMARY_MODEL"
        print(f"跳过摘要前缀（{reason}）")

    if not embed_configured():
        print("未配置 EMBED_*，跳过向量索引（semantic_search 将退化为全文检索）。配置 .env 后重跑 ingest 即可补建。")
        return

    if not store.has_vec():
        print("sqlite-vec 不可用，跳过向量索引。")
        return

    print(f"生成 embedding（{os.getenv('EMBED_MODEL')}，{len(chunks)} 块）...")
    batch_size = 32
    for start in range(0, len(chunks), batch_size):
        batch = chunks[start : start + batch_size]
        inputs: list[str] = []
        for offset, chunk in enumerate(batch):
            session_id = session_ids[start + offset]
            summary = summaries.get(session_id)
            inputs.append(f"{summary}\n{chunk.text}" if summary else chunk.text)
        try:
            vectors = embed(inputs)
        except Exception as exc:
            print(f"\nembedding 生成失败：{exc}")
            print("已保留消息库、FTS 索引和会话分块；修正 EMBED_* 后重跑 ingest 即可补建向量索引。")
            return
        if vectors:
            actual_dim = len(vectors[0])
            recreated = store.ensure_vector_table_dimension(actual_dim)
            if recreated:
                print(
                    f"\n检测到 embedding 实际维度为 {actual_dim}，已按该维度重建 sessions_vec 向量表。"
                )
        store.insert_embeddings(
            [
                {"session_id": session_ids[start + offset], "embedding": vector}
                for offset, vector in enumerate(vectors)
            ]
        )
        print(f"  embedding {min(start + batch_size, len(chunks))}/{len(chunks)}", end="\r")

    print("\n向量索引完成。现在可以运行 python -m wechat_rag_agent.cli")


if __name__ == "__main__":
    main()
