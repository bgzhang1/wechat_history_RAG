from __future__ import annotations

import array
import hashlib
import json
import os
import re
import sqlite3
import threading
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv

from .llm import EMBED_DIM
from .parser import PARSER_VERSION, NormMessage


load_dotenv()

SQL_BATCH = 900
MAX_QUERY_CHARS = 500
MAX_QUERY_TERMS = 8
MAX_FILTER_CHARS = 200
MAX_MESSAGE_ID_CHARS = 2048
API_MESSAGE_PREVIEW_CHARS = 200
CONTEXT_MESSAGE_CHARS = 2000


@dataclass(frozen=True)
class MessageWriteResult:
    inserted: int
    updated: int
    unchanged: int
    threads: frozenset[str]

    @property
    def changed(self) -> int:
        return self.inserted + self.updated


def chunk_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _batched(items: list[Any], size: int | None = None) -> Iterable[list[Any]]:
    batch_size = size or SQL_BATCH
    for start in range(0, len(items), batch_size):
        yield items[start : start + batch_size]


def _safe_int(value: Any, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    if value is None or value == "":
        result = default
    else:
        try:
            result = int(value)
        except (TypeError, ValueError, OverflowError):
            result = default
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _legacy_message_id_candidates(message_id: str) -> list[str]:
    prefix, suffix = _split_scoped_message_id(message_id)
    if not prefix or not suffix:
        return []

    candidates: list[str] = []
    if prefix.lower().endswith(".json"):
        basename_scoped = f"{Path(prefix).name}:{suffix}"
        if basename_scoped != message_id:
            candidates.append(basename_scoped)
    candidates.append(suffix)
    return list(dict.fromkeys(candidates))


def _looks_like_scoped_prefix(prefix: str) -> bool:
    normalized = prefix.replace("\\", "/").lower()
    return bool(normalized.endswith(".json") or normalized.startswith("uploads/"))


def _split_scoped_message_id(message_id: str) -> tuple[str, str]:
    prefix, separator, suffix = str(message_id or "").partition(":")
    if separator and suffix and _looks_like_scoped_prefix(prefix):
        return prefix, suffix
    return "", str(message_id or "")


def _scoped_message_prefix(message_id: str) -> str:
    prefix, _suffix = _split_scoped_message_id(message_id)
    return prefix


def _reply_target_candidates(message_id: str, reply_to: str | None) -> list[str]:
    target = str(reply_to or "").strip()
    if not target:
        return []

    prefix = _scoped_message_prefix(message_id)
    if not prefix:
        return [target]

    target_prefix, target_suffix = _split_scoped_message_id(target)
    candidates: list[str] = []
    if target_prefix and target_suffix:
        if (
            target_prefix != prefix
            and (
                prefix.replace("\\", "/").lower().startswith("uploads/")
                or Path(target_prefix).name == Path(prefix).name
            )
        ):
            candidates.append(f"{prefix}:{target_suffix}")
        candidates.append(target)
        candidates.extend(_legacy_message_id_candidates(f"{prefix}:{target_suffix}"))
    else:
        candidates.append(f"{prefix}:{target}")
        basename_scoped = f"{Path(prefix).name}:{target}" if prefix.lower().endswith(".json") else ""
        if basename_scoped and basename_scoped != candidates[0]:
            candidates.append(basename_scoped)
        candidates.append(target)
    return list(dict.fromkeys(candidates))


def _message_values_match_for_legacy(existing: tuple[Any, ...], incoming: tuple[Any, ...]) -> bool:
    # sender/content may improve as parser compatibility grows; identity is anchored
    # by the old raw id plus stable timeline/type/thread fields.
    if existing[1] != incoming[1]:
        return False
    if existing[2] != incoming[2]:
        return False
    if existing[4] != incoming[4]:
        return False
    if existing[5] != incoming[5]:
        return False
    existing_reply = existing[6]
    incoming_reply = incoming[6]
    return (
        existing_reply == incoming_reply
        or (
            incoming_reply is not None
            and existing_reply in _legacy_message_id_candidates(str(incoming_reply))
        )
    )


def _message_values_from_row(row: sqlite3.Row) -> tuple[Any, ...]:
    return (
        row["sender"],
        int(row["is_self"]),
        row["timestamp"],
        row["content"],
        row["msg_type"],
        row["thread"],
        row["reply_to"],
    )


def _stable_upload_suffix(message_id: str) -> str:
    prefix, suffix = _split_scoped_message_id(message_id)
    if not suffix:
        return ""
    if prefix.replace("\\", "/").lower().startswith("uploads/"):
        return suffix
    return ""


def _stable_upload_reply_suffix(message_id: str, reply_to: str | None) -> str:
    if not _stable_upload_suffix(message_id):
        return ""
    target = str(reply_to or "").strip()
    if not target:
        return ""
    _target_prefix, target_suffix = _split_scoped_message_id(target)
    return target_suffix


def _load_scoped_suffix_candidates(
    conn: sqlite3.Connection,
    suffixes_by_thread: dict[str, set[str]],
    incoming_ids: set[str],
) -> tuple[dict[tuple[str, str], list[str]], dict[str, tuple[Any, ...]]]:
    if not suffixes_by_thread:
        return {}, {}

    candidates_by_thread_suffix: dict[tuple[str, str], list[str]] = defaultdict(list)
    rows_by_id: dict[str, tuple[Any, ...]] = {}
    threads = sorted(suffixes_by_thread)
    for batch in _batched(threads):
        placeholders = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"""
            SELECT id, sender, is_self, timestamp, content, msg_type, thread, reply_to
            FROM messages
            WHERE thread IN ({placeholders})
              AND id LIKE '%:%'
            ORDER BY id
            """,
            batch,
        ).fetchall()
        for row in rows:
            row_id = str(row["id"])
            if row_id in incoming_ids:
                continue
            _prefix, suffix = _split_scoped_message_id(row_id)
            thread = str(row["thread"])
            if not suffix or suffix not in suffixes_by_thread.get(thread, set()):
                continue
            candidates_by_thread_suffix[(thread, suffix)].append(row_id)
            rows_by_id[row_id] = _message_values_from_row(row)

    return candidates_by_thread_suffix, rows_by_id


DB_PATH = os.getenv("CHAT_DB", str(Path("runtime") / "chat.db"))

_conn: sqlite3.Connection | None = None
_thread_local = threading.local()
_conn_lock = threading.RLock()
_connections: dict[int, sqlite3.Connection] = {}
_vec_available = False
_vec_warning_emitted = False
VECTOR_TABLE = "sessions_vec"


def _sqlite_path(path: str) -> str:
    if path == ":memory:" or path.startswith("file:"):
        return path
    db_path = Path(path).expanduser()
    if db_path.parent != Path("."):
        db_path.parent.mkdir(parents=True, exist_ok=True)
    return str(db_path)


def _sqlite_uri(path: str) -> bool:
    return path.startswith("file:")


def db() -> sqlite3.Connection:
    global _conn, _vec_available, _vec_warning_emitted
    local_conn = getattr(_thread_local, "conn", None)
    if local_conn is not None and id(local_conn) in _connections:
        return local_conn
    if local_conn is not None:
        _thread_local.conn = None
        _thread_local.vec_available = False

    with _conn_lock:
        conn = sqlite3.connect(_sqlite_path(DB_PATH), timeout=30, check_same_thread=False, uri=_sqlite_uri(DB_PATH))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        # WAL 模式下 NORMAL 已能保证崩溃一致性，且大幅减少每次 commit 的 fsync 开销
        conn.execute("PRAGMA synchronous = NORMAL")
        conn.execute("PRAGMA cache_size = -64000")

        try:
            import sqlite_vec

            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
            vec_available = True
        except Exception as exc:
            if not _vec_warning_emitted:
                print(f"[警告] sqlite-vec 加载失败，语义检索退化为全文检索：{exc}")
                _vec_warning_emitted = True
            vec_available = False

        init_schema(conn, vec_available=vec_available)
        _thread_local.conn = conn
        _thread_local.vec_available = vec_available
        _connections[id(conn)] = conn
        if _conn is None:
            _conn = conn
        _vec_available = bool(_vec_available or vec_available)
        return conn


def has_vec() -> bool:
    db()
    return bool(getattr(_thread_local, "vec_available", False))


def close_current_connection() -> None:
    global _conn
    with _conn_lock:
        conn = getattr(_thread_local, "conn", None)
        if conn is not None:
            _connections.pop(id(conn), None)
            conn.close()
            _thread_local.conn = None
            _thread_local.vec_available = False
            if _conn is conn:
                _conn = next(iter(_connections.values()), None)


