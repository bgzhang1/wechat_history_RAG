"""Persistent storage for backend chat sessions."""

from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv

load_dotenv()
DB_PATH = os.getenv("BACKEND_CHAT_DB", str(Path("runtime") / "backend_chat.db"))
SQL_BATCH = 900

SessionStatus = Literal["idle", "running", "aborting", "error"]

_conn: sqlite3.Connection | None = None
_lock = threading.RLock()


class ActiveSessionError(RuntimeError):
    """Raised when mutating metadata for a session that is currently generating."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _sqlite_path(path: str) -> str:
    if path == ":memory:" or path.startswith("file:"):
        return path
    db_path = Path(path).expanduser()
    if db_path.parent != Path("."):
        db_path.parent.mkdir(parents=True, exist_ok=True)
    return str(db_path)


def _sqlite_uri(path: str) -> bool:
    return path.startswith("file:")


def _safe_int(value: Any, default: int, minimum: int, maximum: int | None = None) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        parsed = default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _batched(items: list[Any], size: int | None = None) -> list[list[Any]]:
    batch_size = max(1, size or SQL_BATCH)
    return [items[start : start + batch_size] for start in range(0, len(items), batch_size)]


def db() -> sqlite3.Connection:
    global _conn
    with _lock:
        if _conn is not None:
            return _conn
        _conn = sqlite3.connect(_sqlite_path(DB_PATH), timeout=30, check_same_thread=False, uri=_sqlite_uri(DB_PATH))
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA foreign_keys = ON")
        _conn.execute("PRAGMA journal_mode = WAL")
        _conn.execute("PRAGMA busy_timeout = 30000")
        init_schema(_conn)
        return _conn


def close_connection() -> None:
    global _conn
    with _lock:
        if _conn is not None:
            _conn.close()
            _conn = None


def init_schema(conn: sqlite3.Connection | None = None) -> None:
    conn = conn or db()
    with _lock:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS backend_chat_sessions (
              session_id TEXT PRIMARY KEY,
              title      TEXT,
              status     TEXT NOT NULL DEFAULT 'idle',
              last_error TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS backend_chat_messages (
              id         INTEGER PRIMARY KEY AUTOINCREMENT,
              session_id TEXT NOT NULL,
              role       TEXT NOT NULL,
              content    TEXT NOT NULL,
              created_at TEXT NOT NULL,
              FOREIGN KEY(session_id) REFERENCES backend_chat_sessions(session_id)
                ON DELETE CASCADE
            );

            CREATE INDEX IF NOT EXISTS idx_backend_chat_messages_session
              ON backend_chat_messages(session_id, id);
            CREATE INDEX IF NOT EXISTS idx_backend_chat_sessions_updated
              ON backend_chat_sessions(updated_at);
            """
        )
        conn.execute(
            """
            UPDATE backend_chat_sessions
            SET status = 'idle', last_error = 'generation interrupted by backend restart'
            WHERE status IN ('running', 'aborting')
            """
        )
        conn.commit()


def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return dict(row)


def _make_title(question: str) -> str:
    title = " ".join(question.strip().split())
    if not title:
        return "New chat"
    return title[:48]


def get_session(session_id: str) -> dict[str, Any] | None:
    with _lock:
        row = db().execute(
            """
            SELECT session_id, title, status, last_error, created_at, updated_at
            FROM backend_chat_sessions
            WHERE session_id = ?
            """,
            (session_id,),
        ).fetchone()
        return _row_to_dict(row) if row else None


def get_or_create_session(session_id: str | None = None) -> dict[str, Any]:
    with _lock:
        if session_id:
            existing = get_session(session_id)
            if existing is not None:
                return existing

        sid = session_id or str(uuid.uuid4())
        now = _now()
        db().execute(
            """
            INSERT INTO backend_chat_sessions
              (session_id, title, status, last_error, created_at, updated_at)
            VALUES (?, NULL, 'idle', NULL, ?, ?)
            """,
            (sid, now, now),
        )
        db().commit()
        return get_session(sid) or {
            "session_id": sid,
            "title": None,
            "status": "idle",
            "last_error": None,
            "created_at": now,
            "updated_at": now,
        }


