from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from pathlib import Path

from dotenv import load_dotenv

from . import store
from .chunker import Chunk, chunk_thread
from .console import setup_utf8_console
from .llm import chat_configured, chat_model, embed, embed_configured
from .parser import is_weflow_export, parse_weflow


load_dotenv()

SUMMARY_PROMPT = "用一句话概括这段聊天讨论的主题和关键事项，直接输出概括，不要任何前缀。聊天内容：\n\n"
SUMMARY_FLUSH_EVERY = 200
DEFAULT_PROGRESS_EVERY = 50
DEFAULT_PROGRESS_INTERVAL = 15.0
DEFAULT_SUMMARY_BATCH_SIZE = 4
DEFAULT_SUMMARY_MAX_CHARS = 3000
DEFAULT_SUMMARY_FALLBACK_CHARS = 1200
SUMMARY_BATCH_PROMPT = """请分别概括下面多个聊天块的主题和关键事项。
只输出 JSON 数组，不要 Markdown，不要解释。
数组长度应等于输入块数，每项格式为 {"id": 数字, "summary": "一句话摘要"}。
不要合并不同 id 的内容。聊天块 JSON：
"""
SUMMARY_RETRY_SHORT_ERRORS = {"UnprocessableEntityError", "BadRequestError"}
SUMMARY_SPLIT_BATCH_ERRORS = {"UnprocessableEntityError", "BadRequestError", "SummaryBatchParseError"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def positive_int(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError("必须是 >= 1 的整数")
    return value


def non_negative_int(raw: str) -> int:
    value = int(raw)
    if value < 0:
        raise argparse.ArgumentTypeError("必须是 >= 0 的整数")
    return value


def positive_float(raw: str) -> float:
    value = float(raw)
    if value <= 0:
        raise argparse.ArgumentTypeError("必须是 > 0 的数字")
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
    parser.add_argument("--summary-workers", type=positive_int, default=2, help="摘要生成并发数，默认 2")
    parser.add_argument(
        "--summary-batch-size",
        type=positive_int,
        default=max(1, env_int("SUMMARY_BATCH_SIZE", DEFAULT_SUMMARY_BATCH_SIZE)),
        help="每个摘要请求包含的会话块数量，默认 4；也可用 SUMMARY_BATCH_SIZE 配置",
    )
    parser.add_argument("--embed-workers", type=positive_int, default=4, help="embedding 批次并发数，默认 4")
    parser.add_argument("--embed-batch-size", type=positive_int, default=32, help="每个 embedding 请求批次的会话块数量，默认 32")
    parser.add_argument(
        "--summary-max-chars",
        type=positive_int,
        default=max(1, env_int("SUMMARY_MAX_CHARS", DEFAULT_SUMMARY_MAX_CHARS)),
        help="每块摘要最多发送的字符数，默认 3000；也可用 SUMMARY_MAX_CHARS 配置",
    )
    parser.add_argument(
        "--summary-fallback-chars",
        type=non_negative_int,
        default=max(0, env_int("SUMMARY_FALLBACK_CHARS", DEFAULT_SUMMARY_FALLBACK_CHARS)),
        help="摘要遇到 422/BadRequest 时改用更短文本重试；0 表示禁用，默认 1200",
    )
    parser.add_argument(
        "--progress-every",
        type=positive_int,
        default=max(1, env_int("PROGRESS_EVERY", DEFAULT_PROGRESS_EVERY)),
        help="每处理多少个块输出一次进度，默认 50；也可用 PROGRESS_EVERY 配置",
    )
    parser.add_argument(
        "--progress-interval",
        type=positive_float,
        default=max(0.1, env_float("PROGRESS_INTERVAL", DEFAULT_PROGRESS_INTERVAL)),
        help="embedding 无完成批次时的等待提示间隔秒数，默认 15；也可用 PROGRESS_INTERVAL 配置",
    )
    parser.add_argument(
        "--keep-going",
        action="store_true",
        help="摘要或 embedding 单批失败时继续处理其余批次；默认遇到模型/API错误立即停止",
    )
    return parser.parse_args()


def compact_error(exc: Exception, limit: int = 300) -> str:
    detail = str(exc).strip().replace("\r", " ").replace("\n", " ")
    if not detail:
        return type(exc).__name__
    return detail[:limit]


def format_error_counts(errors: dict[str, int]) -> str:
    return "，".join(f"{name}×{count}" for name, count in sorted(errors.items()))


class SummaryBatchParseError(ValueError):
    pass


def merge_counts(target: dict[str, int], source: dict[str, int]) -> None:
    for name, count in source.items():
        target[name] = target.get(name, 0) + count


def merge_examples(target: dict[str, str], source: dict[str, str]) -> None:
    for name, example in source.items():
        target.setdefault(name, example)


def extract_json(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].lstrip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()

    array_start = stripped.find("[")
    array_end = stripped.rfind("]")
    if array_start >= 0 and array_end > array_start:
        return stripped[array_start : array_end + 1]

    object_start = stripped.find("{")
    object_end = stripped.rfind("}")
    if object_start >= 0 and object_end > object_start:
        return stripped[object_start : object_end + 1]

    raise SummaryBatchParseError("摘要批量响应中没有可解析的 JSON")


def parse_summary_batch_response(raw: str, expected_ids: list[int]) -> dict[int, str]:
    try:
        data = json.loads(extract_json(raw))
    except Exception as exc:
        raise SummaryBatchParseError(f"摘要批量响应不是合法 JSON：{compact_error(exc)}") from exc

    result: dict[int, str] = {}
    if isinstance(data, dict):
        if isinstance(data.get("summaries"), list):
            data = data["summaries"]
        else:
            for expected_id in expected_ids:
                value = data.get(str(expected_id), data.get(expected_id))
                if isinstance(value, str) and value.strip():
                    result[expected_id] = value.strip()
            if result:
                return result
            raise SummaryBatchParseError("摘要批量响应 JSON 缺少 summaries 或 id 映射")

    if not isinstance(data, list):
        raise SummaryBatchParseError("摘要批量响应 JSON 顶层不是数组")

    if all(isinstance(item, str) for item in data):
        if len(data) != len(expected_ids):
            raise SummaryBatchParseError(f"摘要数组长度不匹配：期望 {len(expected_ids)}，实际 {len(data)}")
        return {
            expected_id: str(summary).strip()
            for expected_id, summary in zip(expected_ids, data)
            if str(summary).strip()
        }

    for item in data:
        if not isinstance(item, dict):
            continue
        raw_id = item.get("id", item.get("index", item.get("chunk_id")))
        summary = item.get("summary", item.get("摘要"))
        try:
            summary_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        if isinstance(summary, str) and summary.strip():
            result[summary_id] = summary.strip()

    if not result:
        raise SummaryBatchParseError("摘要批量响应没有解析到任何 summary")
    return result


def summarize_once(model_name: str, text: str) -> str | None:
    res = chat_model(model_name).invoke(SUMMARY_PROMPT + text)
    summary = str(res.content).strip()
    return summary or None


def summarize_batch_once(
    model_name: str,
    items: list[tuple[int, Chunk]],
    max_chars: int,
) -> dict[int, str]:
    payload = [
        {"id": local_id, "text": chunk.text[:max_chars]}
        for local_id, (_, chunk) in enumerate(items, start=1)
    ]
    res = chat_model(model_name).invoke(SUMMARY_BATCH_PROMPT + json.dumps(payload, ensure_ascii=False))
    expected_ids = list(range(1, len(items) + 1))
    return parse_summary_batch_response(str(res.content), expected_ids)


def summarize_chunk(
    model_name: str,
    chunk: Chunk,
    max_chars: int,
    fallback_chars: int,
) -> tuple[str | None, str | None, str | None]:
    try:
        return summarize_once(model_name, chunk.text[:max_chars]), None, None
    except Exception as exc:
        error_name = type(exc).__name__
        can_retry_short = (
            error_name in SUMMARY_RETRY_SHORT_ERRORS
            and fallback_chars > 0
            and fallback_chars < max_chars
        )
        if can_retry_short:
            try:
                return summarize_once(model_name, chunk.text[:fallback_chars]), None, None
            except Exception as retry_exc:
                return None, type(retry_exc).__name__, compact_error(retry_exc)
        return None, error_name, compact_error(exc)


def summarize_batch(
    model_name: str,
    items: list[tuple[int, Chunk]],
    max_chars: int,
    fallback_chars: int,
) -> tuple[dict[int, str], dict[str, int], dict[str, str]]:
    if not items:
        return {}, {}, {}
    if len(items) == 1:
        index, chunk = items[0]
        summary, error, detail = summarize_chunk(model_name, chunk, max_chars, fallback_chars)
        if summary:
            return {index: summary}, {}, {}
        return {}, ({error: 1} if error else {}), ({error: detail} if error and detail else {})

    try:
        local_summaries = summarize_batch_once(model_name, items, max_chars)
    except Exception as exc:
        last_exc = exc
        error_name = type(exc).__name__
        if error_name in SUMMARY_RETRY_SHORT_ERRORS and 0 < fallback_chars < max_chars:
            try:
                local_summaries = summarize_batch_once(model_name, items, fallback_chars)
                last_exc = None
            except Exception as retry_exc:
                last_exc = retry_exc

        if last_exc is not None:
            last_name = type(last_exc).__name__
            if last_name in SUMMARY_SPLIT_BATCH_ERRORS:
                midpoint = len(items) // 2
                left_summaries, left_errors, left_examples = summarize_batch(
                    model_name, items[:midpoint], max_chars, fallback_chars
                )
                right_summaries, right_errors, right_examples = summarize_batch(
                    model_name, items[midpoint:], max_chars, fallback_chars
                )
                merge_counts(left_errors, right_errors)
                merge_examples(left_examples, right_examples)
                return {**left_summaries, **right_summaries}, left_errors, left_examples
            return {}, {last_name: len(items)}, {last_name: compact_error(last_exc)}

    summaries: dict[int, str] = {}
    missing: list[tuple[int, Chunk]] = []
    for local_id, item in enumerate(items, start=1):
        original_index, _ = item
        summary = local_summaries.get(local_id)
        if summary:
            summaries[original_index] = summary
        else:
            missing.append(item)

    errors: dict[str, int] = {}
    examples: dict[str, str] = {}
    if missing:
        missing_summaries, missing_errors, missing_examples = summarize_batch(
            model_name, missing, max_chars, fallback_chars
        )
        summaries.update(missing_summaries)
        merge_counts(errors, missing_errors)
        merge_examples(examples, missing_examples)
    return summaries, errors, examples


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
    summary_buffer: list[tuple[int, str]] = []
    embed_fail_sessions = 0
    embedded = 0
    summary_batches = [
        to_summarize[start : start + args.summary_batch_size]
        for start in range(0, len(to_summarize), args.summary_batch_size)
    ]
    total_embed_batches = (len(to_embed_set) + batch_size - 1) // batch_size if to_embed_set else 0
    submitted_embed_batches = 0
    submitted_embed_sessions = 0
    completed_embed_batches = 0
    reported_submitted_sessions = 0
    reported_finished_sessions = 0

    if to_summarize:
        retry_note = (
            f"，422/BadRequest 短文本重试 {args.summary_fallback_chars} 字"
            if args.summary_fallback_chars
            else ""
        )
        print(
            f"生成摘要前缀（{summary_model}，{len(to_summarize)} 块，预计 {len(summary_batches)} 批，"
            f"批 {args.summary_batch_size}，并发 {args.summary_workers}，最多 {args.summary_max_chars} 字{retry_note}）..."
        )
    if to_embed_set:
        print(
            f"生成 embedding（{os.getenv('EMBED_MODEL')}，{len(to_embed_set)} 块，"
            f"预计 {total_embed_batches} 批，批 {batch_size}，并发 {args.embed_workers}）..."
        )

    def abort_model_error(stage: str, errors: dict[str, int], examples: dict[str, str]) -> None:
        store.set_summaries(summary_buffer)
        summary_buffer.clear()
        print(f"\n[错误] {stage} 失败，已停止 ingest（{format_error_counts(errors)}）。")
        for name, example in list(sorted(examples.items()))[:3]:
            print(f"  {name}: {example}")
        print("修复模型/API 配置后重跑 ingest，会自动续补缺失部分。")
        raise SystemExit(1)

    def submit_summary_batch(
        pool: ThreadPoolExecutor,
        running: dict[Future, list[int]],
        indices: list[int],
    ) -> None:
        running[
            pool.submit(
                summarize_batch,
                summary_model,
                [(i, chunks[i]) for i in indices],
                args.summary_max_chars,
                args.summary_fallback_chars,
            )
        ] = indices

    if to_summarize:
        summary_pool = ThreadPoolExecutor(max_workers=args.summary_workers)
        running_summaries: dict[Future, list[int]] = {}
        summary_batch_pos = 0
        done = 0
        generated = 0
        reported_summary_done = 0
        summary_errors: dict[str, int] = {}
        summary_error_examples: dict[str, str] = {}

        try:
            while summary_batch_pos < len(summary_batches) and len(running_summaries) < args.summary_workers:
                submit_summary_batch(summary_pool, running_summaries, summary_batches[summary_batch_pos])
                summary_batch_pos += 1

            while running_summaries:
                done_futures, _ = wait(set(running_summaries), return_when=FIRST_COMPLETED)
                for future in done_futures:
                    indices = running_summaries.pop(future)
                    try:
                        batch_summaries, batch_errors, batch_examples = future.result()
                    except Exception as exc:
                        batch_summaries = {}
                        batch_errors = {type(exc).__name__: len(indices)}
                        batch_examples = {type(exc).__name__: compact_error(exc)}

                    for index, summary in batch_summaries.items():
                        session_id = session_ids[index]
                        summaries[session_id] = summary
                        summary_buffer.append((session_id, summary))
                        if len(summary_buffer) >= SUMMARY_FLUSH_EVERY:
                            store.set_summaries(summary_buffer)
                            summary_buffer.clear()
                    generated += len(batch_summaries)

                    if batch_errors and not args.keep_going:
                        summary_pool.shutdown(wait=False, cancel_futures=True)
                        abort_model_error("摘要生成", batch_errors, batch_examples)

                    merge_counts(summary_errors, batch_errors)
                    merge_examples(summary_error_examples, batch_examples)

                    done += len(indices)
                    if done - reported_summary_done >= args.progress_every or done == len(to_summarize):
                        print(f"  摘要 {done}/{len(to_summarize)}")
                        reported_summary_done = done

                    if summary_batch_pos < len(summary_batches):
                        submit_summary_batch(summary_pool, running_summaries, summary_batches[summary_batch_pos])
                        summary_batch_pos += 1
        finally:
            summary_pool.shutdown(wait=False, cancel_futures=True)

        store.set_summaries(summary_buffer)
        summary_buffer.clear()
        print(f"摘要完成：本次生成 {generated}/{len(to_summarize)}，全库 {len(summaries)}/{len(chunks)}")
        if summary_errors:
            print(f"  [警告] {sum(summary_errors.values())} 条摘要请求失败（{format_error_counts(summary_errors)}）；重跑 ingest 将自动补齐")
            for name, example in list(sorted(summary_error_examples.items()))[:3]:
                print(f"    {name}: {example}")

    embed_batches = [
        sorted(to_embed_set)[start : start + batch_size]
        for start in range(0, len(to_embed_set), batch_size)
    ]

    def submit_embed_batch(
        pool: ThreadPoolExecutor,
        running: dict[Future, list[int]],
        indices: list[int],
    ) -> None:
        nonlocal submitted_embed_batches, submitted_embed_sessions, reported_submitted_sessions
        inputs: list[str] = []
        for i in indices:
            summary = summaries.get(session_ids[i])
            inputs.append(f"{summary}\n{chunks[i].text}" if summary else chunks[i].text)
        running[pool.submit(embed, inputs)] = list(indices)
        submitted_embed_batches += 1
        submitted_embed_sessions += len(indices)
        should_report = (
            submitted_embed_sessions - reported_submitted_sessions >= args.progress_every
            or submitted_embed_batches == total_embed_batches
        )
        if should_report:
            print(
                f"  embedding 已提交 {submitted_embed_sessions}/{len(to_embed_set)} 块"
                f"（{submitted_embed_batches}/{total_embed_batches} 批）"
            )
            reported_submitted_sessions = submitted_embed_sessions

    def finish_embed_future(future: Future, indices: list[int]) -> None:
        nonlocal embedded, embed_fail_sessions, completed_embed_batches, reported_finished_sessions
        completed_embed_batches += 1
        try:
            vectors = future.result()
            if len(vectors) != len(indices):
                raise RuntimeError(f"embedding 返回数量不一致：请求 {len(indices)} 条，返回 {len(vectors)} 条")
        except Exception as exc:
            embed_fail_sessions += len(indices)
            errors = {type(exc).__name__: len(indices)}
            examples = {type(exc).__name__: compact_error(exc)}
            if not args.keep_going:
                abort_model_error("embedding 生成", errors, examples)
            print(f"\n[警告] 一批 embedding 生成失败（{len(indices)} 块）：{compact_error(exc)}")
            return
        if vectors:
            actual_dim = len(vectors[0])
            recreated = store.ensure_vector_table_dimension(actual_dim)
            if recreated:
                print(f"\n检测到 embedding 实际维度为 {actual_dim}，已按该维度重建 sessions_vec 向量表。")
        try:
            store.insert_embeddings(
                [
                    {"session_id": session_ids[i], "embedding": vector}
                    for i, vector in zip(indices, vectors)
                ]
            )
        except Exception as exc:
            embed_fail_sessions += len(indices)
            errors = {type(exc).__name__: len(indices)}
            examples = {type(exc).__name__: compact_error(exc)}
            if not args.keep_going:
                abort_model_error("embedding 写入", errors, examples)
            print(f"\n[警告] 一批 embedding 写入失败（{len(indices)} 块）：{compact_error(exc)}")
            return
        embedded += len(vectors)
        finished = embedded + embed_fail_sessions
        should_report = (
            finished - reported_finished_sessions >= args.progress_every
            or completed_embed_batches == total_embed_batches
        )
        if should_report:
            print(
                f"  embedding 完成 {finished}/{len(to_embed_set)} 块"
                f"（成功 {embedded}，失败 {embed_fail_sessions}；批次 {completed_embed_batches}/{total_embed_batches}）"
            )
            reported_finished_sessions = finished

    if embed_batches:
        embed_pool = ThreadPoolExecutor(max_workers=args.embed_workers)
        running_embeds: dict[Future, list[int]] = {}
        embed_batch_pos = 0
        try:
            while embed_batch_pos < len(embed_batches) and len(running_embeds) < args.embed_workers:
                submit_embed_batch(embed_pool, running_embeds, embed_batches[embed_batch_pos])
                embed_batch_pos += 1

            while running_embeds:
                done_futures, _ = wait(
                    set(running_embeds),
                    timeout=args.progress_interval,
                    return_when=FIRST_COMPLETED,
                )
                if not done_futures:
                    finished = embedded + embed_fail_sessions
                    print(
                        f"  embedding 等待中：完成 {finished}/{len(to_embed_set)} 块，"
                        f"已提交 {submitted_embed_sessions}/{len(to_embed_set)} 块，"
                        f"{len(running_embeds)} 批未返回"
                    )
                    continue
                for future in done_futures:
                    indices = running_embeds.pop(future)
                    finish_embed_future(future, indices)
                    if embed_batch_pos < len(embed_batches):
                        submit_embed_batch(embed_pool, running_embeds, embed_batches[embed_batch_pos])
                        embed_batch_pos += 1
        finally:
            embed_pool.shutdown(wait=False, cancel_futures=True)

    record_pending_files(pending_file_records)

    if embed_fail_sessions:
        print(f"\n[警告] {embed_fail_sessions} 块向量生成失败；消息库与其余索引已保留，重跑 ingest 将只补建缺失部分。")
    if to_embed_set:
        print(f"\n向量索引完成（本次写入 {embedded} 块，失败 {embed_fail_sessions} 块）。现在可以运行 python -m wechat_rag_agent.cli")
    else:
        print("\n索引更新完成。现在可以运行 python -m wechat_rag_agent.cli")


if __name__ == "__main__":
    main()
