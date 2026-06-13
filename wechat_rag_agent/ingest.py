from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

from . import store
from .chunker import Chunk, chunk_thread
from .console import setup_utf8_console
from .llm import chat_configured, chat_model, embed, embed_configured
from .parser import is_weflow_export, parse_weflow


load_dotenv()

SUMMARY_PROMPT = "用一句话（30字内）概括这段聊天讨论的主题和关键事项，直接输出概括，不要任何前缀。聊天内容：\n\n"
SUMMARY_FLUSH_EVERY = 200


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
    parser.add_argument("--force-chunks", action="store_true", help="强制重建会话分块（内容未变的块仍会复用已有摘要和向量）")
    parser.add_argument("--force-summary", action="store_true", help="强制重新生成所有摘要")
    parser.add_argument(
        "--force-embeddings",
        "--force-vector",
        "--force-vectors",
        action="store_true",
        dest="force_embeddings",
        help="强制重建向量索引",
    )
    parser.add_argument("--summary-workers", type=positive_int, default=20, help="摘要生成并发数，默认 20")
    parser.add_argument("--embed-workers", type=positive_int, default=4, help="embedding 批次并发数，默认 4")
    parser.add_argument("--embed-batch-size", type=positive_int, default=32, help="每个 embedding 请求批次的会话块数量，默认 32")
    return parser.parse_args()


def summarize_chunk(model_name: str, chunk: Chunk) -> tuple[str | None, str | None]:
    try:
        res = chat_model(model_name).invoke(SUMMARY_PROMPT + chunk.text[:3000])
        summary = str(res.content).strip()
        return (summary or None), None
    except Exception as exc:
        return None, type(exc).__name__


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


def ingest_files(files: list[Path], pending_file_records: list[tuple[str, int, int, int, int, int]]) -> tuple[int, int, set[str]]:
    total_included = 0
    total_inserted = 0
    affected_threads: set[str] = set()
    for file in files:
        stat = file.stat()
        file_key = str(file.resolve())
        if store.ingest_file_unchanged(file_key, stat.st_size, stat.st_mtime_ns):
            print(f"  skip {file}: 文件未变化，跳过解析")
            continue

        try:
            data = json.loads(file.read_bytes())
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
        if inserted > 0:
            affected_threads.add(result.thread)
        record = (file_key, stat.st_size, stat.st_mtime_ns, result.total, result.included, inserted)
        if inserted == 0:
            store.record_ingest_file(*record)
        else:
            pending_file_records.append(record)
        skipped = " ".join(f"{name}×{count}" for name, count in result.skipped_by_type.items())
        suffix = f"，跳过: {skipped}" if skipped else ""
        print(f"  ok [{result.thread}] 总 {result.total} 条，入库 {result.included} 条（新增 {inserted}）{suffix}")
    return total_included, total_inserted, affected_threads


