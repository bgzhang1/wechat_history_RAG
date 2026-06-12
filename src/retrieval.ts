import { embed, embedConfigured } from "./llm.js";
import * as store from "./store.js";

export interface SessionHit {
  session_id: number;
  thread: string;
  time_range: string;
  participants: string[];
  summary: string | null;
  snippet: string;
  message_ids_sample: string[];
}

const SNIPPET_CHARS = 600;

function toHit(r: store.SessionRow): SessionHit {
  const ids = JSON.parse(r.msg_ids) as string[];
  return {
    session_id: r.session_id,
    thread: r.thread,
    time_range: `${r.start_time.replace("T", " ")} ~ ${r.end_time.replace("T", " ")}`,
    participants: JSON.parse(r.participants),
    summary: r.summary,
    snippet:
      [...r.text].length > SNIPPET_CHARS ? [...r.text].slice(0, SNIPPET_CHARS).join("") + "…" : r.text,
    // 首中尾各取几条，作为 get_context 下钻句柄
    message_ids_sample: ids.length <= 6 ? ids : [...ids.slice(0, 2), ids[Math.floor(ids.length / 2)], ...ids.slice(-2)],
  };
}

function applyFilters(rows: store.SessionRow[], f: { thread?: string; after?: string; before?: string }) {
  return rows.filter((r) => {
    if (f.thread && !r.thread.includes(f.thread)) return false;
    if (f.after && r.end_time < f.after) return false;
    if (f.before && r.start_time > f.before.slice(0, 10) + "T23:59:59" && r.start_time > f.before) return false;
    return true;
  });
}

/** 混合召回（FTS + 向量）+ RRF 融合（k=60），元数据过滤在融合之后做 */
export async function semanticSearch(args: {
  query: string; thread?: string; after?: string; before?: string; limit?: number;
}): Promise<{ note?: string; sessions: SessionHit[] }> {
  const topN = Math.min(args.limit ?? 8, 20);
  const vecReady = store.hasVec() && embedConfigured && store.getAllSessionIdsWithoutEmbedding().length === 0;

  const ftsHits = store.ftsSearchSessions(args.query, 20);
  let vecHits: { sessionId: number }[] = [];
  let note: string | undefined;
  if (vecReady) {
    try {
      const [qv] = await embed([args.query]);
      vecHits = store.vectorSearchSessions(qv, 20);
    } catch (e) {
      note = `向量检索失败（${e instanceof Error ? e.message : e}），本次结果仅来自全文检索`;
    }
  } else {
    note = "向量索引不可用（未配置 EMBED_* 或索引未建），本次结果仅来自全文检索，模糊语义召回可能偏弱";
  }

  // RRF: score(d) = Σ 1 / (k + rank_i(d)), k = 60
  const k = 60;
  const scores = new Map<number, number>();
  for (const [rank, hit] of ftsHits.entries())
    scores.set(hit.sessionId, (scores.get(hit.sessionId) ?? 0) + 1 / (k + rank + 1));
  for (const [rank, hit] of vecHits.entries())
    scores.set(hit.sessionId, (scores.get(hit.sessionId) ?? 0) + 1 / (k + rank + 1));

  const rankedIds = [...scores.entries()].sort((a, b) => b[1] - a[1]).map(([id]) => id);
  const rows = store.getSessions(rankedIds);
  const byId = new Map(rows.map((r) => [r.session_id, r]));
  const ordered = rankedIds.map((id) => byId.get(id)).filter((r): r is store.SessionRow => !!r);
  const filtered = applyFilters(ordered, args).slice(0, topN);

  return { note, sessions: filtered.map(toHit) };
}