def close_all_connections() -> None:
    global _conn, _vec_available
    with _conn_lock:
        for conn in list(_connections.values()):
            conn.close()
        _connections.clear()
        _conn = None
        _vec_available = False
        _thread_local.conn = None
        _thread_local.vec_available = False


def init_schema(conn: sqlite3.Connection, vec_available: bool | None = None) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS messages (
          id        TEXT PRIMARY KEY,
          sender    TEXT NOT NULL,
          is_self   INTEGER NOT NULL,
          timestamp TEXT NOT NULL,
          content   TEXT NOT NULL,
          msg_type  TEXT NOT NULL,
          thread    TEXT NOT NULL,
          reply_to  TEXT,
          seq       INTEGER
        );
        CREATE INDEX IF NOT EXISTS idx_time   ON messages(timestamp);
        CREATE INDEX IF NOT EXISTS idx_sender ON messages(sender);
        CREATE INDEX IF NOT EXISTS idx_seq    ON messages(thread, seq);
        CREATE INDEX IF NOT EXISTS idx_thread_time ON messages(thread, timestamp);
        CREATE INDEX IF NOT EXISTS idx_sender_time ON messages(sender, timestamp);
        CREATE INDEX IF NOT EXISTS idx_self_time   ON messages(is_self, timestamp);

        CREATE TABLE IF NOT EXISTS sessions (
          session_id   INTEGER PRIMARY KEY,
          thread       TEXT NOT NULL,
          start_time   TEXT NOT NULL,
          end_time     TEXT NOT NULL,
          participants TEXT NOT NULL,
          msg_ids      TEXT NOT NULL,
          text         TEXT NOT NULL,
          summary      TEXT,
          text_hash    TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_thread ON sessions(thread);
        CREATE INDEX IF NOT EXISTS idx_sessions_time ON sessions(start_time, end_time);

        CREATE TABLE IF NOT EXISTS msg_session (
          msg_id     TEXT PRIMARY KEY,
          session_id INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_msg_session_sid ON msg_session(session_id);

        CREATE TABLE IF NOT EXISTS ingest_files (
          path       TEXT PRIMARY KEY,
          size       INTEGER NOT NULL,
          mtime_ns   INTEGER NOT NULL,
          total      INTEGER NOT NULL,
          included   INTEGER NOT NULL,
          inserted   INTEGER NOT NULL,
          parser_version INTEGER,
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS ingest_file_messages (
          path       TEXT NOT NULL,
          msg_id     TEXT NOT NULL,
          PRIMARY KEY (path, msg_id)
        );
        CREATE INDEX IF NOT EXISTS idx_ingest_file_messages_msg ON ingest_file_messages(msg_id);
        CREATE INDEX IF NOT EXISTS idx_ingest_file_messages_path ON ingest_file_messages(path);
        """
    )
    _migrate_sessions_text_hash(conn)
    _migrate_ingest_files_parser_version(conn)
    create_messages_fts(conn, if_not_exists=True)
    if vec_available:
        create_vector_table(conn, EMBED_DIM, if_not_exists=True)
    conn.commit()


def _migrate_sessions_text_hash(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(sessions)")}
    if "text_hash" not in columns:
        conn.execute("ALTER TABLE sessions ADD COLUMN text_hash TEXT")
    rows = conn.execute("SELECT session_id, text FROM sessions WHERE text_hash IS NULL").fetchall()
    if rows:
        conn.executemany(
            "UPDATE sessions SET text_hash = ? WHERE session_id = ?",
            [(chunk_text_hash(row["text"]), row["session_id"]) for row in rows],
        )


def _migrate_ingest_files_parser_version(conn: sqlite3.Connection) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info(ingest_files)")}
    if "parser_version" not in columns:
        conn.execute("ALTER TABLE ingest_files ADD COLUMN parser_version INTEGER")


def create_messages_fts(conn: sqlite3.Connection, if_not_exists: bool = True) -> None:
    clause = "IF NOT EXISTS " if if_not_exists else ""
    conn.execute(
        f"""
        CREATE VIRTUAL TABLE {clause}messages_fts
          USING fts5(content, content=messages, content_rowid=rowid, tokenize='trigram')
        """
    )


def create_vector_table(conn: sqlite3.Connection, dimension: int, if_not_exists: bool = True) -> None:
    if dimension <= 0:
        raise ValueError(f"向量维度无效：{dimension}")
    clause = "IF NOT EXISTS " if if_not_exists else ""
    conn.execute(
        f"""
        CREATE VIRTUAL TABLE {clause}{VECTOR_TABLE}
          USING vec0(session_id INTEGER PRIMARY KEY, embedding FLOAT[{dimension}])
        """
    )


def vector_table_dimension() -> int | None:
    if not has_vec():
        return None
    row = db().execute(
        "SELECT sql FROM sqlite_schema WHERE name = ?",
        (VECTOR_TABLE,),
    ).fetchone()
    if row is None or not row["sql"]:
        return None
    match = re.search(r"embedding\s+FLOAT\[(\d+)\]", row["sql"], re.IGNORECASE)
    return int(match.group(1)) if match else None


def reset_vector_table(dimension: int) -> None:
    if not has_vec():
        raise RuntimeError("sqlite-vec 不可用")
    conn = db()
    with conn:
        conn.execute(f"DROP TABLE IF EXISTS {VECTOR_TABLE}")
        create_vector_table(conn, dimension, if_not_exists=False)


def ensure_vector_table_dimension(dimension: int) -> bool:
    current = vector_table_dimension()
    if current == dimension:
        return False
    reset_vector_table(dimension)
    return True


def upsert_messages(messages: list[NormMessage]) -> MessageWriteResult:
    conn = db()
    if not messages:
        return MessageWriteResult(inserted=0, updated=0, unchanged=0, threads=frozenset())

    unique_messages = {message.id: message for message in messages}
    messages = list(unique_messages.values())
    incoming_ids = {message.id for message in messages}
    existing: dict[str, tuple[Any, ...]] = {}
    ids = [m.id for m in messages]
    for batch in _batched(ids):
        placeholders = ",".join("?" for _ in batch)
        for row in conn.execute(
            f"""
            SELECT id, sender, is_self, timestamp, content, msg_type, thread, reply_to
            FROM messages
            WHERE id IN ({placeholders})
            """,
            batch,
        ):
            existing[row["id"]] = _message_values_from_row(row)

    suffixes_by_thread: dict[str, set[str]] = defaultdict(set)
    stable_upload_suffix_by_id: dict[str, str] = {}
    stable_upload_reply_suffix_by_id: dict[str, str] = {}
    for message in messages:
        existing_values = existing.get(message.id)
        if message.id not in existing:
            suffix = _stable_upload_suffix(message.id)
            if suffix:
                stable_upload_suffix_by_id[message.id] = suffix
                suffixes_by_thread[message.thread].add(suffix)
        should_repair_reply = message.id not in existing or (
            message.reply_to is not None
            and existing_values is not None
            and existing_values[6] == message.reply_to
        )
        reply_suffix = _stable_upload_reply_suffix(message.id, message.reply_to) if should_repair_reply else ""
        if reply_suffix:
            stable_upload_reply_suffix_by_id[message.id] = reply_suffix
            suffixes_by_thread[message.thread].add(reply_suffix)

    scoped_suffix_candidates, scoped_suffix_rows = _load_scoped_suffix_candidates(
        conn,
        suffixes_by_thread,
        incoming_ids,
    )
    reply_candidates_by_id = {
        message.id: _reply_target_candidates(message.id, message.reply_to)
        for message in messages
        if message.reply_to
    }
    for message in messages:
        suffix = stable_upload_reply_suffix_by_id.get(message.id)
        if not suffix:
            continue
        existing_candidates = reply_candidates_by_id.setdefault(message.id, [])
        for candidate in scoped_suffix_candidates.get((message.thread, suffix), []):
            if candidate not in existing_candidates:
                existing_candidates.append(candidate)
    reply_target_candidates = list(
        dict.fromkeys(
            candidate
            for candidates in reply_candidates_by_id.values()
            for candidate in candidates
            if candidate not in incoming_ids
        )
    )
    existing_reply_targets: set[str] = set()
    for batch in _batched(reply_target_candidates):
        placeholders = ",".join("?" for _ in batch)
        rows = conn.execute(f"SELECT id FROM messages WHERE id IN ({placeholders})", batch).fetchall()
        existing_reply_targets.update(str(row["id"]) for row in rows)

    normalized_reply_to: dict[str, str | None] = {}
    for message in messages:
        candidates = reply_candidates_by_id.get(message.id, [])
        normalized_reply_to[message.id] = next(
            (
                candidate
                for candidate in candidates
                if candidate != message.id and (candidate in incoming_ids or candidate in existing_reply_targets)
            ),
            message.reply_to,
        )

    legacy_rows: dict[str, tuple[Any, ...]] = {}
    legacy_candidates = {
        legacy_id
        for message in messages
        if message.id not in existing
        for legacy_id in _legacy_message_id_candidates(message.id)
    }
    if legacy_candidates:
        for batch in _batched(list(legacy_candidates)):
            placeholders = ",".join("?" for _ in batch)
            for row in conn.execute(
                f"""
                SELECT id, sender, is_self, timestamp, content, msg_type, thread, reply_to
                FROM messages
                WHERE id IN ({placeholders})
                """,
                batch,
            ):
                legacy_rows[row["id"]] = _message_values_from_row(row)
    legacy_rows.update(scoped_suffix_rows)

    insert_rows: list[tuple[Any, ...]] = []
    update_rows: list[tuple[Any, ...]] = []
    rename_rows: list[tuple[str, str, str]] = []
    changed_existing_ids: set[str] = set()
    unchanged = 0
    touched_threads: set[str] = set()
    claimed_legacy_ids: set[str] = set()

    for message in messages:
        values = (
            message.sender,
            int(message.is_self),
            message.timestamp,
            message.content,
            message.msg_type,
            message.thread,
            normalized_reply_to.get(message.id, message.reply_to),
        )
        legacy_ids = _legacy_message_id_candidates(message.id)
        suffix = stable_upload_suffix_by_id.get(message.id)
        if suffix:
            legacy_ids.extend(
                candidate
                for candidate in scoped_suffix_candidates.get((message.thread, suffix), [])
                if candidate not in legacy_ids
            )
        if message.id not in existing:
            for legacy_id in legacy_ids:
                if legacy_id in claimed_legacy_ids:
                    continue
                legacy_values = legacy_rows.get(legacy_id)
                if legacy_values is not None and _message_values_match_for_legacy(legacy_values, values):
                    rename_rows.append((message.id, legacy_id, message.thread))
                    existing[message.id] = legacy_values
                    claimed_legacy_ids.add(legacy_id)
                    changed_existing_ids.add(message.id)
                    touched_threads.add(message.thread)
                    break

        if message.id not in existing:
            insert_rows.append((message.id, *values))
            touched_threads.add(message.thread)
        elif existing[message.id] != values:
            update_rows.append((*values, message.id))
            changed_existing_ids.add(message.id)
            touched_threads.add(str(existing[message.id][5]))
            touched_threads.add(message.thread)
        else:
            if message.id not in changed_existing_ids:
                unchanged += 1

    with conn:
        for new_id, legacy_id, thread in rename_rows:
            conn.execute("UPDATE messages SET id = ? WHERE id = ?", (new_id, legacy_id))
            conn.execute(
                "UPDATE messages SET reply_to = ? WHERE reply_to = ? AND thread = ?",
                (new_id, legacy_id, thread),
            )
            conn.execute("UPDATE msg_session SET msg_id = ? WHERE msg_id = ?", (new_id, legacy_id))
            source_rows = conn.execute(
                "SELECT path FROM ingest_file_messages WHERE msg_id = ?",
                (legacy_id,),
            ).fetchall()
            if source_rows:
                conn.executemany(
                    "INSERT OR IGNORE INTO ingest_file_messages (path, msg_id) VALUES (?, ?)",
                    [(row["path"], new_id) for row in source_rows],
                )
                conn.execute("DELETE FROM ingest_file_messages WHERE msg_id = ?", (legacy_id,))
        if insert_rows:
            conn.executemany(
                """
                INSERT INTO messages (id, sender, is_self, timestamp, content, msg_type, thread, reply_to)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                insert_rows,
            )
        if update_rows:
            conn.executemany(
                """
                UPDATE messages
                SET sender = ?,
                    is_self = ?,
                    timestamp = ?,
                    content = ?,
                    msg_type = ?,
                    thread = ?,
                    reply_to = ?
                WHERE id = ?
                """,
                update_rows,
            )

    return MessageWriteResult(
        inserted=len(insert_rows),
        updated=len(changed_existing_ids),
        unchanged=unchanged,
        threads=frozenset(touched_threads),
    )


def insert_messages(messages: list[NormMessage]) -> int:
    """Backward-compatible wrapper returning the number of changed rows."""
    return upsert_messages(messages).changed


def ingest_file_unchanged(path: str, size: int, mtime_ns: int) -> bool:
    row = db().execute(
        "SELECT size, mtime_ns, parser_version FROM ingest_files WHERE path = ?",
        (path,),
    ).fetchone()
    return bool(
        row
        and row["size"] == size
        and row["mtime_ns"] == mtime_ns
        and int(row["parser_version"] or 0) >= PARSER_VERSION
    )


def get_ingest_file_records(paths: list[str]) -> dict[str, dict[str, Any]]:
    if not paths:
        return {}

    records: dict[str, dict[str, Any]] = {}
    for batch in _batched(paths):
        placeholders = ",".join("?" for _ in batch)
        rows = db().execute(
            f"""
            SELECT path, size, mtime_ns, total, included, inserted, inserted AS changed, parser_version, updated_at
            FROM ingest_files
            WHERE path IN ({placeholders})
            """,
            batch,
        ).fetchall()
        records.update({str(row["path"]): dict(row) for row in rows})
    return records


def record_ingest_file(path: str, size: int, mtime_ns: int, total: int, included: int, changed: int) -> None:
    db().execute(
        """
        INSERT INTO ingest_files (path, size, mtime_ns, total, included, inserted, parser_version, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(path) DO UPDATE SET
          size = excluded.size,
          mtime_ns = excluded.mtime_ns,
          total = excluded.total,
          included = excluded.included,
          inserted = excluded.inserted,
          parser_version = excluded.parser_version,
          updated_at = CURRENT_TIMESTAMP
        """,
        (path, size, mtime_ns, total, included, changed, PARSER_VERSION),
    )
    db().commit()


def resolve_existing_message_ids(message_ids: list[str]) -> list[str]:
    """Return existing DB ids for imported ids, including legacy raw/basename ids."""
    ordered_candidates: list[list[str]] = []
    all_candidates: list[str] = []
    for message_id in message_ids:
        candidates = [message_id, *_legacy_message_id_candidates(message_id)]
        ordered_candidates.append(candidates)
        all_candidates.extend(candidates)

    existing: set[str] = set()
    for batch in _batched(list(dict.fromkeys(all_candidates))):
        placeholders = ",".join("?" for _ in batch)
        rows = db().execute(f"SELECT id FROM messages WHERE id IN ({placeholders})", batch).fetchall()
        existing.update(str(row["id"]) for row in rows)

    resolved: list[str] = []
    seen: set[str] = set()
    for candidates in ordered_candidates:
        for candidate in candidates:
            if candidate in existing and candidate not in seen:
                resolved.append(candidate)
                seen.add(candidate)
                break
    return resolved


def record_ingest_file_messages(path: str, message_ids: list[str]) -> None:
    conn = db()
    requested_ids = list(dict.fromkeys(message_ids))
    existing_ids: set[str] = set()
    for batch in _batched(requested_ids):
        placeholders = ",".join("?" for _ in batch)
        rows = conn.execute(f"SELECT id FROM messages WHERE id IN ({placeholders})", batch).fetchall()
        existing_ids.update(str(row["id"]) for row in rows)
    ids = [message_id for message_id in requested_ids if message_id in existing_ids]
    with conn:
        conn.execute("DELETE FROM ingest_file_messages WHERE path = ?", (path,))
        for batch in _batched(ids):
            conn.executemany(
                "INSERT OR IGNORE INTO ingest_file_messages (path, msg_id) VALUES (?, ?)",
                [(path, msg_id) for msg_id in batch],
            )


def ingest_file_message_mapping_exists(path: str) -> bool:
    row = db().execute(
        "SELECT 1 FROM ingest_file_messages WHERE path = ? LIMIT 1",
        (path,),
    ).fetchone()
    return row is not None


def get_ingest_file_message_mapping_paths(paths: list[str]) -> set[str]:
    cleaned = [path for path in dict.fromkeys(paths) if path]
    if not cleaned:
        return set()

    conn = db()
    mapped: set[str] = set()
    for batch in _batched(cleaned):
        placeholders = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"""
            SELECT DISTINCT path
            FROM ingest_file_messages
            WHERE path IN ({placeholders})
            """,
            batch,
        ).fetchall()
        mapped.update(str(row["path"]) for row in rows)
    return mapped


def get_threads_for_ingest_file_paths(paths: list[str]) -> list[str]:
    cleaned = [path for path in dict.fromkeys(paths) if path]
    if not cleaned:
        return []

    conn = db()
    threads: set[str] = set()
    for batch in _batched(cleaned):
        placeholders = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"""
            SELECT DISTINCT m.thread
            FROM ingest_file_messages ifm
            JOIN messages m ON m.id = ifm.msg_id
            WHERE ifm.path IN ({placeholders})
            ORDER BY m.thread
            """,
            batch,
        ).fetchall()
        threads.update(str(row["thread"]) for row in rows)
    return sorted(threads)


