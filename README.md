# 微信聊天记录检索 Agent（Python + LangChain）

把 WeFlow 导出的微信聊天 JSON 导入 SQLite，然后用自然语言检索、追溯上下文、统计聊天内容。

## 快速开始

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env
# 编辑 .env，填入 CHAT_*；EMBED_* 可选

python -m wechat_rag_agent.scripts.check
python -m wechat_rag_agent.ingest data
python -m wechat_rag_agent.cli
```

没有配置 `EMBED_*` 也可以运行，`semantic_search` 会自动退化为 FTS-only。配置好 embedding 后用 `--force-rebuild` 重新运行 ingest 即可补建向量索引。

## 常用命令

```bash
# 端点连通性检查
python -m wechat_rag_agent.scripts.check

# 导入单个 JSON 或目录
python -m wechat_rag_agent.ingest data
python -m wechat_rag_agent.ingest path\to\chat.json --no-summary

# 文件大小和修改时间没变时会直接跳过解析；本次新增消息为 0 时会自动跳过 FTS/分块/摘要/向量重建。
# 可以分别强制某个阶段，也可以用 --force-rebuild 全量重建。
python -m wechat_rag_agent.ingest data --force-fts
python -m wechat_rag_agent.ingest data --force-chunks
python -m wechat_rag_agent.ingest data --force-summary --summary-workers 4
python -m wechat_rag_agent.ingest data --force-embeddings --embed-workers 2 --embed-batch-size 32
python -m wechat_rag_agent.ingest data --force-rebuild

# 手动设置摘要和 embedding 并发数
python -m wechat_rag_agent.ingest data --summary-workers 4 --embed-workers 2 --embed-batch-size 32

# 不依赖模型的检索层冒烟测试
python -m wechat_rag_agent.scripts.smoke

# 启动聊天
python -m wechat_rag_agent.cli
```

## 结构

```text
wechat_rag_agent/
  cli.py          # Python CLI 入口
  agent.py        # LangChain ChatOpenAI.bind_tools 工具调用 agent
  tools.py        # 5 个 LangChain tools + pydantic 参数校验
  llm.py          # ChatOpenAI / OpenAIEmbeddings 配置
  parser.py       # WeFlow JSON 解析
  chunker.py      # 会话分块
  store.py        # SQLite schema、FTS、上下文、统计、向量表
  retrieval.py    # FTS + 向量召回 + RRF 融合
  ingest.py       # 离线导入和索引
  scripts/
    check.py
    smoke.py
```

## 设计说明

- LangChain 负责模型连接、消息结构和工具调用。
- SQLite 仍然负责本地数据、FTS5 trigram、上下文窗口和统计查询。
- `ingest` 会缓存已处理文件的路径、大小和修改时间；文件未变化时直接跳过解析。
- `ingest` 会统计本次真实新增消息数；新增为 0 时默认不做重建，避免大 JSON 重复导入时白等。
- FTS、会话分块、摘要、向量索引可以分别用 `--force-fts`、`--force-chunks`、`--force-summary`、`--force-embeddings` 重建。
- 摘要和 embedding 支持手动并发，分别用 `--summary-workers` 和 `--embed-workers` 控制。
- `semantic_search` 使用 FTS + sqlite-vec 向量召回，再用 RRF 融合；向量不可用时自动降级。
- 对 `hi`、`你好` 这类纯问候，本地直接回复，避免触发某些网关的反测活拦截。

旧的 TypeScript 实现暂时保留在 `src/`，后续确认 Python 版稳定后可以删除。
