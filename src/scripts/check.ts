import "dotenv/config";
import { chatClient, embedClient, chatConfigured, embedConfigured, EMBED_DIM } from "../llm.js";

async function main() {
  let ok = true;

  if (chatConfigured) {
    try {
      const res = await chatClient().chat.completions.create({
        model: process.env.CHAT_MODEL!,
        messages: [{ role: "user", content: "回复 OK 两个字母即可" }],
        max_tokens: 8,
      });
      console.log(`✓ chat 端点连通（${process.env.CHAT_MODEL}）：${res.choices[0]?.message?.content?.trim()}`);
    } catch (e) {
      ok = false;
      console.error(`✗ chat 端点失败：${e instanceof Error ? e.message : e}`);
    }
  } else {
    ok = false;
    console.error("✗ 未配置 CHAT_*（agent 对话必需）");
  }

  if (embedConfigured) {
    try {
      const res = await embedClient().embeddings.create({
        model: process.env.EMBED_MODEL!,
        input: ["连通性测试"],
      });
      const dim = res.data[0].embedding.length;
      const dimOk = dim === EMBED_DIM;
      console.log(`${dimOk ? "✓" : "✗"} embeddings 端点连通（${process.env.EMBED_MODEL}），返回维度 ${dim}${dimOk ? "" : `，与 EMBED_DIM=${EMBED_DIM} 不一致，请修正 .env`}`);
      if (!dimOk) ok = false;
    } catch (e) {
      ok = false;
      console.error(`✗ embeddings 端点失败：${e instanceof Error ? e.message : e}`);
    }
  } else {
    console.log("- 未配置 EMBED_*（可选；不配则语义检索退化为全文检索）");
  }

  process.exit(ok ? 0 : 1);
}

main();