def get_session_ids_for_ingest_file_paths(paths: list[str]) -> list[int]:
    by_path = get_session_ids_by_ingest_file_paths(paths)
    return sorted({session_id for session_ids in by_path.values() for session_id in session_ids})


def get_session_ids_by_ingest_file_paths(paths: list[str]) -> dict[str, list[int]]:
    cleaned = [path for path in dict.fromkeys(paths) if path]
    if not cleaned:
        return {}

    conn = db()
    session_ids_by_path: dict[str, set[int]] = defaultdict(set)
    for batch in _batched(cleaned):
        placeholders = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"""
            SELECT DISTINCT ifm.path, ms.session_id
            FROM ingest_file_messages ifm
            JOIN msg_session ms ON ms.msg_id = ifm.msg_id
            WHERE ifm.path IN ({placeholders})
            ORDER BY ifm.path, ms.session_id
            """,
            batch,
        ).fetchall()
        for row in rows:
            session_ids_by_path[str(row["path"])].add(int(row["session_id"]))
    return {path: sorted(session_ids_by_path.get(path, set())) for path in cleaned if path in session_ids_by_path}


def recompute_message_sequence(threads: list[str] | None = None) -> None:
    conn = db()
    if threads is not None:
        scoped_threads = list(dict.fromkeys(threads))
        if not scoped_threads:
            return
        for batch in _batched(scoped_threads):
            placeholders = ",".join("?" for _ in batch)
            conn.execute(
                f"""
                UPDATE messages
                SET seq = ranked.rn
                FROM (
                  SELECT id, ROW_NUMBER() OVER (PARTITION BY thread ORDER BY timestamp, id) AS rn
                  FROM messages WHERE thread IN ({placeholders})
                ) AS ranked
                WHERE messages.id = ranked.id AND messages.seq IS NOT ranked.rn
                """,
                batch,
            )
        conn.commit()
        return
    # UPDATE...FROM 走 messages 主键索引联接，复杂度 O(n log n)；
    # seq IS NOT rn 跳过已正确的行，追加场景几乎只写新消息。
    conn.execute(
        """
        UPDATE messages
        SET seq = ranked.rn
        FROM (
          SELECT id, ROW_NUMBER() OVER (PARTITION BY thread ORDER BY timestamp, id) AS rn
          FROM messages
        ) AS ranked
        WHERE messages.id = ranked.id AND messages.seq IS NOT ranked.rn
        """,
    )
    conn.commit()


