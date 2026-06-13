# 微信聊天记录检索 Agent（Python + LangChain）

把 WeFlow 导出的微信聊天 JSON 导入 SQLite，然后用自然语言检索、追溯上下文、统计聊天内容。

性能优化与基准结果见 [CHANGELOG.md](CHANGELOG.md)。

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

没有配置 `EMBED_*` 也可以运行，`semantic_search` 会自动退化为 FTS-only。配置好 embedding 后重新运行 ingest 即可自动补建向量索引（无需 --force）。

## 常用命令

```bash
# 端点连通性检查
python -m wechat_rag_agent.scripts.check

# 导入单个 JSON 或目录
python -m wechat_rag_agent.ingest data
python -m wechat_rag_agent.ingest path\to\chat.json --no-summary

# 文件大小和修改时间没变时会直接跳过解析；增量导入只重建有新消息的会话线程，
# 内容未变的会话块自动复用已有摘要和向量（不重复调用 LLM）。
# 上次中断/失败留下的缺失摘要、向量、FTS 索引会在下次运行时自动补齐。
# 可以分别强制某个阶段，也可以用 --force-rebuild 全量重建。
python -m wechat_rag_agent.ingest data --force-fts
python -m wechat_rag_agent.ingest data --force-chunks
python -m wechat_rag_agent.ingest data --force-summary
python -m wechat_rag_agent.ingest data --force-embeddings
python -m wechat_rag_agent.ingest data --force-rebuild

# 摘要批量请求和 embedding 并发数；接口慢/限流时可调小 workers、调大 summary batch
python -m wechat_rag_agent.ingest data --summary-workers 1 --summary-batch-size 4 --embed-workers 2 --embed-batch-size 16

# 进度显示与摘要兜底：摘要 422/BadRequest 时会自动用更短文本重试
python -m wechat_rag_agent.ingest data --progress-every 20 --progress-interval 10 --summary-batch-size 4 --summary-max-chars 3000 --summary-fallback-chars 1200

# 默认遇到摘要/embedding 模型或 API 错误会立即停止；需要尽量跑完时再显式开启
python -m wechat_rag_agent.ingest data --keep-going

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
- 增量导入只重建有新消息的线程；会话块带内容哈希（text_hash），内容未变的块直接复用已有摘要和向量，不重复调用 LLM。重新导出同一个聊天再导入时，通常只有末尾少数新块需要处理。
- 缺失的 FTS 索引、seq、摘要、向量会在每次运行时自动检测并补齐（自愈），中断后重跑即可续传。
- 摘要按完成顺序流式进入 embedding 批队列，两个阶段并行执行；摘要写库批量提交。
- FTS、会话分块、摘要、向量索引可以分别用 `--force-fts`、`--force-chunks`、`--force-summary`、`--force-embeddings` 重建。
- 摘要请求会按 `--summary-batch-size`（默认 4）一次处理多个会话块，减少 API 请求数；摘要和 embedding 并发分别用 `--summary-workers`（默认 2）和 `--embed-workers`（默认 4）控制。
- 进度输出可用 `--progress-every` / `--progress-interval` 调整。
- 摘要遇到 `UnprocessableEntityError` / `BadRequestError` 时，会按 `--summary-fallback-chars` 使用更短文本重试；仍失败时默认立即停止。
- 默认遇到摘要或 embedding 的模型/API 错误会立即停止并返回非 0 状态，避免问题扩大；需要旧的“尽量跑完再汇总失败”行为时加 `--keep-going`。
- 模型超时和重试可在 `.env` 中配置：`CHAT_TIMEOUT`、`CHAT_MAX_RETRIES`、`EMBED_TIMEOUT`、`EMBED_MAX_RETRIES`、`EMBED_LOCAL_RETRIES`、`EMBED_RETRY_SLEEP`。
- `semantic_search` 使用 FTS + sqlite-vec 向量召回，再用 RRF 融合；向量不可用时自动降级。
- 对 `hi`、`你好` 这类纯问候，本地直接回复，避免触发某些网关的反测活拦截。

旧的 TypeScript 实现暂时保留在 `src/`，后续确认 Python 版稳定后可以删除。
