from __future__ import annotations

import array
import json
import os
import re
import sqlite3
from collections import defaultdict
from dataclasses import asdict
from typing import Any

from dotenv import load_dotenv

from .llm import EMBED_DIM
from .parser import NormMessage


load_dotenv()

DB_PATH = os.getenv("CHAT_DB", "chat.db")

_conn: sqlite3.Connection | None = None
_vec_available = False
VECTOR_TABLE = "sessions_vec"


def db() -> sqlite3.Connection:
    global _conn, _vec_available
    if _conn is not None:
        return _conn

    _conn = sqlite3.connect(DB_PATH)
    _conn.row_factory = sqlite3.Row
    _conn.execute("PRAGMA journal_mode = WAL")

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

        CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts
          USING fts5(content, content=messages, content_rowid=rowid, tokenize='trigram');

        CREATE TABLE IF NOT EXISTS sessions (
          session_id   INTEGER PRIMARY KEY,
          thread       TEXT NOT NULL,
          start_time   TEXT NOT NULL,
          end_time     TEXT NOT NULL,
          participants TEXT NOT NULL,
          msg_ids      TEXT NOT NULL,
          text         TEXT NOT NULL,
          summary      TEXT
        );

        CREATE TABLE IF NOT EXISTS msg_session (
          msg_id     TEXT PRIMARY KEY,
          session_id INTEGER NOT NULL
        );
        """
    )
    if _vec_available:
        create_vector_table(conn, EMBED_DIM, if_not_exists=True)
    conn.commit()


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
    rows = [asdict(message) for message in messages]
    conn.executemany(
        """
        INSERT OR IGNORE INTO messages (id, sender, is_self, timestamp, content, msg_type, thread, reply_to)
        VALUES (:id, :sender, :is_self, :timestamp, :content, :msg_type, :thread, :reply_to)
        """,
        rows,
    )
    conn.commit()
    return conn.total_changes - before


def finalize_ingest() -> None:
    conn = db()
    conn.executescript(
        """
        WITH ranked AS (
          SELECT id, ROW_NUMBER() OVER (PARTITION BY thread ORDER BY timestamp, id) AS rn
          FROM messages
        )
        UPDATE messages
        SET seq = (SELECT rn FROM ranked WHERE ranked.id = messages.id)
        WHERE id IN (SELECT id FROM ranked);

        INSERT INTO messages_fts(messages_fts) VALUES('rebuild');
        """
    )
    conn.commit()


def replace_sessions(rows: list[Any]) -> list[int]:
    conn = db()
    ids: list[int] = []
    with conn:
        conn.execute("DELETE FROM sessions")
        conn.execute("DELETE FROM msg_session")
        if _vec_available:
            conn.execute(f"DELETE FROM {VECTOR_TABLE}")

        for row in rows:
            data = row.__dict__ if hasattr(row, "__dict__") else dict(row)
            cur = conn.execute(
                """
                INSERT INTO sessions (thread, start_time, end_time, participants, msg_ids, text, summary)
                VALUES (:thread, :start_time, :end_time, :participants, :msg_ids, :text, :summary)
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
    if not has_vec():
        raise RuntimeError("sqlite-vec 不可用")
    dimensions = {len(item["embedding"]) for item in items}
    if len(dimensions) > 1:
        raise RuntimeError(f"同一批 embedding 维度不一致：{sorted(dimensions)}")
    dimension = next(iter(dimensions), None)
    expected = vector_table_dimension()
    if dimension is not None and expected is not None and dimension != expected:
        raise RuntimeError(
            f"embedding 维度不匹配：sessions_vec 期望 {expected} 维，但本批是 {dimension} 维。"
            "请重跑 ingest，程序会自动重建向量表。"
        )
    conn = db()
    with conn:
        conn.executemany(
            f"INSERT OR REPLACE INTO {VECTOR_TABLE} (session_id, embedding) VALUES (?, ?)",
            [
                (int(item["session_id"]), array.array("f", item["embedding"]).tobytes())
                for item in items
            ],
        )


def set_summary(session_id: int, summary: str) -> None:
    db().execute("UPDATE sessions SET summary = ? WHERE session_id = ?", (summary, session_id))
    db().commit()


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


def get_all_messages_by_thread() -> dict[str, list[dict[str, Any]]]:
    rows = db().execute("SELECT * FROM messages ORDER BY thread, seq").fetchall()
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