def rebuild_fts() -> None:
    conn = db()
    with conn:
        conn.execute("DROP TABLE IF EXISTS messages_fts")
        create_messages_fts(conn, if_not_exists=False)
        conn.execute(
            """
            INSERT INTO messages_fts(rowid, content)
            SELECT rowid, content FROM messages
            """
        )


def _message_rowids_for_ingest_targets(paths: list[str], prefixes: list[str]) -> list[int]:
    conn = db()
    rowids: set[int] = set()
    cleaned_paths = [path for path in dict.fromkeys(paths) if path]
    for batch in _batched(cleaned_paths):
        placeholders = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"""
            SELECT DISTINCT m.rowid
            FROM ingest_file_messages ifm
            JOIN messages m ON m.id = ifm.msg_id
            WHERE ifm.path IN ({placeholders})
            """,
            batch,
        ).fetchall()
        rowids.update(int(row["rowid"]) for row in rows)

    cleaned_prefixes = [prefix for prefix in dict.fromkeys(prefixes) if prefix]
    for prefix in cleaned_prefixes:
        rows = conn.execute(
            """
            SELECT rowid
            FROM messages
            WHERE id LIKE ? || '%' ESCAPE '\\'
            """,
            (_escape_like(prefix),),
        ).fetchall()
        rowids.update(int(row["rowid"]) for row in rows)
    return sorted(rowids)


def rebuild_fts_for_ingest_targets(paths: list[str], prefixes: list[str]) -> int:
    rowids = _message_rowids_for_ingest_targets(paths, prefixes)
    if not rowids:
        return 0
    conn = db()
    rebuilt = 0
    with conn:
        for batch in _batched(rowids):
            placeholders = ",".join("?" for _ in batch)
            conn.execute(f"DELETE FROM messages_fts WHERE rowid IN ({placeholders})", batch)
            conn.execute(
                f"""
                INSERT INTO messages_fts(rowid, content)
                SELECT rowid, content
                FROM messages
                WHERE rowid IN ({placeholders})
                """,
                batch,
            )
            rebuilt += len(batch)
    return rebuilt


def count_missing_fts() -> int:
    return int(
        db().execute(
            """
            SELECT COUNT(*) c
            FROM messages m
            WHERE NOT EXISTS (
              SELECT 1 FROM messages_fts_docsize d WHERE d.id = m.rowid
            )
            """
        ).fetchone()["c"]
    )


def count_missing_fts_for_ingest_targets(paths: list[str], prefixes: list[str]) -> int:
    rowids = _message_rowids_for_ingest_targets(paths, prefixes)
    if not rowids:
        return 0
    conn = db()
    missing = 0
    for batch in _batched(rowids):
        placeholders = ",".join("?" for _ in batch)
        row = conn.execute(
            f"""
            SELECT COUNT(*) c
            FROM messages m
            WHERE m.rowid IN ({placeholders})
              AND NOT EXISTS (
                SELECT 1 FROM messages_fts_docsize d WHERE d.id = m.rowid
              )
            """,
            batch,
        ).fetchone()
        missing += int(row["c"] or 0)
    return missing


def count_messages_missing_seq() -> int:
    return int(db().execute("SELECT COUNT(*) c FROM messages WHERE seq IS NULL").fetchone()["c"])


def count_messages_missing_seq_for_ingest_targets(paths: list[str], prefixes: list[str]) -> int:
    rowids = _message_rowids_for_ingest_targets(paths, prefixes)
    if not rowids:
        return 0
    conn = db()
    missing = 0
    for batch in _batched(rowids):
        placeholders = ",".join("?" for _ in batch)
        row = conn.execute(
            f"""
            SELECT COUNT(*) c
            FROM messages
            WHERE rowid IN ({placeholders})
              AND seq IS NULL
            """,
            batch,
        ).fetchone()
        missing += int(row["c"] or 0)
    return missing


def sync_missing_fts_for_ingest_targets(paths: list[str], prefixes: list[str]) -> int:
    rowids = _message_rowids_for_ingest_targets(paths, prefixes)
    if not rowids:
        return 0
    conn = db()
    synced = 0
    with conn:
        for batch in _batched(rowids):
            placeholders = ",".join("?" for _ in batch)
            row = conn.execute(
                f"""
                SELECT COUNT(*) c
                FROM messages m
                WHERE m.rowid IN ({placeholders})
                  AND NOT EXISTS (
                    SELECT 1 FROM messages_fts_docsize d WHERE d.id = m.rowid
                  )
                """,
                batch,
            ).fetchone()
            missing = int(row["c"] or 0)
            if not missing:
                continue
            conn.execute(
                f"""
                INSERT INTO messages_fts(rowid, content)
                SELECT m.rowid, m.content
                FROM messages m
                WHERE m.rowid IN ({placeholders})
                  AND NOT EXISTS (
                    SELECT 1 FROM messages_fts_docsize d WHERE d.id = m.rowid
                  )
                """,
                batch,
            )
            synced += missing
    return synced


def sync_missing_fts() -> int:
    conn = db()
    # 注意：不能对 messages_fts 按 rowid 做 EXISTS 判断——external content FTS5
    # 的 rowid 查询会回源到 messages 表，永远命中，导致增量行从未真正入索引。
    # 查 _docsize 影子表（每个已索引文档一行）才能得知索引的真实内容。
    missing = count_missing_fts()
    if missing == 0:
        return 0
    with conn:
        conn.execute(
            """
            INSERT INTO messages_fts(rowid, content)
            SELECT m.rowid, m.content
            FROM messages m
            WHERE NOT EXISTS (
              SELECT 1 FROM messages_fts_docsize d WHERE d.id = m.rowid
            )
            """
        )
    return int(missing)


def finalize_ingest() -> None:
    recompute_message_sequence()
    rebuild_fts()


