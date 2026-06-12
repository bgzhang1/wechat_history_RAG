import Database from "better-sqlite3";
import * as sqliteVec from "sqlite-vec";
import type { NormMessage } from "./parser.js";
import { EMBED_DIM } from "./llm.js";

export const DB_PATH = process.env.CHAT_DB ?? "chat.db";

let _db: Database.Database | null = null;
let vecAvailable = false;

export function db(): Database.Database {
  if (_db) return _db;
  _db = new Database(DB_PATH);
  _db.pragma("journal_mode = WAL");
  try {
    sqliteVec.load(_db);
    vecAvailable = true;
  } catch (e) {
    // 退化方案见手册 §11：vec0 不可用时 semantic_search 走 FTS-only
    console.error(`[警告] sqlite-vec 加载失败，语义检索退化为全文检索：${e instanceof Error ? e.message : e}`);
  }
  initSchema(_db);
  return _db;
}

export function hasVec(): boolean {
  db();
  return vecAvailable;
}

function initSchema(d: Database.Database) {
  d.exec(`
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
  `);
  if (vecAvailable) {
    d.exec(`
      CREATE VIRTUAL TABLE IF NOT EXISTS sessions_vec
        USING vec0(session_id INTEGER PRIMARY KEY, embedding FLOAT[${EMBED_DIM}]);
    `);
  }
}

// ---------- ingest 写入 ----------

export function insertMessages(msgs: NormMessage[]): number {
  const d = db();
  const stmt = d.prepare(`
    INSERT OR IGNORE INTO messages (id, sender, is_self, timestamp, content, msg_type, thread, reply_to)
    VALUES (@id, @sender, @is_self, @timestamp, @content, @msg_type, @thread, @reply_to)
  `);
  let inserted = 0;
  const tx = d.transaction((batch: NormMessage[]) => {
    for (const m of batch) inserted += stmt.run(m).changes;
  });
  for (let i = 0; i < msgs.length; i += 5000) tx(msgs.slice(i, i + 5000));
  return inserted;
}

/** 入库完成后调用：重算 thread 内时间序号 + 重建 FTS 索引 */
export function finalizeIngest() {
  const d = db();
  d.exec(`
    UPDATE messages SET seq = t.rn
    FROM (SELECT id, ROW_NUMBER() OVER (PARTITION BY thread ORDER BY timestamp, id) AS rn FROM messages) t
    WHERE messages.id = t.id;
  `);
  d.exec(`INSERT INTO messages_fts(messages_fts) VALUES('rebuild');`);
}

export interface SessionRow {
  session_id: number;
  thread: string;
  start_time: string;
  end_time: string;
  participants: string;
  msg_ids: string;
  text: string;
  summary: string | null;
}

export function replaceSessions(rows: Omit<SessionRow, "session_id">[]): number[] {
  const d = db();
  const ids: number[] = [];
  const tx = d.transaction(() => {
    d.exec(`DELETE FROM sessions; DELETE FROM msg_session;`);
    if (vecAvailable) d.exec(`DELETE FROM sessions_vec;`);
    const ins = d.prepare(`
      INSERT INTO sessions (thread, start_time, end_time, participants, msg_ids, text, summary)
      VALUES (@thread, @start_time, @end_time, @participants, @msg_ids, @text, @summary)
    `);
    const map = d.prepare(`INSERT OR REPLACE INTO msg_session (msg_id, session_id) VALUES (?, ?)`);
    for (const r of rows) {
      const id = Number(ins.run(r).lastInsertRowid);
      ids.push(id);
      for (const mid of JSON.parse(r.msg_ids) as string[]) map.run(mid, id);
    }
  });
  tx();
  return ids;
}

export function insertEmbeddings(items: { session_id: number; embedding: number[] }[]) {
  const d = db();
  if (!vecAvailable) throw new Error("sqlite-vec 不可用");
  const stmt = d.prepare(`INSERT OR REPLACE INTO sessions_vec (session_id, embedding) VALUES (?, ?)`);
  const tx = d.transaction(() => {
    for (const it of items) {
      stmt.run(BigInt(it.session_id), Buffer.from(new Float32Array(it.embedding).buffer));
    }
  });
  tx();
}

export function setSummary(sessionId: number, summary: string) {
  db().prepare(`UPDATE sessions SET summary = ? WHERE session_id = ?`).run(summary, sessionId);
}

// ---------- 查询 ----------

export interface Filters {
  sender?: string;
  thread?: string;
  after?: string;
  before?: string;
}

export interface MsgRow {
  id: string;
  sender: string;
  is_self: number;
  timestamp: string;
  content: string;
  msg_type: string;
  thread: string;
  reply_to: string | null;
  seq: number;
}

const TRUNC = 200;
const truncate = (s: string) => ([...s].length > TRUNC ? [...s].slice(0, TRUNC).join("") + "…" : s);

