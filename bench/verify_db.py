"""对一个 chat DB 计算与 session_id 无关的内容摘要，用于对比两次 ingest 产出是否等价。"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def digest(db_path: str) -> dict:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row

    def sha(rows) -> str:
        h = hashlib.sha256()
        for row in rows:
            h.update(json.dumps(row, ensure_ascii=False, sort_keys=True).encode())
        return h.hexdigest()[:16]

    msgs = [
        [row["id"], row["sender"], row["is_self"], row["timestamp"], row["content"],
         row["msg_type"], row["thread"], row["reply_to"], row["seq"]]
        for row in conn.execute("SELECT * FROM messages ORDER BY id")
    ]
    sessions = conn.execute(
        "SELECT session_id, thread, start_time, end_time, participants, msg_ids, text, summary FROM sessions"
    ).fetchall()
    text_hash_of = {
        row["session_id"]: hashlib.sha256(row["text"].encode()).hexdigest()[:16]
        for row in sessions
    }
    sess_rows = sorted(
        [r["thread"], r["start_time"], r["end_time"], r["participants"], r["msg_ids"], r["text"]]
        for r in sessions
    )
    mapping = sorted(
        [row["msg_id"], text_hash_of.get(row["session_id"], "?")]
        for row in conn.execute("SELECT msg_id, session_id FROM msg_session")
    )
    try:
        fts = conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0]
    except Exception:
        fts = -1
    # 向量表是 vec0 虚表，只读连接读不了，改读其影子表
    vec_failed = False
    try:
        vec_ids = {row[0] for row in conn.execute("SELECT rowid FROM sessions_vec_rowids")}
        vec_hashes = sorted(text_hash_of.get(i, "?") for i in vec_ids)
    except Exception:
        vec_failed = True
        vec_hashes = []

    return {
        "messages": len(msgs),
        "messages_sha": sha(msgs),
        "seq_null": conn.execute("SELECT COUNT(*) FROM messages WHERE seq IS NULL").fetchone()[0],
        "fts_rows": fts,
        "sessions": len(sess_rows),
        "sessions_sha": sha(sess_rows),
        "summaries": conn.execute("SELECT COUNT(*) FROM sessions WHERE summary IS NOT NULL").fetchone()[0],
        "msg_session": len(mapping),
        "msg_session_sha": sha(mapping),
        "vec_rows": -1 if vec_failed else len(vec_hashes),
        "vec_sha": sha([[h] for h in vec_hashes]),
    }


if __name__ == "__main__":
    results = {path: digest(path) for path in sys.argv[1:]}
    keys = list(next(iter(results.values())).keys())
    names = list(results)
    width = max(len(n) for n in names)
    for key in keys:
        values = [str(results[n][key]) for n in names]
        mark = "  ==" if len(set(values)) == 1 else "  !! DIFF"
        print(f"{key:18} " + "  ".join(f"{v:>20}" for v in values) + mark)