def get_carryover_for_threads(threads: list[str] | None = None) -> dict[str, tuple[str | None, bytes | None]]:
    """读取即将被重建的会话块的 (text_hash -> (summary, embedding 字节))，用于内容未变块的复用。"""
    conn = db()
    if threads is None:
        rows = conn.execute("SELECT session_id, text_hash, summary FROM sessions").fetchall()
    else:
        if not threads:
            return {}
        placeholders = ",".join("?" for _ in threads)
        rows = conn.execute(
            f"SELECT session_id, text_hash, summary FROM sessions WHERE thread IN ({placeholders})",
            list(threads),
        ).fetchall()

    vectors: dict[int, bytes] = {}
    if has_vec() and rows:
        ids = [int(row["session_id"]) for row in rows]
        for batch in _batched(ids):
            placeholders = ",".join("?" for _ in batch)
            for vec_row in conn.execute(
                f"SELECT session_id, embedding FROM {VECTOR_TABLE} WHERE session_id IN ({placeholders})",
                batch,
            ):
                vectors[int(vec_row["session_id"])] = bytes(vec_row["embedding"])

    carry: dict[str, tuple[str | None, bytes | None]] = {}
    for row in rows:
        if row["text_hash"]:
            carry[row["text_hash"]] = (row["summary"], vectors.get(int(row["session_id"])))
    return carry


def replace_sessions(rows: list[Any], threads: list[str] | None = None) -> list[int]:
    """重建会话分块。threads=None 时全量重建；否则只替换指定线程的分块。"""
    conn = db()
    ids: list[int] = []
    with conn:
        if threads is None:
            conn.execute("DELETE FROM sessions")
            conn.execute("DELETE FROM msg_session")
            if has_vec():
                conn.execute(f"DELETE FROM {VECTOR_TABLE}")
        elif threads:
            scoped_threads = list(dict.fromkeys(threads))
            for thread_batch in _batched(scoped_threads):
                placeholders = ",".join("?" for _ in thread_batch)
                old_ids = [
                    int(row["session_id"])
                    for row in conn.execute(
                        f"SELECT session_id FROM sessions WHERE thread IN ({placeholders})",
                        thread_batch,
                    )
                ]
                for batch in _batched(old_ids):
                    id_marks = ",".join("?" for _ in batch)
                    conn.execute(f"DELETE FROM msg_session WHERE session_id IN ({id_marks})", batch)
                    if has_vec():
                        conn.execute(f"DELETE FROM {VECTOR_TABLE} WHERE session_id IN ({id_marks})", batch)
                conn.execute(f"DELETE FROM sessions WHERE thread IN ({placeholders})", thread_batch)

        for row in rows:
            data = row.__dict__ if hasattr(row, "__dict__") else dict(row)
            data = {**data, "text_hash": data.get("text_hash") or chunk_text_hash(data["text"])}
            cur = conn.execute(
                """
                INSERT INTO sessions (thread, start_time, end_time, participants, msg_ids, text, summary, text_hash)
                VALUES (:thread, :start_time, :end_time, :participants, :msg_ids, :text, :summary, :text_hash)
                """,
                data,
            )
            session_id = int(cur.lastrowid)
            ids.append(session_id)
            conn.executemany(
                "INSERT OR REPLACE INTO msg_session (msg_id, session_id) VALUES (?, ?)",
                [(msg_id, session_id) for msg_id in json.loads(data["msg_ids"])],
            )
    return ids


def insert_embeddings(items: list[dict[str, Any]]) -> None:
    if not items:
        return
    if not has_vec():
        raise RuntimeError("sqlite-vec 不可用")

    def to_bytes(embedding: Any) -> bytes:
        if isinstance(embedding, (bytes, memoryview)):
            return bytes(embedding)
        return array.array("f", embedding).tobytes()

    payload = [(int(item["session_id"]), to_bytes(item["embedding"])) for item in items]
    dimensions = {len(blob) // 4 for _, blob in payload}
    if len(dimensions) > 1:
        raise RuntimeError(f"同一批 embedding 维度不一致：{sorted(dimensions)}")
    dimension = next(iter(dimensions), None)
    expected = vector_table_dimension()
    if dimension is not None and expected is not None and dimension != expected:
        raise RuntimeError(
            f"embedding 维度不匹配：sessions_vec 期望 {expected} 维，但本批是 {dimension} 维。"
            "请重跑 ingest，程序会自动重建向量表。"
        )
    deduped = {session_id: blob for session_id, blob in payload}
    payload = list(deduped.items())
    conn = db()
    with conn:
        ids = [session_id for session_id, _ in payload]
        for batch in _batched(ids):
            placeholders = ",".join("?" for _ in batch)
            conn.execute(f"DELETE FROM {VECTOR_TABLE} WHERE session_id IN ({placeholders})", batch)
        conn.executemany(
            f"INSERT INTO {VECTOR_TABLE} (session_id, embedding) VALUES (?, ?)",
            payload,
        )


def set_summary(session_id: int, summary: str) -> None:
    db().execute("UPDATE sessions SET summary = ? WHERE session_id = ?", (summary, session_id))
    db().commit()


def set_summaries(items: list[tuple[int, str]]) -> None:
    if not items:
        return
    conn = db()
    conn.executemany(
        "UPDATE sessions SET summary = ? WHERE session_id = ?",
        [(summary, session_id) for session_id, summary in items],
    )
    conn.commit()


def count_sessions_missing_summary() -> int:
    return int(
        db()
        .execute(
            """
            SELECT COUNT(*) c
            FROM sessions
            WHERE summary IS NULL OR TRIM(summary) = ''
            """
        )
        .fetchone()["c"]
    )


def get_session_ids_with_embedding(session_ids: list[int] | None = None) -> set[int]:
    if not has_vec():
        return set()
    conn = db()
    if session_ids is None:
        rows = conn.execute(f"SELECT session_id FROM {VECTOR_TABLE}").fetchall()
        return {int(row["session_id"]) for row in rows}

    ids = [int(session_id) for session_id in dict.fromkeys(session_ids)]
    if not ids:
        return set()

    embedded: set[int] = set()
    for batch in _batched(ids):
        placeholders = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"SELECT session_id FROM {VECTOR_TABLE} WHERE session_id IN ({placeholders})",
            batch,
        ).fetchall()
        embedded.update(int(row["session_id"]) for row in rows)
    return embedded


def _norm_time(value: Any, is_before: bool) -> str:
    value = str(value or "").strip().replace(" ", "T")
    value = re.sub(r"([Zz]|[+-]\d{2}:\d{2})$", "", value)
    if len(value) == 10:
        try:
            datetime.fromisoformat(value)
        except ValueError:
            return ""
        return f"{value}T23:59:59" if is_before else f"{value}T00:00:00"
    try:
        return datetime.fromisoformat(value).isoformat(timespec="seconds")
    except ValueError:
        return ""


def _escape_like(value: Any) -> str:
    return str(value).replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


SELF_SENDER_ALIASES = {"我", "自己", "本人", "me", "self"}


def _clean_filter_text(value: Any) -> str:
    return " ".join(str(value or "").split())[:MAX_FILTER_CHARS]


def _clean_message_id(value: Any) -> str:
    return str(value or "").strip()[:MAX_MESSAGE_ID_CHARS]


def _query_terms(value: Any) -> tuple[list[str], bool]:
    raw = str(value or "").strip()
    truncated = len(raw) > MAX_QUERY_CHARS
    if truncated:
        raw = raw[:MAX_QUERY_CHARS]
    raw_terms = [term for term in raw.split() if term]
    if len(raw_terms) > MAX_QUERY_TERMS:
        truncated = True
    return raw_terms[:MAX_QUERY_TERMS], truncated