def list_sessions(limit: int = 100, offset: int = 0) -> list[dict[str, Any]]:
    limit = _safe_int(limit, 100, minimum=1, maximum=200)
    offset = _safe_int(offset, 0, minimum=0)
    with _lock:
        rows = db().execute(
            """
            SELECT
              s.session_id,
              s.title,
              s.status,
              s.last_error,
              s.created_at,
              s.updated_at,
              (
                SELECT COUNT(*)
                FROM backend_chat_messages m
                WHERE m.session_id = s.session_id
              ) AS message_count,
              (
                SELECT m.content
                FROM backend_chat_messages m
                WHERE m.session_id = s.session_id AND m.role = 'user'
                ORDER BY m.id DESC
                LIMIT 1
              ) AS last_question
            FROM backend_chat_sessions s
            ORDER BY s.updated_at DESC, s.created_at DESC, s.session_id DESC
            LIMIT ? OFFSET ?
            """,
            (limit, offset),
        ).fetchall()
        return [_row_to_dict(row) for row in rows]


def count_sessions() -> int:
    with _lock:
        return int(db().execute("SELECT COUNT(*) c FROM backend_chat_sessions").fetchone()["c"])


def rename_session(session_id: str, title: str) -> dict[str, Any] | None:
    title = " ".join(title.strip().split())
    if not title:
        raise ValueError("标题不能为空。")
    now = _now()
    with _lock:
        current = get_session(session_id)
        if current is None:
            return None
        if current["status"] in {"running", "aborting"}:
            raise ActiveSessionError("session is generating")
        db().execute(
            """
            UPDATE backend_chat_sessions
            SET title = ?, updated_at = ?
            WHERE session_id = ?
            """,
            (title[:120], now, session_id),
        )
        db().commit()
        return get_session(session_id)


def get_messages(session_id: str, limit: int | None = None, offset: int = 0) -> list[dict[str, Any]]:
    with _lock:
        if limit is None:
            rows = db().execute(
                """
                SELECT id, role, content, created_at
                FROM backend_chat_messages
                WHERE session_id = ?
                ORDER BY id
                """,
                (session_id,),
            ).fetchall()
        else:
            safe_limit = _safe_int(limit, 0, minimum=0)
            if safe_limit == 0:
                return []
            safe_offset = _safe_int(offset, 0, minimum=0)
            rows = db().execute(
                """
                SELECT id, role, content, created_at
                FROM (
                  SELECT id, role, content, created_at
                  FROM backend_chat_messages
                  WHERE session_id = ?
                  ORDER BY id DESC
                  LIMIT ? OFFSET ?
                )
                ORDER BY id
                """,
                (session_id, safe_limit, safe_offset),
            ).fetchall()
        return [_row_to_dict(row) for row in rows]


