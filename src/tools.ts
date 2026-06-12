import type OpenAI from "openai";

// description 必须写清"何时调用"——这直接决定模型的路由质量（手册 §6）
export const TOOL_DEFS: OpenAI.Chat.Completions.ChatCompletionTool[] = [
  {
    type: "function",
    function: {
      name: "search_messages",
      description:
        "按关键词精确全文检索聊天消息。当问题包含具体的词（人名、店名、物品、专有名词、用户记得的原话片段）时优先用此工具。返回命中消息列表，每条带 message_id。",
      parameters: {
        type: "object",
        properties: {
          query: { type: "string", description: "搜索关键词，多个词用空格分隔" },
          sender: { type: "string", description: '只搜此发送人，可选。传 "我" 表示只搜自己发的消息' },
          thread: { type: "string", description: "只搜此群聊/会话，可选" },
          after: { type: "string", description: "起始时间 ISO 格式，如 2025-03-01，可选" },
          before: { type: "string", description: "结束时间，可选" },
          limit: { type: "integer", description: "返回条数，默认 20" },
          offset: { type: "integer", description: "分页偏移，默认 0" },
        },
        required: ["query"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "semantic_search",
      description:
        "语义检索：当问题是模糊的、主题性的、用户不记得原话用词时用此工具（如'有没有人提过换工作''关于旅行计划聊了什么'）。返回相关的会话片段（含摘要和消息ID列表）。如果 search_messages 关键词搜索无结果，也应改用此工具重试。",
      parameters: {
        type: "object",
        properties: {
          query: { type: "string", description: "用完整自然语言描述要找的内容，不要只给关键词" },
          thread: { type: "string", description: "限定群聊/会话，可选" },
          after: { type: "string", description: "起始时间，可选" },
          before: { type: "string", description: "结束时间，可选" },
          limit: { type: "integer", description: "返回会话块数，默认 8" },
        },
        required: ["query"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "get_context",
      description:
        "获取某条消息前后的完整对话上下文。检索命中关键消息后，回答前必须用此工具确认对话背景（谁在回复谁、前因后果），避免断章取义。如果该消息是引用消息，返回中会附带被引用的原始消息（即使它在很久之前）。",
      parameters: {
        type: "object",
        properties: {
          message_id: { type: "string" },
          before: { type: "integer", description: "向前取几条，默认 15" },
          after: { type: "integer", description: "向后取几条，默认 15" },
        },
        required: ["message_id"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "browse_by_time",
      description:
        "按时间范围顺序浏览消息，不做关键词过滤。回答'某天/某段时间聊了什么'这类无明确关键词的问题时用。",
      parameters: {
        type: "object",
        properties: {
          after: { type: "string", description: "起始时间 ISO 格式" },
          before: { type: "string", description: "结束时间" },
          thread: { type: "string", description: "限定会话，可选" },
          sender: { type: "string", description: "限定发送人，可选" },
          limit: { type: "integer", description: "默认 50" },
          offset: { type: "integer" },
        },
        required: ["after", "before"],
      },
    },
  },
  {
    type: "function",
    function: {
      name: "get_stats",
      description:
        "获取数据全貌：会话列表、参与者、时间跨度、消息总量、各人消息数。首次对话时应先调用一次了解数据范围；回答统计类问题时也用此工具。",
      parameters: { type: "object", properties: {} },
    },
  },
];
