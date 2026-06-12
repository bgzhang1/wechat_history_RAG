import "dotenv/config";
import { readFileSync, readdirSync, statSync, existsSync } from "node:fs";
import { join } from "node:path";
import pLimit from "p-limit";
import { isWeFlowExport, parseWeFlow } from "./parser.js";
import * as store from "./store.js";
import { chunkThread, type Chunk } from "./chunker.js";
import { embed, embedConfigured, chatConfigured, chatClient } from "./llm.js";

const SUMMARY_PROMPT = `用一句话（30字内）概括这段聊天讨论的主题和关键事项，直接输出概括，不要任何前缀。聊天内容：\n\n`;

function collectJsonFiles(target: string): string[] {
  const st = statSync(target);
  if (st.isFile()) return [target];
  // 跳过 .jsonl：WeFlow 同时导出的 chatlab 格式，与 .json 内容重复
  return readdirSync(target)
    .filter((f) => f.toLowerCase().endsWith(".json"))
    .map((f) => join(target, f));
}

async function main() {
  const argv = process.argv.slice(2);
  const noSummary = argv.includes("--no-summary");
  const targets = argv.filter((a) => !a.startsWith("--"));
  if (targets.length === 0) {
    console.error("用法: npm run ingest -- <json文件或目录> [更多路径...] [--no-summary]");
    process.exit(1);
  }

  // ---- 1. 解析 + 入库 ----
  const files = targets.flatMap((t) => {
    if (!existsSync(t)) { console.error(`路径不存在，跳过：${t}`); return []; }
    return collectJsonFiles(t);
  });
  console.log(`发现 ${files.length} 个 JSON 文件`);

  let totalIncluded = 0;
  for (const file of files) {
    let data: unknown;
    try {
      data = JSON.parse(readFileSync(file, "utf-8"));
    } catch (e) {
      console.error(`  ✗ ${file}: JSON 解析失败（${e instanceof Error ? e.message : e}）`);
      continue;
    }
    if (!isWeFlowExport(data)) {
      console.error(`  ✗ ${file}: 非 WeFlow 导出格式（缺少顶层 weflow 键），跳过`);
      continue;
    }
    const result = parseWeFlow(data, file);
    const inserted = store.insertMessages(result.messages);
    totalIncluded += result.included;
    const skipped = [...result.skippedByType.entries()].map(([t, n]) => `${t}×${n}`).join(" ");
    console.log(`  ✓ [${result.thread}] 总 ${result.total} 条，入库 ${result.included} 条（新增 ${inserted}）${skipped ? `，跳过: ${skipped}` : ""}`);
  }
  store.finalizeIngest();
  console.log(`\n消息入库完成（共 ${totalIncluded} 条文本/引用消息），FTS 索引已重建`);

  // ---- 2. 会话分块 ----
  const byThread = store.getAllMessagesByThread();
  const chunks: Chunk[] = [];
  for (const [thread, msgs] of byThread) chunks.push(...chunkThread(thread, msgs));
  const sessionIds = store.replaceSessions(chunks);
  console.log(`会话分块完成：${chunks.length} 个块（平均 ${(totalIncluded / Math.max(chunks.length, 1)).toFixed(1)} 条/块）`);

  // ---- 3. 可选：摘要前缀 ----
  const summaryModel = process.env.SUMMARY_MODEL;
  const summaries = new Map<number, string>();
  if (!noSummary && summaryModel && chatConfigured) {
    console.log(`生成摘要前缀（${summaryModel}）...`);
    const limit = pLimit(4);
    let done = 0;
    await Promise.all(
      chunks.map((c, i) =>
        limit(async () => {
          try {
            const res = await chatClient().chat.completions.create({
              model: summaryModel,
              messages: [{ role: "user", content: SUMMARY_PROMPT + c.text.slice(0, 3000) }],
              max_tokens: 60,
            });
            const s = res.choices[0]?.message?.content?.trim();
            if (s) { summaries.set(sessionIds[i], s); store.setSummary(sessionIds[i], s); }
          } catch { /* 单块摘要失败不阻塞整体 */ }
          if (++done % 50 === 0) console.log(`  摘要 ${done}/${chunks.length}`);
        })
      )
    );
    console.log(`摘要完成：${summaries.size}/${chunks.length}`);
  } else {
    console.log(`跳过摘要前缀（${noSummary ? "--no-summary" : "未配置 SUMMARY_MODEL"}）`);
  }

  // ---- 4. 可选：embedding → 向量表 ----
  if (!embedConfigured) {
    console.log("未配置 EMBED_*，跳过向量索引（semantic_search 将退化为全文检索）。配置 .env 后重跑 ingest 即可补建。");
    return;
  }
  if (!store.hasVec()) {
    console.log("sqlite-vec 不可用，跳过向量索引。");
    return;
  }
  console.log(`生成 embedding（${process.env.EMBED_MODEL}，${chunks.length} 块）...`);
  const BATCH = 32;
  for (let i = 0; i < chunks.length; i += BATCH) {
    const batch = chunks.slice(i, i + BATCH);
    const inputs = batch.map((c, j) => {
      const sid = sessionIds[i + j];
      const summary = summaries.get(sid);
      // embedding 输入 = summary + "\n" + text（Contextual Prefix，手册 §5.3）
      return summary ? `${summary}\n${c.text}` : c.text;
    });
    const vecs = await embed(inputs);
    store.insertEmbeddings(vecs.map((v, j) => ({ session_id: sessionIds[i + j], embedding: v })));
    process.stdout.write(`  embedding ${Math.min(i + BATCH, chunks.length)}/${chunks.length}\r`);
  }
  console.log(`\n向量索引完成。现在可以运行 npm run chat`);
}

main().catch((e) => { console.error(e); process.exit(1); });
