from __future__ import annotations

import array
import hashlib
import json
import os
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from dotenv import load_dotenv

from .llm import EMBED_DIM
from .parser import NormMessage


load_dotenv()

SQL_BATCH = 900


def chunk_text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _batched(items: list[Any], size: int = SQL_BATCH) -> Iterable[list[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]

DB_PATH = os.getenv("CHAT_DB", str(Path("runtime") / "chat.db"))

_conn: sqlite3.Connection | None = None
_vec_available = False
VECTOR_TABLE = "sessions_vec"


def _sqlite_path(path: str) -> str:
    if path == ":memory:" or path.startswith("file:"):
        return path
    db_path = Path(path).expanduser()
    if db_path.parent != Path("."):
        db_path.parent.mkdir(parents=True, exist_ok=True)
    return str(db_path)


def db() -> sqlite3.Connection:
    global _conn, _vec_available
    if _conn is not None:
        return _conn

    _conn = sqlite3.connect(_sqlite_path(DB_PATH), timeout=30)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode = WAL")
    _conn.execute("PRAGMA busy_timeout = 30000")
    # WAL 模式下 NORMAL 已能保证崩溃一致性，且大幅减少每次 commit 的 fsync 开销
    _conn.execute("PRAGMA synchronous = NORMAL")
    _conn.execute("PRAGMA cache_size = -64000")

    try:
        import sqlite_vec

        _conn.enable_load_extension(True)
        sqlite_vec.load(_conn)
        _conn.enable_load_extension(False)
        _vec_available = True
    except Exception as exc:
        print(f"[警告] sqlite-vec 加载失败，语义检索退化为全文检索：{exc}")
        _vec_available = False

    init_schema(_conn)
    return _conn


def has_vec() -> bool:
    db()
    return _vec_available


def init_schema(conn: sqlite3.Connection) -> None:
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
          updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    _migrate_sessions_text_hash(conn)
    create_messages_fts(conn, if_not_exists=True)
    if _vec_available:
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


def insert_messages(messages: list[NormMessage]) -> int:
    conn = db()
    before = conn.total_changes
    rows = [
        (m.id, m.sender, m.is_self, m.timestamp, m.content, m.msg_type, m.thread, m.reply_to)
        for m in messages
    ]
    conn.executemany(
        """
        INSERT OR IGNORE INTO messages (id, sender, is_self, timestamp, content, msg_type, thread, reply_to)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    return conn.total_changes - before


def ingest_file_unchanged(path: str, size: int, mtime_ns: int) -> bool:
    row = db().execute(
        "SELECT size, mtime_ns FROM ingest_files WHERE path = ?",
        (path,),
    ).fetchone()
    return bool(row and row["size"] == size and row["mtime_ns"] == mtime_ns)


def record_ingest_file(path: str, size: int, mtime_ns: int, total: int, included: int, inserted: int) -> None:
    db().execute(
        """
        INSERT INTO ingest_files (path, size, mtime_ns, total, included, inserted, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(path) DO UPDATE SET
          size = excluded.size,
          mtime_ns = excluded.mtime_ns,
          total = excluded.total,
          included = excluded.included,
          inserted = excluded.inserted,
          updated_at = CURRENT_TIMESTAMP
        """,
        (path, size, mtime_ns, total, included, inserted),
    )
    db().commit()


def recompute_message_sequence(threads: list[str] | None = None) -> None:
    conn = db()
    scope = ""
    params: list[Any] = []
    if threads is not None:
        if not threads:
            return
        placeholders = ",".join("?" for _ in threads)
        scope = f"WHERE thread IN ({placeholders})"
        params = list(threads)
    # UPDATE...FROM 走 messages 主键索引联接，复杂度 O(n log n)；
    # seq IS NOT rn 跳过已正确的行，追加场景几乎只写新消息。
    conn.execute(
        f"""
        UPDATE messages
        SET seq = ranked.rn
        FROM (
          SELECT id, ROW_NUMBER() OVER (PARTITION BY thread ORDER BY timestamp, id) AS rn
          FROM messages {scope}
        ) AS ranked
        WHERE messages.id = ranked.id AND messages.seq IS NOT ranked.rn
        """,
        params,
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


def count_messages_missing_seq() -> int:
    return int(db().execute("SELECT COUNT(*) c FROM messages WHERE seq IS NULL").fetchone()["c"])


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
    if _vec_available and rows:
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
            if _vec_available:
                conn.execute(f"DELETE FROM {VECTOR_TABLE}")
        elif threads:
            placeholders = ",".join("?" for _ in threads)
            old_ids = [
                int(row["session_id"])
                for row in conn.execute(
                    f"SELECT session_id FROM sessions WHERE thread IN ({placeholders})", list(threads)
                )
            ]
            for batch in _batched(old_ids):
                id_marks = ",".join("?" for _ in batch)
                conn.execute(f"DELETE FROM msg_session WHERE session_id IN ({id_marks})", batch)
                if _vec_available:
                    conn.execute(f"DELETE FROM {VECTOR_TABLE} WHERE session_id IN ({id_marks})", batch)
            conn.execute(f"DELETE FROM sessions WHERE thread IN ({placeholders})", list(threads))

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
    deduped: dict[int, bytes] = {}
    for session_id, blob in payload:
        deduped[session_id] = blob
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
    return int(db().execute("SELECT COUNT(*) c FROM sessions WHERE summary IS NULL").fetchone()["c"])


def get_session_ids_with_embedding() -> set[int]:
    if not has_vec():
        return set()
    rows = db().execute(f"SELECT session_id FROM {VECTOR_TABLE}").fetchall()
    return {int(row["session_id"]) for row in rows}


def _norm_time(value: str, is_before: bool) -> str:
    value = value.strip().replace(" ", "T")
    if len(value) == 10:
        return f"{value}T23:59:59" if is_before else f"{value}T00:00:00"
    return value


def _filter_clauses(filters: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    where: list[str] = []
    params: dict[str, Any] = {}

    sender = filters.get("sender")
    if sender:
        if sender == "我":
            where.append("m.is_self = 1")
        else:
            where.append("m.sender LIKE '%' || :sender || '%'")
            params["sender"] = sender

    thread = filters.get("thread")
    if thread:
        where.append("m.thread LIKE '%' || :thread || '%'")
        params["thread"] = thread

    after = filters.get("after")
    if after:
        where.append("m.timestamp >= :after")
        params["after"] = _norm_time(after, False)

    before = filters.get("before")
    if before:
        where.append("m.timestamp <= :before")
        params["before"] = _norm_time(before, True)

    return where, params


def _truncate(text: str, limit: int = 200) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


def _to_api_msg(row: sqlite3.Row | dict[str, Any]) -> dict[str, Any]:
    row = dict(row)
    return {
        "message_id": row["id"],
        "sender": f"{row['sender']}(我)" if row["is_self"] else row["sender"],
        "time": row["timestamp"].replace("T", " "),
        "content": _truncate(row["content"]),
        "type": row["msg_type"],
        "thread": row["thread"],
    }


def search_messages(args: dict[str, Any]) -> dict[str, Any]:
    conn = db()
    limit = min(int(args.get("limit") or 20), 100)
    offset = int(args.get("offset") or 0)
    where, params = _filter_clauses(args)

    terms = [term for term in str(args["query"]).strip().split() if term]
    use_fts = bool(terms) and all(len(term) >= 3 for term in terms)

    if use_fts:
        match = " AND ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)
        cond = " AND ".join(["messages_fts MATCH :match", *where])
        base = f"FROM messages_fts JOIN messages m ON m.rowid = messages_fts.rowid WHERE {cond}"
        all_params = {**params, "match": match, "limit": limit, "offset": offset}
        total = conn.execute(f"SELECT COUNT(*) c {base}", all_params).fetchone()["c"]
        rows = conn.execute(f"SELECT m.* {base} ORDER BY m.timestamp LIMIT :limit OFFSET :offset", all_params).fetchall()
    else:
        likes = []
        for idx, term in enumerate(terms):
            likes.append(f"m.content LIKE '%' || :q{idx} || '%'")
            params[f"q{idx}"] = term
        cond = " AND ".join([*likes, *where]) or "1=1"
        base = f"FROM messages m WHERE {cond}"
        all_params = {**params, "limit": limit, "offset": offset}
        total = conn.execute(f"SELECT COUNT(*) c {base}", all_params).fetchone()["c"]
        rows = conn.execute(f"SELECT m.* {base} ORDER BY m.timestamp LIMIT :limit OFFSET :offset", all_params).fetchall()

    return {
        "total_count": total,
        "returned": len(rows),
        "offset": offset,
        "messages": [_to_api_msg(row) for row in rows],
    }


def get_context(args: dict[str, Any]) -> dict[str, Any]:
    conn = db()
    center = conn.execute("SELECT * FROM messages WHERE id = ?", (args["message_id"],)).fetchone()
    if center is None:
        return {"error": f"查无此消息 id：{args['message_id']}"}

    before = min(int(args.get("before") or 15), 50)
    after = min(int(args.get("after") or 15), 50)
    rows = conn.execute(
        "SELECT * FROM messages WHERE thread = ? AND seq BETWEEN ? AND ? ORDER BY seq",
        (center["thread"], center["seq"] - before, center["seq"] + after),
    ).fetchall()

    quoted: dict[str, Any] | None = None
    if center["reply_to"]:
        quoted_row = conn.execute("SELECT * FROM messages WHERE id = ?", (center["reply_to"],)).fetchone()
        quoted = (
            _to_api_msg(quoted_row)
            if quoted_row
            else {"note": "被引消息不在库中（可能是图片/表情等未入库类型），content 中的内联引用文字可作参考"}
        )

    return {
        "thread": center["thread"],
        "center_message_id": center["id"],
        "quoted_message": quoted,
        "messages": [
            {**_to_api_msg(row), **({"is_center": True} if row["id"] == center["id"] else {})}
            for row in rows
        ],
    }


def browse(args: dict[str, Any]) -> dict[str, Any]:
    conn = db()
    limit = min(int(args.get("limit") or 50), 200)
    offset = int(args.get("offset") or 0)
    where, params = _filter_clauses(args)
    cond = " AND ".join(where) or "1=1"
    base = f"FROM messages m WHERE {cond}"
    all_params = {**params, "limit": limit, "offset": offset}
    total = conn.execute(f"SELECT COUNT(*) c {base}", all_params).fetchone()["c"]
    rows = conn.execute(f"SELECT m.* {base} ORDER BY m.timestamp LIMIT :limit OFFSET :offset", all_params).fetchall()
    return {"total_count": total, "returned": len(rows), "offset": offset, "messages": [_to_api_msg(row) for row in rows]}


def stats() -> dict[str, Any]:
    conn = db()
    overall = conn.execute("SELECT COUNT(*) total, MIN(timestamp) earliest, MAX(timestamp) latest FROM messages").fetchone()
    threads = conn.execute(
        """
        SELECT thread, COUNT(*) count, MIN(timestamp) earliest, MAX(timestamp) latest
        FROM messages GROUP BY thread ORDER BY count DESC
        """
    ).fetchall()
    senders = conn.execute(
        """
        SELECT sender, MAX(is_self) is_self, COUNT(*) count
        FROM messages GROUP BY sender ORDER BY count DESC LIMIT 50
        """
    ).fetchall()
    types = conn.execute("SELECT msg_type, COUNT(*) count FROM messages GROUP BY msg_type ORDER BY count DESC").fetchall()
    session_count = conn.execute("SELECT COUNT(*) c FROM sessions").fetchone()["c"]
    return {
        "total_messages": overall["total"],
        "time_span": {"earliest": overall["earliest"], "latest": overall["latest"]},
        "threads": [dict(row) for row in threads],
        "senders": [{**dict(row), "is_self": True if row["is_self"] else None} for row in senders],
        "message_types": [dict(row) for row in types],
        "indexed_session_chunks": session_count,
    }


def get_all_messages_by_thread(threads: list[str] | None = None) -> dict[str, list[dict[str, Any]]]:
    if threads is None:
        rows = db().execute("SELECT * FROM messages ORDER BY thread, seq").fetchall()
    else:
        if not threads:
            return {}
        placeholders = ",".join("?" for _ in threads)
        rows = db().execute(
            f"SELECT * FROM messages WHERE thread IN ({placeholders}) ORDER BY thread, seq",
            list(threads),
        ).fetchall()
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        result[row["thread"]].append(dict(row))
    return dict(result)


def get_sessions(ids: list[int]) -> list[dict[str, Any]]:
    if not ids:
        return []
    placeholders = ",".join("?" for _ in ids)
    rows = db().execute(f"SELECT * FROM sessions WHERE session_id IN ({placeholders})", ids).fetchall()
    return [dict(row) for row in rows]


def get_all_sessions() -> list[dict[str, Any]]:
    rows = db().execute("SELECT * FROM sessions ORDER BY session_id").fetchall()
    return [dict(row) for row in rows]


def get_all_session_ids_without_embedding() -> list[int]:
    if not has_vec():
        return []
    rows = db().execute(
        """
        SELECT s.session_id FROM sessions s
        LEFT JOIN sessions_vec v ON v.session_id = s.session_id
        WHERE v.session_id IS NULL
        """
    ).fetchall()
    return [int(row["session_id"]) for row in rows]


def fts_search_sessions(query: str, limit: int) -> list[dict[str, int]]:
    conn = db()
    terms = [term for term in query.strip().split() if term]
    if not terms:
        return []
    use_fts = all(len(term) >= 3 for term in terms)

    if use_fts:
        match = " OR ".join(f'"{term.replace(chr(34), chr(34) * 2)}"' for term in terms)
        msg_rows = conn.execute(
            """
            SELECT m.id FROM messages_fts JOIN messages m ON m.rowid = messages_fts.rowid
            WHERE messages_fts MATCH ? LIMIT 200
            """,
            (match,),
        ).fetchall()
    else:
        likes = " OR ".join("content LIKE '%' || ? || '%'" for _ in terms)
        msg_rows = conn.execute(f"SELECT id FROM messages WHERE {likes} LIMIT 200", terms).fetchall()

    if not msg_rows:
        return []

    msg_ids = [row["id"] for row in msg_rows]
    placeholders = ",".join("?" for _ in msg_ids)
    rows = conn.execute(
        f"""
        SELECT session_id, COUNT(*) c FROM msg_session WHERE msg_id IN ({placeholders})
        GROUP BY session_id ORDER BY c DESC LIMIT ?
        """,
        [*msg_ids, limit],
    ).fetchall()
    return [{"sessionId": int(row["session_id"])} for row in rows]


def vector_search_sessions(query_vec: list[float], limit: int) -> list[dict[str, int]]:
    if not has_vec():
        return []
    rows = db().execute(
        """
        SELECT session_id, distance FROM sessions_vec
        WHERE embedding MATCH ? AND k = ? ORDER BY distance
        """,
        (array.array("f", query_vec).tobytes(), int(limit)),
    ).fetchall()
    return [{"sessionId": int(row["session_id"])} for row in rows]