def rebuild_chunks_scoped(
    scope_threads: list[str] | None,
    force_summary: bool,
    force_embeddings: bool,
) -> None:
    """重建指定线程（None=全部）的会话分块；内容未变的块复用已有摘要和向量。"""
    by_thread = store.get_all_messages_by_thread(scope_threads)
    rebuilt: list[Chunk] = []
    for thread, messages in by_thread.items():
        rebuilt.extend(chunk_thread(thread, messages))

    carry = store.get_carryover_for_threads(scope_threads)
    new_ids = store.replace_sessions(rebuilt, scope_threads)

    carried_summaries: list[tuple[int, str]] = []
    carried_vectors: list[dict[str, object]] = []
    for session_id, chunk in zip(new_ids, rebuilt):
        hit = carry.pop(store.chunk_text_hash(chunk.text), None)
        if hit is None:
            continue
        old_summary, old_vector = hit
        if old_summary and not force_summary:
            carried_summaries.append((session_id, old_summary))
        if old_vector is not None and not force_embeddings:
            carried_vectors.append({"session_id": session_id, "embedding": old_vector})

    store.set_summaries(carried_summaries)
    if carried_vectors and store.has_vec():
        store.insert_embeddings(carried_vectors)

    scope_label = "全部线程" if scope_threads is None else f"{len(by_thread)} 个线程"
    print(
        f"会话分块完成：重建 {scope_label} 共 {len(rebuilt)} 块，"
        f"复用未变化块的摘要 {len(carried_summaries)} 条、向量 {len(carried_vectors)} 条"
    )


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

    pending_file_records: list[tuple[str, int, int, int, int, int]] = []
    total_included, total_inserted, affected_threads = ingest_files(files, pending_file_records)

    has_new_messages = total_inserted > 0
    summary_model = os.getenv("SUMMARY_MODEL")
    summary_ready = bool(summary_model) and chat_configured() and not args.no_summary
    embed_ready = embed_configured() and store.has_vec()

    # 自愈：即使没有新消息，也补齐上次中断/失败留下的缺失索引、摘要和向量
    missing_summaries = store.count_sessions_missing_summary() if summary_ready else 0
    missing_embeddings = len(store.get_all_session_ids_without_embedding()) if embed_ready else 0
    missing_fts = store.count_missing_fts()
    missing_seq = store.count_messages_missing_seq()

    should_touch_fts = has_new_messages or force_fts or missing_fts > 0 or missing_seq > 0
    should_rebuild_chunks = has_new_messages or force_chunks
    should_generate_summary = (has_new_messages or force_summary or missing_summaries > 0) and not args.no_summary
    should_build_embeddings = has_new_messages or force_embeddings or missing_embeddings > 0

    if not any([should_touch_fts, should_rebuild_chunks, should_generate_summary, should_build_embeddings]):
        print("\n本次新增消息为 0，摘要和向量索引完整，无需重建。")
        print("可按需加 --force-fts / --force-chunks / --force-summary / --force-embeddings。")
        return

    if missing_summaries and not has_new_messages and not force_summary:
        print(f"\n检测到 {missing_summaries} 个会话块缺少摘要，本次将自动补齐")
    if missing_embeddings and not has_new_messages and not force_embeddings:
        print(f"检测到 {missing_embeddings} 个会话块缺少向量，本次将自动补齐")
    if (missing_fts or missing_seq) and not has_new_messages and not force_fts:
        print(f"检测到 FTS 索引缺失 {missing_fts} 条 / seq 缺失 {missing_seq} 条，本次将自动补齐")

    if force_fts:
        store.recompute_message_sequence()
        store.rebuild_fts()
        print(f"\nFTS 索引已强制全量重建（本次解析 {total_included} 条文本/引用消息，新增 {total_inserted} 条）")
    elif has_new_messages or missing_fts or missing_seq:
        store.recompute_message_sequence(None if missing_seq and not has_new_messages else sorted(affected_threads))
        synced = store.sync_missing_fts()
        print(f"\n消息入库完成（新增 {total_inserted} 条），FTS 增量索引 {synced} 条")
    else:
        print("\n消息无变化，跳过 FTS")

    chunks: list[Chunk] = []
    session_ids: list[int] = []
    summaries: dict[int, str] = {}

    if should_rebuild_chunks:
        scope = None if force_chunks else sorted(affected_threads)
        rebuild_chunks_scoped(scope, force_summary, force_embeddings)
        chunks, session_ids, summaries = load_existing_chunks()
        total_messages = store.stats()["total_messages"]
        avg = total_messages / max(len(chunks), 1)
        print(f"全库会话分块：{len(chunks)} 个块（平均 {avg:.1f} 条/块）")
    elif should_generate_summary or should_build_embeddings:
        chunks, session_ids, summaries = load_existing_chunks()
        if not chunks:
            print("没有可用会话分块；请先运行 --force-chunks。")
            record_pending_files(pending_file_records)
            return
        print(f"复用已有会话分块：{len(chunks)} 个块")
    else:
        print("跳过会话分块")

    # ---- 摘要 / 向量流水线：摘要完成的块立即进入 embedding 批队列，两阶段并行 ----
    summary_enabled = should_generate_summary and summary_ready
    if should_generate_summary and not summary_enabled:
        reason = "--no-summary" if args.no_summary else "未配置 SUMMARY_MODEL"
        print(f"跳过摘要前缀（{reason}）")
    elif not should_generate_summary:
        print("跳过摘要前缀（未请求摘要重建）")

    embed_enabled = should_build_embeddings and embed_ready
    if should_build_embeddings and not embed_enabled:
        record_pending_files(pending_file_records)
        if not embed_configured():
            print("未配置 EMBED_*，跳过向量索引（semantic_search 将退化为全文检索）。配置 .env 后重跑 ingest 即可补建。")
        else:
            print("sqlite-vec 不可用，跳过向量索引。")
        if not summary_enabled:
            return

    to_summarize: list[int] = []
    if summary_enabled:
        if force_summary:
            to_summarize = list(range(len(chunks)))
        else:
            to_summarize = [i for i, sid in enumerate(session_ids) if sid not in summaries]
    to_summarize_set = set(to_summarize)

    # 新生成的摘要会改变 embedding 输入文本，这些块的向量需要连带重建
    if embed_ready and to_summarize and not embed_enabled:
        embed_enabled = True

    to_embed_set: set[int] = set()
    if embed_enabled:
        have_vec = store.get_session_ids_with_embedding()
        for i, sid in enumerate(session_ids):
            if force_embeddings or sid not in have_vec or i in to_summarize_set:
                to_embed_set.add(i)

    if summary_enabled and not to_summarize:
        print(f"摘要已是最新（{len(summaries)}/{len(chunks)} 块），跳过生成")
    if embed_enabled and not to_embed_set:
        print("向量索引已是最新，跳过 embedding")

    if not to_summarize and not to_embed_set:
        record_pending_files(pending_file_records)
        print("\n无需生成摘要或向量。现在可以运行 python -m wechat_rag_agent.cli")
        return

    batch_size = args.embed_batch_size
    embed_futures: dict[Future, list[int]] = {}
    pending_embed: list[int] = []
    summary_buffer: list[tuple[int, str]] = []
    embed_fail_sessions = 0

    if to_summarize:
        print(f"生成摘要前缀（{summary_model}，{len(to_summarize)} 块，并发 {args.summary_workers}）...")
    if to_embed_set:
        print(f"生成 embedding（{os.getenv('EMBED_MODEL')}，{len(to_embed_set)} 块，批 {batch_size}，并发 {args.embed_workers}）...")

    with ThreadPoolExecutor(max_workers=args.summary_workers) as summary_pool, \
            ThreadPoolExecutor(max_workers=args.embed_workers) as embed_pool:

        def submit_embed_batch(indices: list[int]) -> None:
            inputs: list[str] = []
            for i in indices:
                summary = summaries.get(session_ids[i])
                inputs.append(f"{summary}\n{chunks[i].text}" if summary else chunks[i].text)
            embed_futures[embed_pool.submit(embed, inputs)] = list(indices)

        def queue_for_embed(index: int) -> None:
            pending_embed.append(index)
            if len(pending_embed) >= batch_size:
                submit_embed_batch(pending_embed[:batch_size])
                del pending_embed[:batch_size]

        # 不需要等摘要的块（已有摘要或本次不生成摘要）直接进入 embedding 队列
        for i in sorted(to_embed_set - to_summarize_set):
            queue_for_embed(i)

        if to_summarize:
            futures = {
                summary_pool.submit(summarize_chunk, summary_model, chunks[i]): i
                for i in to_summarize
            }
            done = 0
            generated = 0
            summary_errors: dict[str, int] = {}
            for future in as_completed(futures):
                index = futures[future]
                session_id = session_ids[index]
                summary, error = future.result()
                if summary:
                    summaries[session_id] = summary
                    summary_buffer.append((session_id, summary))
                    generated += 1
                    if len(summary_buffer) >= SUMMARY_FLUSH_EVERY:
                        store.set_summaries(summary_buffer)
                        summary_buffer.clear()
                elif error:
                    summary_errors[error] = summary_errors.get(error, 0) + 1
                done += 1
                if done % 50 == 0 or done == len(to_summarize):
                    print(f"  摘要 {done}/{len(to_summarize)}")
                if index in to_embed_set:
                    queue_for_embed(index)
            store.set_summaries(summary_buffer)
            summary_buffer.clear()
            print(f"摘要完成：本次生成 {generated}/{len(to_summarize)}，全库 {len(summaries)}/{len(chunks)}")
            if summary_errors:
                detail = "，".join(f"{name}×{count}" for name, count in sorted(summary_errors.items()))
                print(f"  [警告] {sum(summary_errors.values())} 条摘要请求失败（{detail}）；重跑 ingest 将自动补齐")

        if pending_embed:
            submit_embed_batch(pending_embed)
            pending_embed.clear()

        embedded = 0
        for future in as_completed(embed_futures):
            indices = embed_futures[future]
            try:
                vectors = future.result()
            except Exception as exc:
                embed_fail_sessions += len(indices)
                print(f"\n[警告] 一批 embedding 生成失败（{len(indices)} 块）：{exc}")
                continue
            if vectors:
                actual_dim = len(vectors[0])
                recreated = store.ensure_vector_table_dimension(actual_dim)
                if recreated:
                    print(f"\n检测到 embedding 实际维度为 {actual_dim}，已按该维度重建 sessions_vec 向量表。")
            store.insert_embeddings(
                [
                    {"session_id": session_ids[i], "embedding": vector}
                    for i, vector in zip(indices, vectors)
                ]
            )
            embedded += len(vectors)
            print(f"  embedding {embedded}/{len(to_embed_set)}", end="\r")

    record_pending_files(pending_file_records)

    if embed_fail_sessions:
        print(f"\n[警告] {embed_fail_sessions} 块向量生成失败；消息库与其余索引已保留，重跑 ingest 将只补建缺失部分。")
    if to_embed_set:
        print(f"\n向量索引完成（本次新建 {len(to_embed_set) - embed_fail_sessions} 块）。现在可以运行 python -m wechat_rag_agent.cli")
    else:
        print("\n索引更新完成。现在可以运行 python -m wechat_rag_agent.cli")


if __name__ == "__main__":
    main()
