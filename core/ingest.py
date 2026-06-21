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
from .llm import embed, embed_configured, invoke_summary, summary_config_status
from .parser import file_scope_for_path, is_weflow_export, parse_weflow
from .redaction import redact_text


load_dotenv()

SUMMARY_PROMPT = "用一句话概括这段聊天讨论的主题和关键事项，直接输出概括，不要任何前缀。聊天内容：\n\n"
SUMMARY_FLUSH_EVERY = 50
DEFAULT_PROGRESS_EVERY = 50
DEFAULT_PROGRESS_INTERVAL = 15.0
DEFAULT_SUMMARY_WORKERS = 2
DEFAULT_SUMMARY_BATCH_SIZE = 4
DEFAULT_SUMMARY_MAX_CHARS = 3000
DEFAULT_SUMMARY_FALLBACK_CHARS = 1200
DEFAULT_EMBED_WORKERS = 4
DEFAULT_EMBED_BATCH_SIZE = 32
PROGRESS_PREFIX = "__INGEST_PROGRESS__ "
SUMMARY_BATCH_PROMPT = """请分别概括下面多个聊天块的主题和关键事项。
只输出 JSON 数组，不要 Markdown，不要解释。
数组长度应等于输入块数，每项格式为 {"id": 数字, "summary": "一句话摘要"}。
不要合并不同 id 的内容。聊天块 JSON：
"""
SUMMARY_RETRY_SHORT_ERRORS = {"UnprocessableEntityError", "BadRequestError"}
SUMMARY_SPLIT_BATCH_ERRORS = {"UnprocessableEntityError", "BadRequestError", "SummaryBatchParseError"}
ENV_TRUE_VALUES = {"1", "true", "yes", "y", "on"}
ENV_FALSE_VALUES = {"0", "false", "no", "n", "off"}


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


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    normalized = raw.strip().lower()
    if normalized in ENV_TRUE_VALUES:
        return True
    if normalized in ENV_FALSE_VALUES:
        return False
    return default