function filterClauses(f: Filters): { where: string[]; params: Record<string, unknown> } {
  const where: string[] = [];
  const params: Record<string, unknown> = {};
  if (f.sender) {
    if (f.sender === "我") where.push(`m.is_self = 1`);
    else { where.push(`m.sender LIKE '%' || @sender || '%'`); params.sender = f.sender; }
  }
  if (f.thread) { where.push(`m.thread LIKE '%' || @thread || '%'`); params.thread = f.thread; }
  if (f.after) { where.push(`m.timestamp >= @after`); params.after = normTime(f.after, false); }
  if (f.before) { where.push(`m.timestamp <= @before`); params.before = normTime(f.before, true); }
  return { where, params };
}

/** 容忍 "2025-03-01" / "2025-03-01 12:00" 等输入；before 侧补到当天末尾 */
function normTime(t: string, isBefore: boolean): string {
  const s = t.trim().replace(" ", "T");
  if (/^\d{4}-\d{2}-\d{2}$/.test(s)) return isBefore ? `${s}T23:59:59` : `${s}T00:00:00`;
  return s;
}

function toApiMsg(r: MsgRow) {
  return {
    message_id: r.id,
    sender: r.is_self ? `${r.sender}(我)` : r.sender,
    time: r.timestamp.replace("T", " "),
    content: truncate(r.content),
    type: r.msg_type,
    thread: r.thread,
  };
}

/** trigram 要求查询词 ≥3 字符；短词回退 LIKE 全扫（百万行内 <100ms，可接受） */
export function searchMessages(args: {
  query: string; sender?: string; thread?: string; after?: string; before?: string;
  limit?: number; offset?: number;
}) {
  const d = db();
  const limit = Math.min(args.limit ?? 20, 100);
  const offset = args.offset ?? 0;
  const { where, params } = filterClauses(args);

  const terms = args.query.trim().split(/\s+/).filter(Boolean);
  const useFts = terms.every((t) => [...t].length >= 3);

  let rows: MsgRow[]; let total: number;
  if (useFts && terms.length > 0) {
    const match = terms.map((t) => `"${t.replace(/"/g, '""')}"`).join(" AND ");
    const cond = [`messages_fts MATCH @match`, ...where].join(" AND ");
    const base = `FROM messages_fts JOIN messages m ON m.rowid = messages_fts.rowid WHERE ${cond}`;
    total = (d.prepare(`SELECT COUNT(*) c ${base}`).get({ ...params, match }) as { c: number }).c;
    rows = d.prepare(`SELECT m.* ${base} ORDER BY m.timestamp LIMIT @limit OFFSET @offset`)
      .all({ ...params, match, limit, offset }) as MsgRow[];
  } else {
    const likes = terms.map((_, i) => `m.content LIKE '%' || @q${i} || '%'`);
    terms.forEach((t, i) => (params[`q${i}`] = t));
    const cond = [...likes, ...where].join(" AND ") || "1=1";
    const base = `FROM messages m WHERE ${cond}`;
    total = (d.prepare(`SELECT COUNT(*) c ${base}`).get(params) as { c: number }).c;
    rows = d.prepare(`SELECT m.* ${base} ORDER BY m.timestamp LIMIT @limit OFFSET @offset`)
      .all({ ...params, limit, offset }) as MsgRow[];
  }
  return { total_count: total, returned: rows.length, offset, messages: rows.map(toApiMsg) };
}

export function getContext(args: { message_id: string; before?: number; after?: number }) {
  const d = db();
  const center = d.prepare(`SELECT * FROM messages WHERE id = ?`).get(args.message_id) as MsgRow | undefined;
  if (!center) return { error: `查无此消息 id：${args.message_id}` };
  const before = Math.min(args.before ?? 15, 50);
  const after = Math.min(args.after ?? 15, 50);
  const rows = d.prepare(
    `SELECT * FROM messages WHERE thread = ? AND seq BETWEEN ? AND ? ORDER BY seq`
  ).all(center.thread, center.seq - before, center.seq + after) as MsgRow[];

  // 引用消息：邻居窗口之外跳到被引消息（引用经常指向很久之前的内容）
  let quoted: ReturnType<typeof toApiMsg> | { note: string } | null = null;
  if (center.reply_to) {
    const q = d.prepare(`SELECT * FROM messages WHERE id = ?`).get(center.reply_to) as MsgRow | undefined;
    quoted = q ? toApiMsg(q) : { note: "被引消息不在库中（可能是图片/表情等未入库类型），content 中的内联引用文字可作参考" };
  }
  return {
    thread: center.thread,
    center_message_id: center.id,
    quoted_message: quoted,
    messages: rows.map((r) => ({ ...toApiMsg(r), is_center: r.id === center.id || undefined })),
  };
}

