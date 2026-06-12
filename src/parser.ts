import { basename } from "node:path";

export interface NormMessage {
  id: string;
  sender: string;
  is_self: number;
  timestamp: string; // 本地时间 ISO 8601（无时区后缀）
  content: string;
  msg_type: string;
  thread: string;
  reply_to: string | null;
}

// v1 白名单：纯文本。引用消息本质是文本（content 已含内联引用文字），且占 17%，
// 丢弃会大量截断对话因果链。
// v2 计划加回："动画表情"（content 自带语义名 [表情包：抱抱]，占 23%，是情感信号）；
//             图片/视频/链接等用占位符维持对话连贯性
const INCLUDE_TYPES = new Set(["文本消息", "引用消息"]);

interface WeFlowMessage {
  localId: number;
  createTime: number;
  type: string;
  content: string | null;
  isSend: number;
  senderUsername?: string;
  senderDisplayName?: string;
  platformMessageId?: string;
  replyToMessageId?: string;
  quotedSender?: string;
  quotedContent?: string;
}

interface WeFlowExport {
  weflow: { version: string; generator: string };
  session: {
    wxid: string;
    nickname: string;
    remark?: string;
    displayName: string;
    type: string;
  };
  messages: WeFlowMessage[];
}

function toLocalIso(unixSec: number): string {
  const d = new Date(unixSec * 1000);
  const p = (n: number, w = 2) => String(n).padStart(w, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}T${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`;
}

export function isWeFlowExport(data: unknown): data is WeFlowExport {
  return (
    typeof data === "object" &&
    data !== null &&
    "weflow" in data &&
    "messages" in data &&
    Array.isArray((data as WeFlowExport).messages)
  );
}

export interface ParseResult {
  thread: string;
  total: number;
  included: number;
  skippedByType: Map<string, number>;
  messages: NormMessage[];
}

export function parseWeFlow(data: WeFlowExport, filePath: string): ParseResult {
  const { session } = data;
  // remark 非空时优先 remark 作为对方/会话显示名
  const peerName = session.remark?.trim() || session.displayName || session.nickname;
  const thread = peerName;
  const fileBase = basename(filePath);

  const messages: NormMessage[] = [];
  const skippedByType = new Map<string, number>();

  for (const m of data.messages) {
    if (!INCLUDE_TYPES.has(m.type)) {
      skippedByType.set(m.type, (skippedByType.get(m.type) ?? 0) + 1);
      continue;
    }
    if (m.content == null || m.content === "") {
      skippedByType.set(`${m.type}(空内容)`, (skippedByType.get(`${m.type}(空内容)`) ?? 0) + 1);
      continue;
    }
    const sender =
      m.isSend === 1
        ? m.senderDisplayName || "我"
        : (m.senderUsername === session.wxid ? peerName : m.senderDisplayName) || peerName;
    messages.push({
      id: m.platformMessageId || `${fileBase}:${m.localId}`,
      sender,
      is_self: m.isSend === 1 ? 1 : 0,
      timestamp: toLocalIso(m.createTime),
      content: m.content,
      msg_type: m.type,
      thread,
      reply_to: m.replyToMessageId || null,
    });
  }

  return { thread, total: data.messages.length, included: messages.length, skippedByType, messages };
}