def emit_progress(stage: str, progress: int, message: str = "") -> None:
    if not env_bool("INGEST_PROGRESS_JSON", False):
        return
    payload = {
        "stage": stage,
        "progress": max(0, min(int(progress), 100)),
        "message": message,
    }
    print(f"{PROGRESS_PREFIX}{json.dumps(payload, ensure_ascii=False)}", flush=True)


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
        return [target] if target.suffix.lower() == ".json" else []

    try:
        root = target.resolve()
    except OSError:
        return []

    files: list[Path] = []
    for path in target.rglob("*"):
        try:
            if not path.is_file() or path.suffix.lower() != ".json":
                continue
            path.resolve().relative_to(root)
        except (OSError, ValueError):
            continue
        files.append(path)

    return sorted(files, key=lambda path: path.as_posix().lower())


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="导入 WeFlow 微信聊天 JSON，并构建 SQLite/FTS/向量索引。")
    parser.add_argument("targets", nargs="+", help="JSON 文件或目录，可传多个")
    parser.add_argument("--no-summary", action="store_true", help="跳过索引期摘要前缀")
    parser.add_argument("--force-import", action="store_true", help="即使文件未变化，也重新解析 JSON 并核对消息入库")
    parser.add_argument("--force-rebuild", action="store_true", help="即使本次新增消息为 0，也强制重建 FTS、会话分块、摘要和向量索引")
    parser.add_argument("--force-fts", action="store_true", help="强制重建 FTS 全文索引")
    parser.add_argument("--force-chunks", action="store_true", help="强制重建会话分块（内容未变的块仍会复用已有摘要和向量）")
    parser.add_argument("--force-summary", action="store_true", help="强制重新生成所有摘要")
    parser.add_argument("--skip-import", action="store_true", help="跳过 JSON 解析与消息入库，仅基于现有数据库执行索引/摘要/向量构建")
    parser.add_argument(
        "--force-embeddings",
        "--force-vector",
        "--force-vectors",
        action="store_true",
        dest="force_embeddings",
        help="强制重建向量索引",
    )
    parser.add_argument(
        "--summary-workers",
        type=positive_int,
        default=max(1, env_int("SUMMARY_WORKERS", DEFAULT_SUMMARY_WORKERS)),
        help="摘要生成并发数，默认 2；也可用 SUMMARY_WORKERS 配置",
    )
    parser.add_argument(
        "--summary-batch-size",
        type=positive_int,
        default=max(1, env_int("SUMMARY_BATCH_SIZE", DEFAULT_SUMMARY_BATCH_SIZE)),
        help="每个摘要请求包含的会话块数量，默认 4；也可用 SUMMARY_BATCH_SIZE 配置",
    )
    parser.add_argument(
        "--embed-workers",
        type=positive_int,
        default=max(1, env_int("EMBED_WORKERS", DEFAULT_EMBED_WORKERS)),
        help="embedding 批次并发数，默认 4；也可用 EMBED_WORKERS 配置",
    )
    parser.add_argument(
        "--embed-batch-size",
        type=positive_int,
        default=max(1, env_int("EMBED_BATCH_SIZE", DEFAULT_EMBED_BATCH_SIZE)),
        help="每个 embedding 请求批次的会话块数量，默认 32；也可用 EMBED_BATCH_SIZE 配置",
    )
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
        default=env_bool("INGEST_KEEP_GOING", False),
        help="摘要或 embedding 单批失败时继续处理其余批次；也可用 INGEST_KEEP_GOING=true 配置",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_false",
        dest="keep_going",
        help="即使 INGEST_KEEP_GOING=true，也在模型/API错误时立即停止",
    )
    args = parser.parse_args()
    if args.summary_fallback_chars >= args.summary_max_chars:
        args.summary_fallback_chars = max(0, args.summary_max_chars // 2)
    return args


def compact_error(exc: Exception, limit: int = 300) -> str:
    detail = redact_text(exc, limit=limit)
    return detail or type(exc).__name__


def format_error_counts(errors: dict[str, int]) -> str:
    return "，".join(f"{name}×{count}" for name, count in sorted(errors.items()))


def summary_config_reason(summary_model: str, status: dict[str, object]) -> str:
    if not summary_model:
        return "未配置 SUMMARY_MODEL"
    raw_missing = status.get("missing", [])
    missing_names = raw_missing if isinstance(raw_missing, list) else []
    missing = [str(name) for name in missing_names]
    if missing:
        return "未配置 " + "、".join(missing)
    return "摘要模型不可用"


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
    res = invoke_summary(SUMMARY_PROMPT + text, model_name)
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
    res = invoke_summary(SUMMARY_BATCH_PROMPT + json.dumps(payload, ensure_ascii=False), model_name)
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


def file_key_for(file: Path) -> str:
    return str(file.resolve())


def record_file_message_sources(file_key: str, message_ids: list[str]) -> None:
    existing_ids = store.resolve_existing_message_ids(message_ids)
    store.record_ingest_file_messages(file_key, existing_ids)


def index_scope_for_files(files: list[Path]) -> tuple[list[str], list[int]]:
    file_keys = [file_key_for(file) for file in files]
    prefixes = [f"{file_scope_for_path(file)}:" for file in files]
    threads = {
        *store.get_threads_for_ingest_file_paths(file_keys),
        *store.get_threads_for_message_id_prefixes(prefixes),
    }
    session_ids = {
        *store.get_session_ids_for_ingest_file_paths(file_keys),
        *store.get_session_ids_for_message_id_prefixes(prefixes),
    }
    return sorted(threads), sorted(session_ids)


def validate_skip_import_scope(
    *,
    threads: list[str],
    session_ids: list[int],
    force_fts: bool,
    force_chunks: bool,
    force_summary: bool,
    force_embeddings: bool,
) -> None:
    needs_message_scope = force_fts or force_chunks
    needs_session_scope = (force_summary or force_embeddings) and not force_chunks
    if not any([force_fts, force_chunks, force_summary, force_embeddings]):
        if threads or session_ids:
            return
        emit_progress("error", 0, "没有找到目标 JSON 的已入库索引范围")
        print("\n[错误] 目标 JSON 尚无可定位的已入库消息或会话块。请先执行增量导入、全流程导入或强制重建。")
        raise SystemExit(1)
    if needs_message_scope and not threads:
        emit_progress("error", 0, "没有找到目标 JSON 的已入库消息范围")
        print("\n[错误] 目标 JSON 尚无可定位的已入库消息范围，无法执行 FTS 或分块构建。请先执行增量导入、全流程导入或强制重建。")
        raise SystemExit(1)
    if needs_session_scope and not session_ids:
        emit_progress("error", 0, "没有找到目标 JSON 的已有会话分块")
        print("\n[错误] 目标 JSON 尚无已有会话分块，无法执行摘要或向量构建。请先执行仅分块、全流程导入或强制重建。")
        raise SystemExit(1)


def load_existing_chunks(session_ids_filter: list[int] | None = None) -> tuple[list[Chunk], list[int], dict[int, str]]:
    rows = store.get_sessions(session_ids_filter) if session_ids_filter is not None else store.get_all_sessions()
    cleaned_summaries = {
        int(row["session_id"]): str(row["summary"] or "").strip()
        for row in rows
        if str(row["summary"] or "").strip()
    }
    chunks = [
        Chunk(
            thread=row["thread"],
            start_time=row["start_time"],
            end_time=row["end_time"],
            participants=row["participants"],
            msg_ids=row["msg_ids"],
            text=row["text"],
            summary=cleaned_summaries.get(int(row["session_id"])),
        )
        for row in rows
    ]
    session_ids = [int(row["session_id"]) for row in rows]
    return chunks, session_ids, cleaned_summaries


def ingest_files(
    files: list[Path],
    pending_file_records: list[tuple[str, int, int, int, int, int]],
    force_import: bool = False,
) -> tuple[int, int, int, set[str], int, int]:
    total_included = 0
    total_inserted = 0
    total_updated = 0
    affected_threads: set[str] = set()
    usable_files = 0
    failed_files = 0
    total_files = len(files)
    for file_index, file in enumerate(files, start=1):
        file_label = file.name
        start_progress = 10 + int(((file_index - 1) / max(total_files, 1)) * 14)
        finish_progress = 10 + int((file_index / max(total_files, 1)) * 14)
        emit_progress("parsing", start_progress, f"解析 {file_index}/{total_files}: {file_label}")
        try:
            stat = file.stat()
        except OSError as exc:
            failed_files += 1
            print(f"  x {file}: 文件状态读取失败（{compact_error(exc)}）")
            emit_progress("parsing", finish_progress, f"文件读取失败 {file_index}/{total_files}: {file_label}")
            continue
        file_key = file_key_for(file)
        unchanged = store.ingest_file_unchanged(file_key, stat.st_size, stat.st_mtime_ns)
        has_source_mapping = store.ingest_file_message_mapping_exists(file_key)
        if unchanged and has_source_mapping and not force_import:
            usable_files += 1
            print(f"  skip {file}: 文件未变化，跳过解析")
            emit_progress("parsing", finish_progress, f"跳过未变化文件 {file_index}/{total_files}: {file_label}")
            continue
        if unchanged and not force_import:
            print(f"  map {file}: 文件未变化，补建文件来源映射")
        elif unchanged and force_import:
            print(f"  reparse {file}: 强制重建，重新解析未变化文件")

        try:
            data = json.loads(file.read_bytes())
        except Exception as exc:
            failed_files += 1
            print(f"  x {file}: 文件读取或 JSON 解析失败（{compact_error(exc)}）")
            emit_progress("parsing", finish_progress, f"文件读取或 JSON 解析失败 {file_index}/{total_files}: {file_label}")
            continue

        if not is_weflow_export(data):
            failed_files += 1
            print(f"  x {file}: 非 WeFlow 导出格式（缺少顶层 weflow 键），跳过")
            emit_progress("parsing", finish_progress, f"跳过非 WeFlow 文件 {file_index}/{total_files}: {file_label}")
            continue

        try:
            result = parse_weflow(data, file)
        except Exception as exc:
            failed_files += 1
            print(f"  x {file}: WeFlow 内容解析失败（{compact_error(exc)}）")
            emit_progress("parsing", finish_progress, f"WeFlow 内容解析失败 {file_index}/{total_files}: {file_label}")
            continue

        write_progress = start_progress
        if finish_progress > start_progress:
            write_progress = start_progress + max(1, (finish_progress - start_progress) // 2)
        emit_progress("indexing", write_progress, f"写入消息库 {file_index}/{total_files}: {file_label}")
        usable_files += 1
        write_result = store.upsert_messages(result.messages)
        emit_progress("indexing", write_progress, f"记录文件来源 {file_index}/{total_files}: {file_label}")
        record_file_message_sources(file_key, [message.id for message in result.messages])
        inserted = write_result.inserted
        updated = write_result.updated
        total_included += result.included
        total_inserted += inserted
        total_updated += updated
        if write_result.changed > 0:
            affected_threads.update(write_result.threads or {result.thread})
        record = (file_key, stat.st_size, stat.st_mtime_ns, result.total, result.included, write_result.changed)
        if write_result.changed == 0:
            store.record_ingest_file(*record)
        else:
            pending_file_records.append(record)
        skipped = " ".join(f"{name}×{count}" for name, count in result.skipped_by_type.items())
        suffix = f"，跳过: {skipped}" if skipped else ""
        update_suffix = f"，更新 {updated}" if updated else ""
        print(f"  ok [{result.thread}] 总 {result.total} 条，入库 {result.included} 条（新增 {inserted}{update_suffix}）{suffix}")
        emit_progress(
            "parsing",
            finish_progress,
            f"已解析 {file_index}/{total_files}: {file_label}，入库 {result.included} 条",
        )
    return total_included, total_inserted, total_updated, affected_threads, usable_files, failed_files


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
        old_summary_text = str(old_summary or "").strip()
        if old_summary_text and not force_summary:
            carried_summaries.append((session_id, old_summary_text))
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
    emit_progress("starting", 3, "准备导入任务")
    force_fts = args.force_rebuild or args.force_fts
    force_chunks = args.force_rebuild or args.force_chunks
    force_summary = args.force_rebuild or args.force_summary
    force_embeddings = args.force_rebuild or args.force_embeddings
    explicit_index_mode = (
        args.skip_import
        and not args.force_rebuild
        and any([args.force_fts, args.force_chunks, args.force_summary, args.force_embeddings])
    )
    explicit_summary_only = args.skip_import and args.force_summary and not args.force_rebuild
    explicit_embeddings_only = args.skip_import and args.force_embeddings and not args.force_rebuild

    files: list[Path] = []
    for raw in args.targets:
        target = Path(raw)
        if not target.exists():
            print(f"路径不存在，跳过：{target}")
            continue
        files.extend(collect_json_files(target))

    target_file_keys: list[str] = [str(file.resolve()) for file in files]
    target_message_prefixes: list[str] = [f"{file_scope_for_path(file)}:" for file in files]
    index_scope_threads: list[str] | None = None
    index_scope_session_ids: list[int] | None = None
    if target_message_prefixes:
        index_scope_threads, index_scope_session_ids = index_scope_for_files(files)
        if args.skip_import:
            print(
                "索引范围：目标 JSON 关联 "
                f"{len(index_scope_threads)} 个会话、{len(index_scope_session_ids)} 个会话块"
            )

    pending_file_records: list[tuple[str, int, int, int, int, int]] = []
    if args.skip_import:
        print(f"跳过 JSON 解析与入库（已选择 --skip-import；目标路径匹配 {len(files)} 个 JSON 文件）")
        emit_progress("indexing", 20, "跳过 JSON 解析，准备构建索引")
        if not files:
            emit_progress("error", 0, "没有找到可构建索引的 JSON 文件")
            print("\n[错误] 未找到可构建索引的 JSON 文件。单项索引构建需要明确的 WeFlow JSON 目标，避免误重建全库。")
            raise SystemExit(1)
        validate_skip_import_scope(
            threads=index_scope_threads or [],
            session_ids=index_scope_session_ids or [],
            force_fts=force_fts,
            force_chunks=force_chunks,
            force_summary=force_summary,
            force_embeddings=force_embeddings,
        )
        total_included = 0
        total_inserted = 0
        total_updated = 0
        affected_threads: set[str] = set()
        usable_files = 0
        failed_files = 0
    else:
        print(f"发现 {len(files)} 个 JSON 文件")
        emit_progress("parsing", 10, f"发现 {len(files)} 个 JSON 文件")
        if not files:
            emit_progress("error", 0, "没有找到 JSON 文件")
            print("\n[错误] 未找到可导入的 JSON 文件。请检查路径是否正确，或选择包含 WeFlow 导出的目录。")
            raise SystemExit(1)
        (
            total_included,
            total_inserted,
            total_updated,
            affected_threads,
            usable_files,
            failed_files,
        ) = ingest_files(files, pending_file_records, force_import=args.force_rebuild or args.force_import)
        if target_message_prefixes:
            index_scope_threads, index_scope_session_ids = index_scope_for_files(files)
        if failed_files and usable_files == 0:
            emit_progress("error", 0, "没有可导入的 WeFlow JSON 文件")
            print(f"\n[错误] {failed_files} 个 JSON 文件解析或格式校验失败，没有可导入的 WeFlow 聊天记录。")
            raise SystemExit(1)
        emit_progress("indexing", 25, "JSON 解析与入库完成")

    has_message_changes = (total_inserted + total_updated) > 0
    summary_model = (os.getenv("SUMMARY_MODEL") or "").strip()
    summary_status = summary_config_status(summary_model) if summary_model else {"configured": False, "missing": ["SUMMARY_MODEL"]}
    summary_ready = bool(summary_status["configured"]) and not args.no_summary
    embed_ready = embed_configured() and store.has_vec()

    # 自愈：普通增量/全流程即使没有新消息，也补齐上次中断/失败留下的缺失项。
    # 显式单项构建要严格按用户选择执行，避免“仅 FTS/仅向量”暗中调用模型。
    auto_repair_missing_indexes = not explicit_index_mode
    target_index_status = (
        store.get_session_index_status(index_scope_session_ids)
        if summary_ready and index_scope_session_ids is not None
        else None
    )
    missing_summaries = (
        int(target_index_status["missing_summary"])
        if summary_ready and target_index_status is not None
        else store.count_sessions_missing_summary()
        if summary_ready
        else 0
    )
    missing_embeddings = (
        len(store.get_session_ids_without_embedding(index_scope_session_ids))
        if embed_ready and index_scope_session_ids is not None
        else len(store.get_all_session_ids_without_embedding())
        if embed_ready
        else 0
    )
    missing_fts = (
        store.count_missing_fts_for_ingest_targets(target_file_keys, target_message_prefixes)
        if target_message_prefixes
        else store.count_missing_fts()
    )
    missing_seq = (
        store.count_messages_missing_seq_for_ingest_targets(target_file_keys, target_message_prefixes)
        if target_message_prefixes
        else store.count_messages_missing_seq()
    )

    should_touch_fts = (
        has_message_changes
        or force_fts
        or (auto_repair_missing_indexes and (missing_fts > 0 or missing_seq > 0))
    )
    should_rebuild_chunks = has_message_changes or force_chunks
    should_generate_summary = (
        has_message_changes
        or force_summary
        or (auto_repair_missing_indexes and missing_summaries > 0)
    ) and not args.no_summary
    should_build_embeddings = (
        has_message_changes
        or force_embeddings
        or (auto_repair_missing_indexes and missing_embeddings > 0)
    )

    if not any([should_touch_fts, should_rebuild_chunks, should_generate_summary, should_build_embeddings]):
        print("\n本次新增/更新消息为 0，摘要和向量索引完整，无需重建。")
        print("可按需加 --force-fts / --force-chunks / --force-summary / --force-embeddings。")
        emit_progress("completed", 100, "无需重建")
        return

    if missing_summaries and auto_repair_missing_indexes and not has_message_changes and not force_summary:
        print(f"\n检测到 {missing_summaries} 个会话块缺少摘要，本次将自动补齐")
    if missing_embeddings and auto_repair_missing_indexes and not has_message_changes and not force_embeddings:
        print(f"检测到 {missing_embeddings} 个会话块缺少向量，本次将自动补齐")
    if (missing_fts or missing_seq) and auto_repair_missing_indexes and not has_message_changes and not force_fts:
        print(f"检测到 FTS 索引缺失 {missing_fts} 条 / seq 缺失 {missing_seq} 条，本次将自动补齐")

    if force_fts:
        emit_progress("indexing", 30, "重建 FTS 索引")
        if target_message_prefixes and total_updated == 0:
            store.recompute_message_sequence(index_scope_threads)
            rebuilt = store.rebuild_fts_for_ingest_targets(target_file_keys, target_message_prefixes)
            print(
                f"\nFTS 索引已按目标 JSON 重建 {rebuilt} 条"
                f"（本次解析 {total_included} 条文本/引用消息，新增 {total_inserted} 条，更新 {total_updated} 条）"
            )
        else:
            store.recompute_message_sequence()
            store.rebuild_fts()
            print(
                f"\nFTS 索引已强制全量重建（本次解析 {total_included} 条文本/引用消息，"
                f"新增 {total_inserted} 条，更新 {total_updated} 条）"
            )
        emit_progress("indexing", 35, "FTS 索引完成")
    elif has_message_changes or missing_fts or missing_seq:
        emit_progress("indexing", 30, "同步 FTS 索引")
        sequence_scope = (
            index_scope_threads
            if missing_seq and not has_message_changes and target_message_prefixes
            else None
            if missing_seq and not has_message_changes
            else sorted(affected_threads)
        )
        store.recompute_message_sequence(sequence_scope)
        if total_updated:
            store.rebuild_fts()
            print(f"\n消息入库完成（新增 {total_inserted} 条，更新 {total_updated} 条），FTS 已全量刷新以同步修正内容")
        elif target_message_prefixes:
            synced = store.sync_missing_fts_for_ingest_targets(target_file_keys, target_message_prefixes)
            print(f"\n消息入库完成（新增 {total_inserted} 条，更新 {total_updated} 条），目标 JSON FTS 增量索引 {synced} 条")
        else:
            synced = store.sync_missing_fts()
            print(f"\n消息入库完成（新增 {total_inserted} 条，更新 {total_updated} 条），FTS 增量索引 {synced} 条")
        emit_progress("indexing", 35, "FTS 索引完成")
    else:
        print("\n消息无变化，跳过 FTS")
        emit_progress("indexing", 35, "跳过 FTS")

    chunks: list[Chunk] = []
    session_ids: list[int] = []
    summaries: dict[int, str] = {}

    if should_rebuild_chunks:
        scope = (
            index_scope_threads
            if index_scope_threads is not None and (args.skip_import or force_chunks)
            else (None if force_chunks else sorted(affected_threads))
        )
        emit_progress("chunking", 40, "重建会话分块")
        rebuild_chunks_scoped(scope, force_summary, force_embeddings)
        if target_message_prefixes:
            _index_threads, index_scope_session_ids = index_scope_for_files(files)
            if not args.skip_import and index_scope_session_ids is not None:
                print(f"索引范围：目标 JSON 关联 {len(index_scope_session_ids)} 个会话块")
        chunks, session_ids, summaries = load_existing_chunks(
            index_scope_session_ids if target_message_prefixes and index_scope_session_ids is not None else None
        )
        total_messages = store.stats()["total_messages"]
        avg = total_messages / max(len(chunks), 1)
        scope_label = "目标范围" if target_message_prefixes and index_scope_session_ids is not None else "全库"
        print(f"{scope_label}会话分块：{len(chunks)} 个块（全库平均 {avg:.1f} 条/块）")
        emit_progress("chunking", 45, f"会话分块完成：{len(chunks)} 个块")
    elif should_generate_summary or should_build_embeddings:
        chunks, session_ids, summaries = load_existing_chunks(
            index_scope_session_ids if target_message_prefixes and index_scope_session_ids is not None else None
        )
        if not chunks:
            print("没有可用会话分块；请先运行 --force-chunks。")
            record_pending_files(pending_file_records)
            if explicit_summary_only or explicit_embeddings_only:
                emit_progress("error", 0, "没有可用会话分块")
                raise SystemExit(1)
            emit_progress("completed", 100, "没有可用会话分块")
            return
        scope_label = "目标范围" if target_message_prefixes and index_scope_session_ids is not None else "已有"
        print(f"复用{scope_label}会话分块：{len(chunks)} 个块")
        emit_progress("chunking", 45, f"复用会话分块：{len(chunks)} 个块")
    else:
        print("跳过会话分块")

    # ---- 摘要 / 向量流水线：摘要完成的块立即进入 embedding 批队列，两阶段并行 ----
    summary_enabled = should_generate_summary and summary_ready
    if should_generate_summary and not summary_enabled:
        reason = "--no-summary" if args.no_summary else summary_config_reason(summary_model, summary_status)
        print(f"跳过摘要前缀（{reason}）")
        if explicit_summary_only:
            emit_progress("error", 0, f"摘要构建失败：{reason}")
            raise SystemExit(1)
    elif not should_generate_summary:
        print("跳过摘要前缀（未请求摘要重建）")

    embed_enabled = should_build_embeddings and embed_ready
    if should_build_embeddings and not embed_enabled:
        record_pending_files(pending_file_records)
        if not embed_configured():
            print("未配置 EMBED_*，跳过向量索引（semantic_search 将退化为全文检索）。配置 .env 后重跑 ingest 即可补建。")
            if explicit_embeddings_only:
                emit_progress("error", 0, "向量构建失败：未配置 EMBED_*")
                raise SystemExit(1)
        else:
            print("sqlite-vec 不可用，跳过向量索引。")
            if explicit_embeddings_only:
                emit_progress("error", 0, "向量构建失败：sqlite-vec 不可用")
                raise SystemExit(1)
        if not summary_enabled:
            emit_progress("completed", 100, "向量索引不可用，已完成可执行步骤")
            return

    to_summarize: list[int] = []
    if summary_enabled:
        if force_summary:
            to_summarize = list(range(len(chunks)))
        else:
            to_summarize = [i for i, sid in enumerate(session_ids) if sid not in summaries]
    to_summarize_set = set(to_summarize)

    # 新生成的摘要会改变 embedding 输入文本，这些块的向量需要连带重建
    if embed_ready and to_summarize and not embed_enabled and auto_repair_missing_indexes:
        embed_enabled = True

    to_embed_set: set[int] = set()
    if embed_enabled:
        have_vec = store.get_session_ids_with_embedding(session_ids)
        for i, sid in enumerate(session_ids):
            if force_embeddings or sid not in have_vec or i in to_summarize_set:
                to_embed_set.add(i)

    if summary_enabled and not to_summarize:
        print(f"摘要已是最新（{len(summaries)}/{len(chunks)} 块），跳过生成")
    if embed_enabled and not to_embed_set:
        print("向量索引已是最新，跳过 embedding")

    if not to_summarize and not to_embed_set:
        record_pending_files(pending_file_records)
        print("\n无需生成摘要或向量。现在可以运行 python -m core.cli")
        emit_progress("completed", 100, "无需生成摘要或向量")
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
        emit_progress("summary", 55, f"准备生成 {len(to_summarize)} 个摘要")
        retry_note = (
            f"，422/BadRequest 短文本重试 {args.summary_fallback_chars} 字"
            if args.summary_fallback_chars
            else ""
        )
        print(
            f"生成摘要前缀（{summary_model}，{len(to_summarize)} 块，预计 {len(summary_batches)} 批，"
            f"批 {args.summary_batch_size}，并发 {args.summary_workers}，最多 {args.summary_max_chars} 字{retry_note}）..."
        )
    if to_embed_set and not to_summarize:
        emit_progress("embedding", 75, f"准备生成 {len(to_embed_set)} 个向量")
    if to_embed_set:
        print(
            f"生成 embedding（{os.getenv('EMBED_MODEL')}，{len(to_embed_set)} 块，"
            f"预计 {total_embed_batches} 批，批 {batch_size}，并发 {args.embed_workers}）..."
        )

    def abort_model_error(stage: str, errors: dict[str, int], examples: dict[str, str]) -> None:
        store.set_summaries(list(summary_buffer))
        summary_buffer.clear()
        record_pending_files(pending_file_records)
        emit_progress("error", 0, f"{stage} 失败")
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
                            store.set_summaries(list(summary_buffer))
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
                        emit_progress(
                            "summary",
                            55 + int((done / max(len(to_summarize), 1)) * 15),
                            f"摘要 {done}/{len(to_summarize)}",
                        )
                        reported_summary_done = done

                    if summary_batch_pos < len(summary_batches):
                        submit_summary_batch(summary_pool, running_summaries, summary_batches[summary_batch_pos])
                        summary_batch_pos += 1
        finally:
            summary_pool.shutdown(wait=False, cancel_futures=True)

        store.set_summaries(list(summary_buffer))
        summary_buffer.clear()
        print(f"摘要完成：本次生成 {generated}/{len(to_summarize)}，全库 {len(summaries)}/{len(chunks)}")
        emit_progress("summary", 70, f"摘要完成：{generated}/{len(to_summarize)}")
        if summary_errors:
            print(f"  [警告] {sum(summary_errors.values())} 条摘要请求失败（{format_error_counts(summary_errors)}）；重跑 ingest 将自动补齐")
            for name, example in list(sorted(summary_error_examples.items()))[:3]:
                print(f"    {name}: {example}")

    if to_embed_set:
        emit_progress("embedding", 75, f"准备生成 {len(to_embed_set)} 个向量")

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
        running[pool.submit(embed, inputs, args.embed_batch_size)] = list(indices)
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
            emit_progress(
                "embedding",
                75 + int((finished / max(len(to_embed_set), 1)) * 20),
                f"embedding 完成 {finished}/{len(to_embed_set)}",
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
                    emit_progress(
                        "embedding",
                        75 + int((finished / max(len(to_embed_set), 1)) * 20),
                        f"embedding 等待中：完成 {finished}/{len(to_embed_set)}",
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
        print(f"\n向量索引完成（本次写入 {embedded} 块，失败 {embed_fail_sessions} 块）。现在可以运行 python -m core.cli")
    else:
        print("\n索引更新完成。现在可以运行 python -m core.cli")
    emit_progress("completed", 100, "导入完成")


if __name__ == "__main__":
    main()
