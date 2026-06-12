import type OpenAI from "openai";
import { chatClient } from "./llm.js";
import { TOOL_DEFS } from "./tools.js";
import { executeTool } from "./executor.js";

const MAX_ROUNDS = 15; // 防失控：单次提问最多 15 轮工具调用

export const SYSTEM_PROMPT = `你是微信聊天记录检索助手，通过检索工具查找并回答用户关于聊天记录的问题。

# 工具路由规则
- 问题含具体词（人名、店名、专名、原话片段）→ search_messages
- 问题模糊、主题性、不记得原话 → semantic_search（用完整句子描述，不要只给关键词）
- search_messages 无结果时 → 换 semantic_search 重试，换近义表述
- "某段时间聊了什么" → browse_by_time
- 统计类问题 / 首次对话 → get_stats

# 检索纪律
- 命中关键消息后，回答前用 get_context 确认前后文，禁止断章取义
- 结果过多时收窄条件（加时间/发送人/会话过滤），而不是逐页翻完
- 最多检索几轮后必须给出结论；信息不足就如实说明缺什么

# 回答要求
- 引用原文：发送人 + 时间 + 消息内容
- 明确区分"记录中明确说了"和"根据上下文推断"
- 检索不到就说检索不到，禁止编造聊天内容`;

export async function runAgent(
  history: OpenAI.Chat.Completions.ChatCompletionMessageParam[],
): Promise<void> {
  for (let round = 0; round < MAX_ROUNDS; round++) {
    const stream = await chatClient().chat.completions.create({
      model: process.env.CHAT_MODEL!,
      messages: history,
      tools: TOOL_DEFS,
      stream: true,
    });

    // 流式消费：text delta 直接打印；tool_calls delta 必须按 index 累积拼接
    // （function.arguments 分片到达，拼完才是合法 JSON）
    let content = "";
    const toolCalls: { id: string; name: string; args: string }[] = [];
    let finishReason: string | null = null;

    for await (const chunk of stream) {
      const choice = chunk.choices[0];
      if (!choice) continue;
      if (choice.delta?.content) {
        content += choice.delta.content;
        process.stdout.write(choice.delta.content);
      }
      for (const tc of choice.delta?.tool_calls ?? []) {
        toolCalls[tc.index] ??= { id: "", name: "", args: "" };
        if (tc.id) toolCalls[tc.index].id += tc.id;
        if (tc.function?.name) toolCalls[tc.index].name += tc.function.name;
        if (tc.function?.arguments) toolCalls[tc.index].args += tc.function.arguments;
      }
      if (choice.finish_reason) finishReason = choice.finish_reason;
    }

    history.push({
      role: "assistant",
      content: content || null,
      ...(toolCalls.length > 0 && {
        tool_calls: toolCalls.map((tc) => ({
          id: tc.id,
          type: "function" as const,
          function: { name: tc.name, arguments: tc.args },
        })),
      }),
    });

    if (finishReason !== "tool_calls" || toolCalls.length === 0) return; // 回答完成

    // 每个 tool_call_id 必须有对应的 role:"tool" 回填，缺一个下轮请求直接 400
    for (const tc of toolCalls) {
      process.stderr.write(`\n  [tool] ${tc.name}(${tc.args.slice(0, 80)})\n`);
      const result = await executeTool(tc.name, tc.args);
      history.push({ role: "tool", tool_call_id: tc.id, content: result });
    }
  }
  const msg = "（已达单次提问的检索轮数上限）";
  process.stdout.write(msg);
  history.push({ role: "assistant", content: msg });
}
