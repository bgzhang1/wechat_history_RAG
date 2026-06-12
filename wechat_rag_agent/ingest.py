from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

from . import store
from .chunker import Chunk, chunk_thread
from .console import setup_utf8_console
from .llm import chat_configured, chat_model, embed, embed_configured
from .parser import is_weflow_export, parse_weflow


load_dotenv()

SUMMARY_PROMPT = "用一句话（30字内）概括这段聊天讨论的主题和关键事项，直接输出概括，不要任何前缀。聊天内容：\n\n"


def positive_int(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError("必须是 >= 1 的整数")
    return value


def collect_json_files(target: Path) -> list[Path]:
    if target.is_file():
        return [target]
    return [path for path in target.iterdir() if path.is_file() and path.suffix.lower() == ".json"]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导入 WeFlow 微信聊天 JSON，并构建 SQLite/FTS/向量索引。")
    parser.add_argument("targets", nargs="+", help="JSON 文件或目录，可传多个")
    parser.add_argument("--no-summary", action="store_true", help="跳过索引期摘要前缀")
    parser.add_argument("--force-rebuild", action="store_true", help="即使本次新增消息为 0，也强制重建 FTS、会话分块、摘要和向量索引")
    parser.add_argument("--force-fts", action="store_true", help="强制重建 FTS 全文索引")
    parser.add_argument("--force-chunks", action="store_true", help="强制重建会话分块")
    parser.add_argument("--force-summary", action="store_true", help="强制重新生成摘要")
    parser.add_argument(
        "--force-embeddings",
        "--force-vector",
        "--force-vectors",
        action="store_true",
        dest="force_embeddings",
        help="强制重建向量索引",
    )
    parser.add_argument("--summary-workers", type=positive_int, default=1, help="摘要生成并发数，默认 1")
    parser.add_argument("--embed-workers", type=positive_int, default=1, help="embedding 批次并发数，默认 1")
    parser.add_argument("--embed-batch-size", type=positive_int, default=32, help="每个 embedding 请求批次的会话块数量，默认 32")
    return parser.parse_args()


def summarize_chunk(model_name: str, chunk: Chunk) -> str | None:
    try:
        res = chat_model(model_name).invoke(SUMMARY_PROMPT + chunk.text[:3000])
        summary = str(res.content).strip()
        return summary or None
    except Exception:
        return None


def build_embedding_inputs(
    chunks: list[Chunk],
    session_ids: list[int],
    summaries: dict[int, str],
    start: int,
    batch_size: int,
) -> tuple[int, list[str]]:
    batch = chunks[start : start + batch_size]
    inputs: list[str] = []
    for offset, chunk in enumerate(batch):
        session_id = session_ids[start + offset]
        summary = summaries.get(session_id)
        inputs.append(f"{summary}\n{chunk.text}" if summary else chunk.text)
    return start, inputs


def record_pending_files(records: list[tuple[str, int, int, int, int, int]]) -> None:
    for record in records:
        store.record_ingest_file(*record)
    records.clear()


def load_existing_chunks() -> tuple[list[Chunk], list[int], dict[int, str]]:
    rows = store.get_all_sessions()
    chunks = [
        Chunk(
            thread=row["thread"],
            start_time=row["start_time"],
            end_time=row["end_time"],
            participants=row["participants"],
            msg_ids=row["msg_ids"],
            text=row["text"],
            summary=row["summary"],
        )
        for row in rows
    ]
    session_ids = [int(row["session_id"]) for row in rows]
    summaries = {
        int(row["session_id"]): row["summary"]
        for row in rows
        if row.get("summary")
    }
    return chunks, session_ids, summaries


def main() -> None:
    setup_utf8_console()

    args = parse_args()
    force_fts = args.force_rebuild or args.force_fts
    force_chunks = args.force_rebuild or args.force_chunks
    force_summary = args.force_rebuild or args.force_summary
    force_embeddings = args.force_rebuild or args.force_embeddings

    files: list[Path] = []
    for raw in args.targets:
        target = Path(raw)
        if not target.exists():
            print(f"路径不存在，跳过：{target}")
            continue
        files.extend(collect_json_files(target))

    print(f"发现 {len(files)} 个 JSON 文件")

    total_included = 0
    total_inserted = 0
    pending_file_records: list[tuple[str, int, int, int, int, int]] = []
    for file in files:
        stat = file.stat()
        file_key = str(file.resolve())
        if store.ingest_file_unchanged(file_key, stat.st_size, stat.st_mtime_ns):
            print(f"  skip {file}: 文件未变化，跳过解析")
            continue

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
        total_inserted += inserted
        record = (file_key, stat.st_size, stat.st_mtime_ns, result.total, result.included, inserted)
        if inserted == 0:
            store.record_ingest_file(*record)
        else:
            pending_file_records.append(record)
        skipped = " ".join(f"{name}×{count}" for name, count in result.skipped_by_type.items())
        suffix = f"，跳过: {skipped}" if skipped else ""
        print(f"  ok [{result.thread}] 总 {result.total} 条，入库 {result.included} 条（新增 {inserted}）{suffix}")

    has_new_messages = total_inserted > 0
    should_touch_fts = has_new_messages or force_fts
    should_rebuild_chunks = has_new_messages or force_chunks
    should_generate_summary = (has_new_messages or force_summary) and not args.no_summary
    should_build_embeddings = has_new_messages or force_embeddings

    if not any([should_touch_fts, should_rebuild_chunks, should_generate_summary, should_build_embeddings]):
        print("\n本次新增消息为 0，且没有指定强制重建阶段，跳过 FTS、会话分块、摘要和向量索引。")
        print("可按需加 --force-fts / --force-chunks / --force-summary / --force-embeddings。")
        return

    if force_fts:
        store.recompute_message_sequence()
        store.rebuild_fts()
        print(f"\nFTS 索引已强制全量重建（本次解析 {total_included} 条文本/引用消息，新增 {total_inserted} 条）")
    elif has_new_messages:
        store.recompute_message_sequence()
        synced = store.sync_missing_fts()
        print(f"\n消息入库完成（新增 {total_inserted} 条），FTS 增量索引 {synced} 条")
    else:
        print("\n跳过 FTS 重建")

    chunks: list[Chunk] = []
    session_ids: list[int] = []
    summaries: dict[int, str] = {}

    if should_rebuild_chunks:
        by_thread = store.get_all_messages_by_thread()
        for thread, messages in by_thread.items():
            chunks.extend(chunk_thread(thread, messages))
        session_ids = store.replace_sessions(chunks)
        total_messages = store.stats()["total_messages"]
        avg = total_messages / max(len(chunks), 1)
        print(f"会话分块完成：{len(chunks)} 个块（平均 {avg:.1f} 条/块）")
    elif should_generate_summary or should_build_embeddings:
        chunks, session_ids, summaries = load_existing_chunks()
        if not chunks:
            print("没有可用会话分块；请先运行 --force-chunks。")
            record_pending_files(pending_file_records)
            return
        print(f"复用已有会话分块：{len(chunks)} 个块")
    else:
        print("跳过会话分块")

    summary_model = os.getenv("SUMMARY_MODEL")
    if should_generate_summary and summary_model and chat_configured():
        print(f"生成摘要前缀（{summary_model}，并发 {args.summary_workers}）...")
        done = 0
        with ThreadPoolExecutor(max_workers=args.summary_workers) as pool:
            futures = {
                pool.submit(summarize_chunk, summary_model, chunk): (index, session_ids[index])
                for index, chunk in enumerate(chunks)
            }
            for future in as_completed(futures):
                index, session_id = futures[future]
                summary = future.result()
                if summary:
                    summaries[session_id] = summary
                    store.set_summary(session_id, summary)
                done += 1
                if done % 50 == 0 or done == len(chunks):
                    print(f"  摘要 {done}/{len(chunks)}")
        print(f"摘要完成：{len(summaries)}/{len(chunks)}")
    else:
        reason = (
            "--no-summary"
            if args.no_summary
            else "未配置 SUMMARY_MODEL"
            if should_generate_summary
            else "未请求摘要重建"
        )
        print(f"跳过摘要前缀（{reason}）")

    if not should_build_embeddings:
        record_pending_files(pending_file_records)
        print("跳过向量索引")
        return

    if not embed_configured():
        record_pending_files(pending_file_records)
        print("未配置 EMBED_*，跳过向量索引（semantic_search 将退化为全文检索）。配置 .env 后加 --force-rebuild 重跑即可补建。")
        return

    if not store.has_vec():
        record_pending_files(pending_file_records)
        print("sqlite-vec 不可用，跳过向量索引。")
        return

    print(f"生成 embedding（{os.getenv('EMBED_MODEL')}，{len(chunks)} 块）...")
    batch_size = args.embed_batch_size
    starts = list(range(0, len(chunks), batch_size))
    done = 0
    with ThreadPoolExecutor(max_workers=args.embed_workers) as pool:
        futures = {
            pool.submit(embed, inputs): start
            for start, inputs in (
                build_embedding_inputs(chunks, session_ids, summaries, start, batch_size)
                for start in starts
            )
        }
        for future in as_completed(futures):
            start = futures[future]
            try:
                vectors = future.result()
            except Exception as exc:
                print(f"\nembedding 生成失败：{exc}")
                print("已保留消息库、FTS 索引和会话分块；修正 EMBED_* 后加 --force-rebuild 重跑即可补建向量索引。")
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
            done += len(vectors)
            print(f"  embedding {min(done, len(chunks))}/{len(chunks)}", end="\r")

    record_pending_files(pending_file_records)

    print("\n向量索引完成。现在可以运行 python -m wechat_rag_agent.cli")


if __name__ == "__main__":
    main()