export function browse(args: {
  after: string; before: string; thread?: string; sender?: string; limit?: number; offset?: number;
}) {
  const d = db();
  const limit = Math.min(args.limit ?? 50, 200);
  const offset = args.offset ?? 0;
  const { where, params } = filterClauses(args);
  const cond = where.join(" AND ") || "1=1";
  const base = `FROM messages m WHERE ${cond}`;
  const total = (d.prepare(`SELECT COUNT(*) c ${base}`).get(params) as { c: number }).c;
  const rows = d.prepare(`SELECT m.* ${base} ORDER BY m.timestamp LIMIT @limit OFFSET @offset`)
    .all({ ...params, limit, offset }) as MsgRow[];
  return { total_count: total, returned: rows.length, offset, messages: rows.map(toApiMsg) };
}

export function stats() {
  const d = db();
  const overall = d.prepare(
    `SELECT COUNT(*) total, MIN(timestamp) earliest, MAX(timestamp) latest FROM messages`
  ).get() as { total: number; earliest: string; latest: string };
  const threads = d.prepare(
    `SELECT thread, COUNT(*) count, MIN(timestamp) earliest, MAX(timestamp) latest
     FROM messages GROUP BY thread ORDER BY count DESC`
  ).all();
  const senders = d.prepare(
    `SELECT sender, MAX(is_self) is_self, COUNT(*) count FROM messages GROUP BY sender ORDER BY count DESC LIMIT 50`
  ).all() as { sender: string; is_self: number; count: number }[];
  const types = d.prepare(
    `SELECT msg_type, COUNT(*) count FROM messages GROUP BY msg_type ORDER BY count DESC`
  ).all();
  const sessionCount = (d.prepare(`SELECT COUNT(*) c FROM sessions`).get() as { c: number }).c;
  return {
    total_messages: overall.total,
    time_span: { earliest: overall.earliest, latest: overall.latest },
    threads,
    senders: senders.map((s) => ({ ...s, is_self: s.is_self ? true : undefined })),
    message_types: types,
    indexed_session_chunks: sessionCount,
  };
}

// ---------- 检索层用到的底层查询 ----------

export function getAllMessagesByThread(): Map<string, MsgRow[]> {
  const rows = db().prepare(`SELECT * FROM messages ORDER BY thread, seq`).all() as MsgRow[];
  const map = new Map<string, MsgRow[]>();
  for (const r of rows) {
    if (!map.has(r.thread)) map.set(r.thread, []);
    map.get(r.thread)!.push(r);
  }
  return map;
}

export function getSessions(ids: number[]): SessionRow[] {
  if (ids.length === 0) return [];
  const ph = ids.map(() => "?").join(",");
  return db().prepare(`SELECT * FROM sessions WHERE session_id IN (${ph})`).all(...ids) as SessionRow[];
}

export function getAllSessionIdsWithoutEmbedding(): number[] {
  if (!hasVec()) return [];
  return (db().prepare(
    `SELECT s.session_id FROM sessions s
     LEFT JOIN sessions_vec v ON v.session_id = s.session_id
     WHERE v.session_id IS NULL`
  ).all() as { session_id: number }[]).map((r) => r.session_id);
}

/** FTS 命中消息 → 映射到所属会话块（手册 §5.4 路径①） */
export function ftsSearchSessions(query: string, limit: number): { sessionId: number }[] {
  const d = db();
  const terms = query.trim().split(/\s+/).filter(Boolean);
  if (terms.length === 0) return [];
  const useFts = terms.every((t) => [...t].length >= 3);
  let msgIds: { id: string }[];
  if (useFts) {
    const match = terms.map((t) => `"${t.replace(/"/g, '""')}"`).join(" OR ");
    msgIds = d.prepare(
      `SELECT m.id FROM messages_fts JOIN messages m ON m.rowid = messages_fts.rowid
       WHERE messages_fts MATCH ? LIMIT 200`
    ).all(match) as { id: string }[];
  } else {
    const likes = terms.map(() => `content LIKE '%' || ? || '%'`).join(" OR ");
    msgIds = d.prepare(`SELECT id FROM messages WHERE ${likes} LIMIT 200`).all(...terms) as { id: string }[];
  }
  if (msgIds.length === 0) return [];
  const ph = msgIds.map(() => "?").join(",");
  const hits = d.prepare(
    `SELECT session_id, COUNT(*) c FROM msg_session WHERE msg_id IN (${ph})
     GROUP BY session_id ORDER BY c DESC LIMIT ?`
  ).all(...msgIds.map((m) => m.id), limit) as { session_id: number }[];
  return hits.map((h) => ({ sessionId: h.session_id }));
}

export function vectorSearchSessions(queryVec: number[], limit: number): { sessionId: number }[] {
  if (!hasVec()) return [];
  const rows = db().prepare(
    `SELECT session_id, distance FROM sessions_vec
     WHERE embedding MATCH ? AND k = ? ORDER BY distance`
  ).all(Buffer.from(new Float32Array(queryVec).buffer), BigInt(limit)) as { session_id: number }[];
  return rows.map((r) => ({ sessionId: r.session_id }));
}