def count_messages(session_id: str) -> int:
    with _lock:
        return int(
            db().execute(
                "SELECT COUNT(*) c FROM backend_chat_messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()["c"]
        )


def last_exchange_matches(
    session_id: str,
    question: str,
    *,
    answer: str | None = None,
    answer_contains: str | None = None,
) -> bool:
    normalized_question = question.strip()
    with _lock:
        rows = db().execute(
            """
            SELECT role, content
            FROM backend_chat_messages
            WHERE session_id = ?
            ORDER BY id DESC
            LIMIT 2
            """,
            (session_id,),
        ).fetchall()

    if len(rows) != 2:
        return False

    assistant_row, user_row = rows[0], rows[1]
    if assistant_row["role"] != "assistant" or user_row["role"] != "user":
        return False
    if user_row["content"].strip() != normalized_question:
        return False
    if answer is not None and assistant_row["content"] != answer:
        return False
    return not (answer_contains is not None and answer_contains not in assistant_row["content"])


def append_exchange(session_id: str, question: str, answer: str, final_status: SessionStatus = "idle") -> None:
    now = _now()
    with _lock:
        get_or_create_session(session_id)
        conn = db()
        with conn:
            conn.executemany(
                """
                INSERT INTO backend_chat_messages (session_id, role, content, created_at)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (session_id, "user", question, now),
                    (session_id, "assistant", answer, now),
                ],
            )
            conn.execute(
                """
                UPDATE backend_chat_sessions
                SET
                  title = COALESCE(title, ?),
                  status = ?,
                  last_error = NULL,
                  updated_at = ?
                WHERE session_id = ?
                """,
                (_make_title(question), final_status, now, session_id),
            )


def try_begin(session_id: str) -> bool:
    now = _now()
    with _lock:
        get_or_create_session(session_id)
        current = get_session(session_id)
        if current and current["status"] in {"running", "aborting"}:
            return False
        db().execute(
            """
            UPDATE backend_chat_sessions
            SET status = 'running', last_error = NULL, updated_at = ?
            WHERE session_id = ?
            """,
            (now, session_id),
        )
        db().commit()
        return True


def request_abort(session_id: str) -> str:
    now = _now()
    with _lock:
        current = get_session(session_id)
        if current is None:
            return "missing"
        if current["status"] not in {"running", "aborting"}:
            return "idle"
        if current["status"] == "aborting":
            return "aborting"
        db().execute(
            """
            UPDATE backend_chat_sessions
            SET status = 'aborting', updated_at = ?
            WHERE session_id = ?
            """,
            (now, session_id),
        )
        db().commit()
        return "running"


def finish(session_id: str, status: SessionStatus = "idle", last_error: str | None = None) -> None:
    now = _now()
    with _lock:
        if get_session(session_id) is None:
            return
        db().execute(
            """
            UPDATE backend_chat_sessions
            SET status = ?, last_error = ?, updated_at = ?
            WHERE session_id = ?
            """,
            (status, last_error, now, session_id),
        )
        db().commit()


def delete_session_result(session_id: str) -> Literal["deleted", "missing", "active"]:
    with _lock:
        conn = db()
        current = get_session(session_id)
        if current is None:
            return "missing"
        if current["status"] in {"running", "aborting"}:
            return "active"
        with conn:
            conn.execute(
                "DELETE FROM backend_chat_sessions WHERE session_id = ?",
                (session_id,),
            )
            conn.execute(
                "DELETE FROM backend_chat_messages WHERE session_id = ?",
                (session_id,),
            )
        return "deleted"


def delete_session(session_id: str) -> bool:
    return delete_session_result(session_id) == "deleted"


def delete_sessions(session_ids: list[str]) -> dict[str, Any]:
    unique_ids = list(dict.fromkeys(session_ids))
    if not unique_ids:
        return {"deleted": [], "missing": [], "active": []}

    with _lock:
        conn = db()
        existing: set[str] = set()
        active: set[str] = set()
        for batch in _batched(unique_ids):
            placeholders = ",".join("?" for _ in batch)
            existing_rows = conn.execute(
                f"""
                SELECT session_id
                FROM backend_chat_sessions
                WHERE session_id IN ({placeholders})
                """,
                batch,
            ).fetchall()
            existing.update(str(row["session_id"]) for row in existing_rows)
            active_rows = conn.execute(
                f"""
                SELECT session_id
                FROM backend_chat_sessions
                WHERE session_id IN ({placeholders})
                  AND status IN ('running', 'aborting')
                """,
                batch,
            ).fetchall()
            active.update(str(row["session_id"]) for row in active_rows)
        missing = [session_id for session_id in unique_ids if session_id not in existing]
        deletable = [session_id for session_id in unique_ids if session_id in existing and session_id not in active]

        with conn:
            for batch in _batched(deletable):
                placeholders = ",".join("?" for _ in batch)
                conn.execute(
                    f"DELETE FROM backend_chat_sessions WHERE session_id IN ({placeholders})",
                    batch,
                )
                conn.execute(
                    f"DELETE FROM backend_chat_messages WHERE session_id IN ({placeholders})",
                    batch,
                )

        return {
            "deleted": deletable,
            "missing": missing,
            "active": [session_id for session_id in unique_ids if session_id in active],
        }
