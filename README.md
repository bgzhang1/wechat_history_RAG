# 微信聊天记录检索 Agent — Demo

输入微信导出的 JSON 聊天记录（WeFlow 格式），通过自然语言提问检索、追溯、统计聊天内容。
实现自《微信聊天记录检索Agent-技术路线手册》v1.0，覆盖 M0~M3 全部里程碑 + M4 摘要前缀（可选开关）。

## 快速开始

```bash
npm install

# 1. 配置模型（agent 对话必需；embedding 可选）
cp .env.example .env   # 填入你的 API key

# 2. 连通性测试
npm run check

# 3. 索引聊天记录（data/ 已放入样本文件；可指向任意文件或目录）
npm run ingest -- data            # 加 --no-summary 可跳过摘要前缀

# 4. 开始对话
npm run chat
```

没有任何 API key 时也能跑通索引和检索层：`npm run ingest -- data` 会跳过向量索引，
`npm run smoke` 可直接验证 5 个检索工具（FTS / LIKE 回退 / 回复链上下文 / 时间浏览 / 统计）。
之后补配 `.env` 重跑 ingest 即可补建向量索引，无需重来。

## 试试这些问题

- `谁发消息最多？`（统计 → get_stats）
- `"老婆老婆我看看"是怎么回事？`（精确 → search_messages + get_context 回复链跳转）
- `半夜有没有聊到世界杯？`（语义 → semantic_search 混合召回）
- `今天下午聊了什么？`（浏览 → browse_by_time）

## 架构速览

```
CLI(readline) → Agent Loop(手写循环, OpenAI 兼容 function calling, 流式)
             → 5 个检索工具(zod 校验入参)
             → chat.db (SQLite: messages + FTS5 trigram + sessions + sqlite-vec)
离线: npm run ingest = 解析 → 入库 → 会话分块(30min GAP/800字/60条) →
      [可选]摘要前缀 → embedding → 向量表
在线: semantic_search = FTS + 向量 KNN 并行召回 → RRF(k=60) 融合 → 元数据过滤
```

关键设计（详见手册）：
- **会话级分块**，不做逐条 embedding（单条消息平均仅 6 字符）
- **trigram FTS + <3 字符短词 LIKE 回退**（中文 2 字查询词很常见）
- **引用消息回复链**：get_context 可跳转到很久之前的被引消息
- **EMBED_* 未配置 / sqlite-vec 加载失败时自动降级**为 FTS-only，不阻塞 demo

## 目录结构

```
src/
├── index.ts          # CLI 入口 + 历史裁剪
├── agent.ts          # agent loop + system prompt 路由规则
├── tools.ts          # 5 个工具的 function calling 定义
├── executor.ts       # 工具执行器 + zod 校验（错误回执给模型自纠）
├── llm.ts            # OpenAI 兼容客户端 ×2 + embed() 分批重试
├── parser.ts         # WeFlow JSON 解析（v1 白名单: 文本+引用消息）
├── store.ts          # SQLite schema + 全部查询
├── chunker.ts        # 会话分块（含小块合并 + 3 条滑动重叠）
├── retrieval.ts      # 混合召回 + RRF 融合
├── ingest.ts         # 离线索引管道
└── scripts/
    ├── check.ts      # 端点连通性测试
    └── smoke.ts      # 检索层冒烟测试（无需 API key）
```
