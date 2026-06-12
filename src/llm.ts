import "dotenv/config";
import OpenAI from "openai";

export const chatConfigured = !!(
  process.env.CHAT_BASE_URL &&
  process.env.CHAT_API_KEY &&
  process.env.CHAT_MODEL
);

export const embedConfigured = !!(
  process.env.EMBED_BASE_URL &&
  process.env.EMBED_API_KEY &&
  process.env.EMBED_MODEL
);

export const EMBED_DIM = Number(process.env.EMBED_DIM ?? 1024);

let _chat: OpenAI | null = null;
let _embed: OpenAI | null = null;

export function chatClient(): OpenAI {
  if (!chatConfigured) {
    throw new Error("未配置主模型：请在 .env 中设置 CHAT_BASE_URL / CHAT_API_KEY / CHAT_MODEL（参考 .env.example）");
  }
  _chat ??= new OpenAI({
    baseURL: process.env.CHAT_BASE_URL,
    apiKey: process.env.CHAT_API_KEY,
  });
  return _chat;
}

export function embedClient(): OpenAI {
  if (!embedConfigured) {
    throw new Error("未配置 Embedding：请在 .env 中设置 EMBED_BASE_URL / EMBED_API_KEY / EMBED_MODEL");
  }
  _embed ??= new OpenAI({
    baseURL: process.env.EMBED_BASE_URL,
    apiKey: process.env.EMBED_API_KEY,
  });
  return _embed;
}

export async function embed(texts: string[]): Promise<number[][]> {
  // 兼容端点普遍限制单批条数，分批 + 简单重试
  const BATCH = 32;
  const out: number[][] = [];
  for (let i = 0; i < texts.length; i += BATCH) {
    const batch = texts.slice(i, i + BATCH);
    let lastErr: unknown;
    for (let attempt = 0; attempt < 3; attempt++) {
      try {
        const res = await embedClient().embeddings.create({
          model: process.env.EMBED_MODEL!,
          input: batch,
        });
        out.push(...res.data.map((d) => d.embedding));
        lastErr = null;
        break;
      } catch (e) {
        lastErr = e;
        await new Promise((r) => setTimeout(r, 1000 * (attempt + 1)));
      }
    }
    if (lastErr) throw lastErr;
  }
  return out;
}
