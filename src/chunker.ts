import type { MsgRow } from "./store.js";

// 参数依据实测数据校准（手册附录 B）：
// 私聊消息平均仅 6 字符、中位间隔 6 秒、全天 >30min 间隔仅 6 次
// → 字符/消息数上限才是实际的主切分器，30min GAP 只负责天然会话边界
const GAP_MINUTES = 30;
const MAX_CHARS = 800;
const MAX_MSGS = 60;
const MIN_CHARS = 50;
const MERGE_GAP_HOURS = 2;
const OVERLAP_MSGS = 3;

export interface Chunk {
  thread: string;
  start_time: string;
  end_time: string;
  participants: string; // JSON 数组
  msg_ids: string;      // JSON 数组
  text: string;
  summary: string | null;
}

const ts = (m: MsgRow) => new Date(m.timestamp + "Z").getTime(); // 仅作差值用，时区偏移可抵消
const chars = (msgs: MsgRow[]) => msgs.reduce((n, m) => n + [...m.content].length, 0);

export function chunkThread(thread: string, msgs: MsgRow[], threadType = ""): Chunk[] {
  if (msgs.length === 0) return [];

  // 1) 顺序扫描切块
  const raw: MsgRow[][] = [];
  let cur: MsgRow[] = [];
  let curChars = 0;
  for (const m of msgs) {
    const len = [...m.content].length;
    const gapMin = cur.length > 0 ? (ts(m) - ts(cur[cur.length - 1])) / 60000 : 0;
    if (cur.length > 0 && (gapMin > GAP_MINUTES || curChars + len > MAX_CHARS || cur.length >= MAX_MSGS)) {
      raw.push(cur);
      cur = [];
      curChars = 0;
    }
    cur.push(m);
    curChars += len;
  }
  if (cur.length > 0) raw.push(cur);

  // 2) 小块合并：字符数 < MIN_CHARS 且与下一块间隔 < 2h → 并入下一块
  //    （避免"好的""收到"单独成块）
  const merged: MsgRow[][] = [];
  for (let i = 0; i < raw.length; i++) {
    const block = raw[i];
    const next = raw[i + 1];
    if (
      next &&
      chars(block) < MIN_CHARS &&
      (ts(next[0]) - ts(block[block.length - 1])) / 3600000 < MERGE_GAP_HOURS
    ) {
      raw[i + 1] = [...block, ...next];
      continue;
    }
    merged.push(block);
  }

  // 3) 滑动重叠：每块开头复制上一块末尾 3 条（仅进入文本，不进入 msg_ids），
  //    缓解话题恰好在块边界切断的情况
  return merged.map((block, i) => {
    const overlap = i > 0 ? merged[i - 1].slice(-OVERLAP_MSGS) : [];
    const participants = [...new Set(block.map((m) => m.sender))];
    const start = block[0].timestamp;
    const end = block[block.length - 1].timestamp;
    const header = `[${start.replace("T", " ").slice(0, 16)} ~ ${end.replace("T", " ").slice(11, 16)}] ${threadType}${thread}（${participants.join("、")}）`;
    const lines = [...overlap, ...block].map((m) => `${m.sender}: ${m.content}`);
    return {
      thread,
      start_time: start,
      end_time: end,
      participants: JSON.stringify(participants),
      msg_ids: JSON.stringify(block.map((m) => m.id)),
      text: `${header}\n${lines.join("\n")}`,
      summary: null,
    };
  });
}
