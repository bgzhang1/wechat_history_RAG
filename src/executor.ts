import { z } from "zod";
import * as store from "./store.js";
import * as retrieval from "./retrieval.js";

// 模型给的 JSON 不可全信：入参先过 zod 校验，非法参数返回错误文本而不是抛异常，
// 让模型自己纠正重试（手册 §6 铁律一）
const isoDate = z
  .string()
  .regex(/^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?$/, "时间格式无效，请用 ISO 8601 如 2025-03-01");

const SearchArgs = z.object({
  query: z.string().min(1),
  sender: z.string().optional(),
  thread: z.string().optional(),
  after: isoDate.optional(),
  before: isoDate.optional(),
  limit: z.number().int().positive().optional(),
  offset: z.number().int().nonnegative().optional(),
});

const SemArgs = z.object({
  query: z.string().min(1),
  thread: z.string().optional(),
  after: isoDate.optional(),
  before: isoDate.optional(),
  limit: z.number().int().positive().optional(),
});

const CtxArgs = z.object({
  message_id: z.string().min(1),
  before: z.number().int().nonnegative().optional(),
  after: z.number().int().nonnegative().optional(),
});

const BrowseArgs = z.object({
  after: isoDate,
  before: isoDate,
  thread: z.string().optional(),
  sender: z.string().optional(),
  limit: z.number().int().positive().optional(),
  offset: z.number().int().nonnegative().optional(),
});

export async function executeTool(name: string, argsJson: string): Promise<string> {
  try {
    const args = JSON.parse(argsJson || "{}");
    switch (name) {
      case "search_messages":
        return JSON.stringify(store.searchMessages(SearchArgs.parse(args)));
      case "semantic_search":
        return JSON.stringify(await retrieval.semanticSearch(SemArgs.parse(args)));
      case "get_context":
        return JSON.stringify(store.getContext(CtxArgs.parse(args)));
      case "browse_by_time":
        return JSON.stringify(store.browse(BrowseArgs.parse(args)));
      case "get_stats":
        return JSON.stringify(store.stats());
      default:
        return `错误：未知工具 ${name}`;
    }
  } catch (e) {
    if (e instanceof z.ZodError) {
      const issues = e.issues.map((i) => `${i.path.join(".")}: ${i.message}`).join("; ");
      return `参数校验失败：${issues}。请修正参数后重试。`;
    }
    return `工具执行错误：${e instanceof Error ? e.message : String(e)}。请检查参数后重试。`;
  }
}
