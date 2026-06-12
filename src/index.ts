import "dotenv/config";
import readline from "node:readline/promises";
import type OpenAI from "openai";
import { runAgent, SYSTEM_PROMPT } from "./agent.js";
import { chatConfigured } from "./llm.js";
import { db, stats } from "./store.js";
import { existsSync } from "node:fs";

// 工具结果是历史中最占空间且最无复用价值的部分：
// 历史超过 ~50 条时裁掉最早的工具结果即可支撑长会话
function trimHistory(history: OpenAI.Chat.Completions.ChatCompletionMessageParam[]) {
  if (history.length <= 50) return;
  let toTrim = history.length - 50;
  for (const msg of history) {
    if (toTrim <= 0) break;
    if (msg.role === "tool" && msg.content !== "（已省略的历史检索结果）") {
      msg.content = "（已省略的历史检索结果）";
      toTrim--;
    }
  }
}

async function main() {
  if (!chatConfigured) {
    console.error("未配置主模型。请复制 .env.example 为 .env 并填入 CHAT_BASE_URL / CHAT_API_KEY / CHAT_MODEL。");
    process.exit(1);
  }
  if (!existsSync(process.env.CHAT_DB ?? "chat.db")) {
    console.error("找不到 chat.db。请先运行: npm run ingest -- <微信导出JSON文件或目录>");
    process.exit(1);
  }
  db();
  const s = stats();
  console.log("微信聊天记录检索 Agent（输入 exit 退出）");
  console.log(`已索引 ${s.total_messages} 条消息 / ${s.indexed_session_chunks} 个会话块，时间跨度 ${s.time_span.earliest?.slice(0, 10)} ~ ${s.time_span.latest?.slice(0, 10)}\n`);

  const history: OpenAI.Chat.Completions.ChatCompletionMessageParam[] = [
    { role: "system", content: SYSTEM_PROMPT },
  ];
  const rl = readline.createInterface({ input: process.stdin, output: process.stdout });

  while (true) {
    const q = (await rl.question("\n你: ")).trim();
    if (!q || q === "exit") break;
    history.push({ role: "user", content: q });
    process.stdout.write("\n助手: ");
    try {
      await runAgent(history); // 历史原地追加，天然多轮
    } catch (e) {
      console.error(`\n[错误] ${e instanceof Error ? e.message : e}`);
    }
    trimHistory(history);
    console.log();
  }
  rl.close();
}

main();
