// 临时冒烟测试：不依赖任何 API key，直接验证检索层
import "dotenv/config";
import * as store from "../store.js";
import * as retrieval from "../retrieval.js";

const r1 = store.searchMessages({ query: "晚安", limit: 3 });
console.log("【search 短词(LIKE回退)】total:", r1.total_count, "| 首条:", r1.messages[0]?.time, r1.messages[0]?.sender, "→", r1.messages[0]?.content);

const r2 = store.searchMessages({ query: "老婆老婆我看看", limit: 3 });
console.log("【search 长词(FTS)】total:", r2.total_count, "| 首条:", r2.messages[0]?.content);

if (r2.messages[0]) {
  const ctx = store.getContext({ message_id: r2.messages[0].message_id, before: 2, after: 2 });
  if ("messages" in ctx) {
    console.log("【get_context】quoted:", JSON.stringify(ctx.quoted_message));
    for (const m of ctx.messages) console.log("   ", m.is_center ? ">>" : "  ", m.time, m.sender, ":", m.content.slice(0, 30));
  }
}

const r3 = store.browse({ after: "2026-06-12", before: "2026-06-12", limit: 3 });
console.log("【browse_by_time】total:", r3.total_count);

const s = store.stats();
console.log("【get_stats】", JSON.stringify({ total: s.total_messages, span: s.time_span, senders: s.senders, chunks: s.indexed_session_chunks }, null, 0));

const sem = await retrieval.semanticSearch({ query: "房顶", limit: 2 });
console.log("【semantic_search(FTS-only降级)】note:", sem.note?.slice(0, 30) + "…", "| 命中", sem.sessions.length, "块");
if (sem.sessions[0]) console.log("   块片段:", sem.sessions[0].time_range, "|", sem.sessions[0].snippet.split("\n").slice(0, 3).join(" / "));