def _filter_clauses(filters: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    where: list[str] = []
    params: dict[str, Any] = {}

    sender = _clean_filter_text(filters.get("sender"))
    if sender:
        if sender.lower() in SELF_SENDER_ALIASES:
            where.append("m.is_self = 1")
        else:
            where.append("m.sender LIKE '%' || :sender || '%' ESCAPE '\\'")
            params["sender"] = _escape_like(sender)

    thread = _clean_filter_text(filters.get("thread"))
    if thread:
        where.append("m.thread LIKE '%' || :thread || '%' ESCAPE '\\'")
        params["thread"] = _escape_like(thread)

    after = filters.get("after")
    if after:
        normalized_after = _norm_time(after, False)
        if normalized_after:
            where.append("m.timestamp >= :after")
            params["after"] = normalized_after

    before = filters.get("before")
    if before:
        normalized_before = _norm_time(before, True)
        if normalized_before:
            where.append("m.timestamp <= :before")
            params["before"] = normalized_before

    return where, params


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


def _content_preview(text: str, limit: int, highlight_terms: list[str] | None = None) -> tuple[str, bool]:
    if len(text) <= limit:
        return text, False
    match_index: int | None = None
    haystack = text.casefold()
    for raw_term in highlight_terms or []:
        term = str(raw_term or "").strip()
        if not term:
            continue
        index = haystack.find(term.casefold())
        if index >= 0 and (match_index is None or index < match_index):
            match_index = index

    if match_index is None:
        return _truncate(text, limit), True

    start = max(0, match_index - limit // 3)
    if start + limit > len(text):
        start = max(0, len(text) - limit)
    end = start + limit
    preview = text[start:end]
    if start > 0:
        preview = "..." + preview
    if end < len(text):
        preview += "..."
    return preview, True


def _to_api_msg(
    row: sqlite3.Row | dict[str, Any],
    *,
    content_limit: int = API_MESSAGE_PREVIEW_CHARS,
    highlight_terms: list[str] | None = None,
) -> dict[str, Any]:
    row = dict(row)
    content = str(row["content"] or "")
    display_content, truncated = _content_preview(content, content_limit, highlight_terms)
    payload = {
        "message_id": row["id"],
        "sender": f"{row['sender']}(我)" if row["is_self"] else row["sender"],
        "time": row["timestamp"].replace("T", " "),
        "content": display_content,
        "type": row["msg_type"],
        "thread": row["thread"],
    }
    if truncated:
        payload["content_truncated"] = True
        payload["content_original_length"] = len(content)
    return payload


def _to_context_msg(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    return _to_api_msg(row, content_limit=CONTEXT_MESSAGE_CHARS)


def _to_preview_msg(row: sqlite3.Row | dict[str, Any], highlight_terms: list[str] | None = None) -> dict[str, Any]:
    return {
        **_to_api_msg(row, content_limit=API_MESSAGE_PREVIEW_CHARS, highlight_terms=highlight_terms),
    }


def search_messages(args: dict[str, Any]) -> dict[str, Any]:
    conn = db()
    limit = _safe_int(args.get("limit"), 20, minimum=1, maximum=100)
    offset = _safe_int(args.get("offset"), 0, minimum=0)
    where, params = _filter_clauses(args)

    terms, query_truncated = _query_terms(args.get("query"))
    if not terms:
        return {
            "total_count": 0,
            "returned": 0,
            "offset": offset,
            "note": "query is empty; provide at least one keyword",
            "messages": [],
        }
    use_fts = bool(terms) and all(len(term) >= 3 for term in terms)

    if use_fts:
        match = " AND ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)
        cond = " AND ".join(["messages_fts MATCH :match", *where])
        base = f"FROM messages_fts JOIN messages m ON m.rowid = messages_fts.rowid WHERE {cond}"
        all_params = {**params, "match": match, "limit": limit, "offset": offset}
        total = conn.execute(f"SELECT COUNT(*) c {base}", all_params).fetchone()["c"]
        rows = conn.execute(
            f"""
            SELECT m.*, bm25(messages_fts) rank {base}
            ORDER BY rank, m.timestamp DESC, m.id DESC
            LIMIT :limit OFFSET :offset
            """,
            all_params,
        ).fetchall()
    else:
        likes = []
        for idx, term in enumerate(terms):
            likes.append(f"m.content LIKE '%' || :q{idx} || '%' ESCAPE '\\'")
            params[f"q{idx}"] = _escape_like(term)
        cond = " AND ".join([*likes, *where]) or "1=1"
        base = f"FROM messages m WHERE {cond}"
        all_params = {**params, "limit": limit, "offset": offset}
        total = conn.execute(f"SELECT COUNT(*) c {base}", all_params).fetchone()["c"]
        rows = conn.execute(
            f"SELECT m.* {base} ORDER BY m.timestamp DESC, m.id DESC LIMIT :limit OFFSET :offset",
            all_params,
        ).fetchall()

    result = {
        "total_count": total,
        "returned": len(rows),
        "offset": offset,
        "messages": [_to_preview_msg(row, terms) for row in rows],
    }
    if query_truncated:
        result["note"] = f"query too long; only the first {MAX_QUERY_TERMS} terms / {MAX_QUERY_CHARS} characters were searched"
    return result


def get_context(args: dict[str, Any]) -> dict[str, Any]:
    conn = db()
    message_id = _clean_message_id(args.get("message_id"))
    if not message_id:
        return {"error": "message_id is required"}
    center = conn.execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    if center is None:
        return {"error": f"查无此消息 id：{message_id}"}

    before_raw = args.get("before")
    after_raw = args.get("after")
    before = _safe_int(before_raw, 15, minimum=0, maximum=50)
    after = _safe_int(after_raw, 15, minimum=0, maximum=50)
    if center["seq"] is None:
        rows = conn.execute(
            """
            WITH ranked AS (
              SELECT
                *,
                ROW_NUMBER() OVER (ORDER BY timestamp, id) AS rn
              FROM messages
              WHERE thread = ?
            ),
            center_rank AS (
              SELECT rn FROM ranked WHERE id = ?
            )
            SELECT ranked.*
            FROM ranked, center_rank
            WHERE ranked.rn BETWEEN center_rank.rn - ? AND center_rank.rn + ?
            ORDER BY ranked.rn
            """,
            (center["thread"], center["id"], before, after),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM messages WHERE thread = ? AND seq BETWEEN ? AND ? ORDER BY seq",
            (center["thread"], center["seq"] - before, center["seq"] + after),
        ).fetchall()

    quoted: dict[str, Any] | None = None
    if center["reply_to"]:
        quoted_row = conn.execute("SELECT * FROM messages WHERE id = ?", (center["reply_to"],)).fetchone()
        quoted = (
            _to_context_msg(quoted_row)
            if quoted_row
            else {"note": "被引消息不在库中（可能是图片/表情等未入库类型），content 中的内联引用文字可作参考"}
        )

    return {
        "thread": center["thread"],
        "center_message_id": center["id"],
        "quoted_message": quoted,
        "messages": [
            {**_to_context_msg(row), **({"is_center": True} if row["id"] == center["id"] else {})}
            for row in rows
        ],
    }


def browse(args: dict[str, Any]) -> dict[str, Any]:
    conn = db()
    limit = _safe_int(args.get("limit"), 50, minimum=1, maximum=200)
    offset = _safe_int(args.get("offset"), 0, minimum=0)
    where, params = _filter_clauses(args)
    cond = " AND ".join(where) or "1=1"
    base = f"FROM messages m WHERE {cond}"
    all_params = {**params, "limit": limit, "offset": offset}
    total = conn.execute(f"SELECT COUNT(*) c {base}", all_params).fetchone()["c"]
    rows = conn.execute(f"SELECT m.* {base} ORDER BY m.timestamp, m.id LIMIT :limit OFFSET :offset", all_params).fetchall()
    return {"total_count": total, "returned": len(rows), "offset": offset, "messages": [_to_preview_msg(row) for row in rows]}


def stats_summary(*, include_message_types: bool = True) -> dict[str, Any]:
    conn = db()
    overall = conn.execute(
        """
        SELECT
          COUNT(*) total,
          MIN(timestamp) earliest,
          MAX(timestamp) latest,
          COUNT(DISTINCT NULLIF(TRIM(COALESCE(thread, '')), '')) thread_count,
          COUNT(DISTINCT NULLIF(TRIM(COALESCE(sender, '')), '')) sender_count
        FROM messages
        """
    ).fetchone()
    types = (
        conn.execute("SELECT msg_type, COUNT(*) count FROM messages GROUP BY msg_type ORDER BY count DESC").fetchall()
        if include_message_types
        else []
    )
    session_count = conn.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"]
    return {
        "total_messages": overall["total"],
        "time_span": {"earliest": overall["earliest"], "latest": overall["latest"]},
        "message_types": [dict(row) for row in types],
        "indexed_session_chunks": session_count,
        "thread_count": overall["thread_count"],
        "sender_count": overall["sender_count"],
    }


def stats_threads(limit: int | None = 50, offset: int = 0) -> dict[str, Any]:
    conn = db()
    safe_offset = _safe_int(offset, 0, minimum=0)
    params: list[Any] = []
    limit_sql = ""
    if limit is not None:
        safe_limit = _safe_int(limit, 50, minimum=1, maximum=500)
        limit_sql = " LIMIT ? OFFSET ?"
        params.extend([safe_limit, safe_offset])
    rows = conn.execute(
        """
        SELECT TRIM(thread) thread, COUNT(*) count, MIN(timestamp) earliest, MAX(timestamp) latest
        FROM messages
        WHERE TRIM(COALESCE(thread, '')) != ''
        GROUP BY TRIM(thread)
        ORDER BY count DESC, latest DESC, thread COLLATE NOCASE ASC
        """ + limit_sql,
        params,
    ).fetchall()
    total = conn.execute(
        """
        SELECT COUNT(*) c
        FROM (
          SELECT 1 FROM messages
          WHERE TRIM(COALESCE(thread, '')) != ''
          GROUP BY TRIM(thread)
        )
        """
    ).fetchone()["c"]
    return {
        "total_count": total,
        "returned": len(rows),
        "offset": safe_offset if limit is not None else 0,
        "items": [dict(row) for row in rows],
    }


def stats_senders(limit: int | None = 50, offset: int = 0) -> dict[str, Any]:
    conn = db()
    safe_offset = _safe_int(offset, 0, minimum=0)
    params: list[Any] = []
    limit_sql = ""
    if limit is not None:
        safe_limit = _safe_int(limit, 50, minimum=1, maximum=500)
        limit_sql = " LIMIT ? OFFSET ?"
        params.extend([safe_limit, safe_offset])
    rows = conn.execute(
        """
        SELECT TRIM(sender) sender, MAX(is_self) is_self, COUNT(*) count
        FROM messages
        WHERE TRIM(COALESCE(sender, '')) != ''
        GROUP BY TRIM(sender)
        ORDER BY count DESC, sender COLLATE NOCASE ASC
        """ + limit_sql,
        params,
    ).fetchall()
    total = conn.execute(
        """
        SELECT COUNT(*) c
        FROM (
          SELECT 1 FROM messages
          WHERE TRIM(COALESCE(sender, '')) != ''
          GROUP BY TRIM(sender)
        )
        """
    ).fetchone()["c"]
    return {
        "total_count": total,
        "returned": len(rows),
        "offset": safe_offset if limit is not None else 0,
        "items": [{**dict(row), "is_self": True if row["is_self"] else None} for row in rows],
    }


def stats(
    thread_limit: int | None = 50,
    thread_offset: int = 0,
    sender_limit: int | None = 50,
    sender_offset: int = 0,
) -> dict[str, Any]:
    summary = stats_summary()
    threads = stats_threads(limit=thread_limit, offset=thread_offset)
    senders = stats_senders(limit=sender_limit, offset=sender_offset)
    return {
        **summary,
        "threads": threads["items"],
        "threads_page": {key: value for key, value in threads.items() if key != "items"},
        "senders": senders["items"],
        "senders_page": {key: value for key, value in senders.items() if key != "items"},
    }


def suggest_entities(query: str = "", limit: int = 10) -> dict[str, Any]:
    conn = db()
    safe_limit = _safe_int(limit, 10, minimum=1, maximum=50)
    query = _clean_filter_text(query)
    params: list[Any] = []
    where_thread = ""
    where_sender = ""
    if query:
        where_thread = "WHERE TRIM(COALESCE(thread, '')) != '' AND TRIM(thread) LIKE '%' || ? || '%' ESCAPE '\\'"
        where_sender = "WHERE TRIM(COALESCE(sender, '')) != '' AND TRIM(sender) LIKE '%' || ? || '%' ESCAPE '\\'"
        params.append(_escape_like(query))
    else:
        where_thread = "WHERE TRIM(COALESCE(thread, '')) != ''"
        where_sender = "WHERE TRIM(COALESCE(sender, '')) != ''"

    thread_rows = conn.execute(
        f"""
        SELECT 'thread' AS type, TRIM(thread) AS value, COUNT(*) AS count, NULL AS is_self
        FROM messages
        {where_thread}
        GROUP BY TRIM(thread)
        ORDER BY count DESC, value COLLATE NOCASE ASC
        LIMIT ?
        """,
        [*params, safe_limit],
    ).fetchall()

    sender_params = [_escape_like(query)] if query else []
    sender_rows = conn.execute(
        f"""
        SELECT 'sender' AS type, TRIM(sender) AS value, COUNT(*) AS count, MAX(is_self) AS is_self
        FROM messages
        {where_sender}
        GROUP BY TRIM(sender)
        ORDER BY count DESC, value COLLATE NOCASE ASC
        LIMIT ?
        """,
        [*sender_params, safe_limit],
    ).fetchall()

    items = [dict(row) for row in [*thread_rows, *sender_rows]]
    type_priority = {"thread": 0, "sender": 1}
    items.sort(
        key=lambda item: (
            -int(item["count"]),
            type_priority.get(str(item["type"]), 9),
            str(item["value"]).casefold(),
        )
    )
    for item in items:
        item["is_self"] = True if item["is_self"] else None

    return {
        "query": query,
        "items": items[:safe_limit],
    }


def get_all_messages_by_thread(threads: list[str] | None = None) -> dict[str, list[dict[str, Any]]]:
    if threads is None:
        rows = db().execute("SELECT * FROM messages ORDER BY thread, seq").fetchall()
    else:
        scoped_threads = list(dict.fromkeys(threads))
        if not scoped_threads:
            return {}
        rows = []
        for batch in _batched(scoped_threads):
            placeholders = ",".join("?" for _ in batch)
            rows.extend(
                db().execute(
                    f"SELECT * FROM messages WHERE thread IN ({placeholders}) ORDER BY thread, seq",
                    batch,
                ).fetchall()
            )
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        result[row["thread"]].append(dict(row))
    return dict(result)


def get_sessions(ids: list[int]) -> list[dict[str, Any]]:
    if not ids:
        return []
    rows: list[sqlite3.Row] = []
    conn = db()
    for batch in _batched(ids):
        placeholders = ",".join("?" for _ in batch)
        rows.extend(conn.execute(f"SELECT * FROM sessions WHERE session_id IN ({placeholders})", batch).fetchall())
    return [dict(row) for row in sorted(rows, key=lambda row: int(row["session_id"]))]


def get_all_sessions() -> list[dict[str, Any]]:
    rows = db().execute("SELECT * FROM sessions ORDER BY session_id").fetchall()
    return [dict(row) for row in rows]


def get_threads_for_message_id_prefixes(prefixes: list[str]) -> list[str]:
    cleaned = [prefix for prefix in dict.fromkeys(prefixes) if prefix]
    if not cleaned:
        return []

    conn = db()
    threads: set[str] = set()
    for prefix in cleaned:
        rows = conn.execute(
            """
            SELECT DISTINCT thread
            FROM messages
            WHERE id LIKE ? || '%' ESCAPE '\\'
            ORDER BY thread
            """,
            (_escape_like(prefix),),
        ).fetchall()
        threads.update(str(row["thread"]) for row in rows)
    return sorted(threads)


def get_session_ids_for_message_id_prefixes(prefixes: list[str]) -> list[int]:
    by_prefix = get_session_ids_by_message_id_prefixes(prefixes)
    return sorted({session_id for session_ids in by_prefix.values() for session_id in session_ids})


def get_session_ids_by_message_id_prefixes(prefixes: list[str]) -> dict[str, list[int]]:
    cleaned = [prefix for prefix in dict.fromkeys(prefixes) if prefix]
    if not cleaned:
        return {}

    conn = db()
    session_ids_by_prefix: dict[str, set[int]] = defaultdict(set)
    for prefix in cleaned:
        rows = conn.execute(
            """
            SELECT DISTINCT ms.session_id
            FROM messages m
            JOIN msg_session ms ON ms.msg_id = m.id
            WHERE m.id LIKE ? || '%' ESCAPE '\\'
            ORDER BY ms.session_id
            """,
            (_escape_like(prefix),),
        ).fetchall()
        session_ids_by_prefix[prefix].update(int(row["session_id"]) for row in rows)
    return {prefix: sorted(session_ids_by_prefix.get(prefix, set())) for prefix in cleaned if prefix in session_ids_by_prefix}


def get_session_index_status(session_ids: list[int]) -> dict[str, int | None]:
    return get_session_index_statuses({"_": session_ids})["_"]


def get_session_index_statuses(session_ids_by_key: dict[str, list[int]]) -> dict[str, dict[str, int | None]]:
    normalized: dict[str, list[int]] = {
        key: [int(session_id) for session_id in dict.fromkeys(session_ids)]
        for key, session_ids in session_ids_by_key.items()
    }
    vec_available = has_vec()
    empty_status = {"total": 0, "missing_summary": 0, "missing_embedding": 0 if vec_available else None}
    if not normalized:
        return {}

    ids = sorted({session_id for session_ids in normalized.values() for session_id in session_ids})
    if not ids:
        return {key: dict(empty_status) for key in normalized}

    conn = db()
    summary_by_session: dict[int, int] = {}
    missing_embedding_by_session: dict[int, int] = {}
    for batch in _batched(ids):
        placeholders = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"""
            SELECT session_id,
                   CASE WHEN summary IS NULL OR TRIM(summary) = '' THEN 1 ELSE 0 END missing_summary
            FROM sessions
            WHERE session_id IN ({placeholders})
            """,
            batch,
        ).fetchall()
        for row in rows:
            summary_by_session[int(row["session_id"])] = int(row["missing_summary"] or 0)
        if vec_available:
            vec_rows = conn.execute(
                f"""
                SELECT s.session_id,
                       CASE WHEN v.session_id IS NULL THEN 1 ELSE 0 END missing_embedding
                FROM sessions s
                LEFT JOIN {VECTOR_TABLE} v ON v.session_id = s.session_id
                WHERE s.session_id IN ({placeholders})
                """,
                batch,
            ).fetchall()
            for row in vec_rows:
                missing_embedding_by_session[int(row["session_id"])] = int(row["missing_embedding"] or 0)

    statuses: dict[str, dict[str, int | None]] = {}
    for key, session_ids in normalized.items():
        existing_session_ids = [session_id for session_id in session_ids if session_id in summary_by_session]
        statuses[key] = {
            "total": len(existing_session_ids),
            "missing_summary": sum(summary_by_session[session_id] for session_id in existing_session_ids),
            "missing_embedding": (
                sum(missing_embedding_by_session.get(session_id, 0) for session_id in existing_session_ids)
                if vec_available
                else None
            ),
        }
    return statuses


def get_recent_sessions(limit: int = 8, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    safe_limit = _safe_int(limit, 8, minimum=1, maximum=50)
    where, params = _session_filter_clauses(filters)
    cond = "WHERE " + " AND ".join(where) if where else ""
    rows = db().execute(
        f"""
        SELECT *
        FROM sessions s
        {cond}
        ORDER BY s.end_time DESC, s.session_id DESC
        LIMIT :limit
        """,
        {**params, "limit": safe_limit},
    ).fetchall()
    return [dict(row) for row in rows]


def get_session_ids_without_embedding(session_ids: list[int] | None = None) -> list[int]:
    if not has_vec():
        return []
    conn = db()
    if session_ids is None:
        rows = conn.execute(
            f"""
            SELECT s.session_id FROM sessions s
            LEFT JOIN {VECTOR_TABLE} v ON v.session_id = s.session_id
            WHERE v.session_id IS NULL
            """
        ).fetchall()
        return [int(row["session_id"]) for row in rows]

    ids = [int(session_id) for session_id in dict.fromkeys(session_ids)]
    missing: list[int] = []
    for batch in _batched(ids):
        placeholders = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"""
            SELECT s.session_id FROM sessions s
            LEFT JOIN {VECTOR_TABLE} v ON v.session_id = s.session_id
            WHERE s.session_id IN ({placeholders})
              AND v.session_id IS NULL
            """,
            batch,
        ).fetchall()
        missing.extend(int(row["session_id"]) for row in rows)
    return missing


def get_all_session_ids_without_embedding() -> list[int]:
    return get_session_ids_without_embedding()


def count_sessions_without_embedding() -> int:
    if not has_vec():
        return 0
    return int(
        db().execute(
            f"""
            SELECT COUNT(*) c FROM sessions s
            LEFT JOIN {VECTOR_TABLE} v ON v.session_id = s.session_id
            WHERE v.session_id IS NULL
            """
        ).fetchone()["c"]
    )


def _session_filter_clauses(filters: dict[str, Any] | None) -> tuple[list[str], dict[str, Any]]:
    if not filters:
        return [], {}

    where: list[str] = []
    params: dict[str, Any] = {}
    thread = _clean_filter_text(filters.get("thread"))
    if thread:
        where.append("s.thread LIKE '%' || :session_thread || '%' ESCAPE '\\'")
        params["session_thread"] = _escape_like(thread)
    after = filters.get("after")
    if after:
        normalized_after = _norm_time(after, False)
        if normalized_after:
            where.append("s.end_time >= :session_after")
            params["session_after"] = normalized_after
    before = filters.get("before")
    if before:
        normalized_before = _norm_time(before, True)
        if normalized_before:
            where.append("s.start_time <= :session_before")
            params["session_before"] = normalized_before
    return where, params


def fts_search_sessions(query: str, limit: int, filters: dict[str, Any] | None = None) -> list[dict[str, int]]:
    conn = db()
    safe_limit = _safe_int(limit, 20, minimum=1, maximum=500)
    message_limit = max(200, safe_limit * 20)
    terms, _query_truncated = _query_terms(query)
    if not terms:
        return []
    use_fts = all(len(term) >= 3 for term in terms)
    session_where, session_params = _session_filter_clauses(filters)

    if session_where:
        if use_fts:
            match = " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)
            cond = " AND ".join(["messages_fts MATCH :match", *session_where])
            rows = conn.execute(
                f"""
                SELECT ms.session_id, COUNT(*) c, MAX(m.timestamp) last_time
                FROM messages_fts
                JOIN messages m ON m.rowid = messages_fts.rowid
                JOIN msg_session ms ON ms.msg_id = m.id
                JOIN sessions s ON s.session_id = ms.session_id
                WHERE {cond}
                GROUP BY ms.session_id
                ORDER BY c DESC, last_time DESC, ms.session_id DESC
                LIMIT :limit
                """,
                {**session_params, "match": match, "limit": safe_limit},
            ).fetchall()
        else:
            likes: list[str] = []
            params: dict[str, Any] = {**session_params, "limit": safe_limit}
            for idx, term in enumerate(terms):
                likes.append(f"m.content LIKE '%' || :term{idx} || '%' ESCAPE '\\'")
                params[f"term{idx}"] = _escape_like(term)
            cond = " AND ".join([f"({' OR '.join(likes)})", *session_where])
            rows = conn.execute(
                f"""
                SELECT ms.session_id, COUNT(*) c, MAX(m.timestamp) last_time
                FROM messages m
                JOIN msg_session ms ON ms.msg_id = m.id
                JOIN sessions s ON s.session_id = ms.session_id
                WHERE {cond}
                GROUP BY ms.session_id
                ORDER BY c DESC, last_time DESC, ms.session_id DESC
                LIMIT :limit
                """,
                params,
            ).fetchall()
        return [{"sessionId": int(row["session_id"])} for row in rows]

    if use_fts:
        match = " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)
        msg_rows = conn.execute(
            """
            SELECT m.id FROM messages_fts JOIN messages m ON m.rowid = messages_fts.rowid
            WHERE messages_fts MATCH ? ORDER BY bm25(messages_fts), m.timestamp DESC, m.id DESC LIMIT ?
            """,
            (match, message_limit),
        ).fetchall()
    else:
        likes = " OR ".join("content LIKE '%' || ? || '%' ESCAPE '\\'" for _ in terms)
        msg_rows = conn.execute(
            f"SELECT id FROM messages WHERE {likes} ORDER BY timestamp DESC, id DESC LIMIT ?",
            [*[_escape_like(term) for term in terms], message_limit],
        ).fetchall()

    if not msg_rows:
        return []

    session_hits: dict[int, tuple[int, str]] = {}
    msg_ids = [row["id"] for row in msg_rows]
    for batch in _batched(msg_ids):
        placeholders = ",".join("?" for _ in batch)
        rows = conn.execute(
            f"""
            SELECT ms.session_id, COUNT(*) c, MAX(s.end_time) last_time
            FROM msg_session ms
            JOIN sessions s ON s.session_id = ms.session_id
            WHERE ms.msg_id IN ({placeholders})
            GROUP BY ms.session_id
            """,
            batch,
        ).fetchall()
        for row in rows:
            session_id = int(row["session_id"])
            count, last_time = session_hits.get(session_id, (0, ""))
            session_hits[session_id] = (
                count + int(row["c"] or 0),
                max(last_time, str(row["last_time"] or "")),
            )

    ranked = sorted(
        session_hits.items(),
        key=lambda item: (item[1][0], item[1][1], item[0]),
        reverse=True,
    )[:safe_limit]
    return [{"sessionId": session_id} for session_id, _hit in ranked]


def vector_search_sessions(
    query_vec: list[float],
    limit: int,
    filters: dict[str, Any] | None = None,
) -> list[dict[str, int]]:
    if not has_vec():
        return []
    safe_limit = _safe_int(limit, 20, minimum=1, maximum=500)
    query_blob = array.array("f", query_vec).tobytes()
    session_where, session_params = _session_filter_clauses(filters)
    conn = db()
    if session_where:
        cond = " AND ".join(session_where)
        rows = conn.execute(
            f"""
            SELECT session_id, distance
            FROM {VECTOR_TABLE}
            WHERE embedding MATCH :embedding
              AND k = :limit
              AND session_id IN (
                SELECT s.session_id
                FROM sessions s
                WHERE {cond}
              )
            ORDER BY distance
            """,
            {**session_params, "embedding": query_blob, "limit": safe_limit},
        ).fetchall()
    else:
        rows = conn.execute(
            f"""
            SELECT session_id, distance FROM {VECTOR_TABLE}
            WHERE embedding MATCH ? AND k = ? ORDER BY distance
            """,
            (query_blob, safe_limit),
        ).fetchall()
    return [{"sessionId": int(row["session_id"])} for row in rows]
