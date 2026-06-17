# 微信聊天记录检索 Agent

把 WeFlow 导出的微信聊天 JSON 导入本地 SQLite，然后用自然语言检索、追溯上下文、统计和浏览聊天记录。

详细架构、数据模型、导入流水线、检索策略和优化建议见 [docs/TECHNICAL.md](docs/TECHNICAL.md)。

## 项目能力

- 导入 WeFlow 微信聊天 JSON。
- 增量写入本地 SQLite，自动跳过未变化文件。
- 使用 FTS5 trigram 构建中文全文索引。
- 将聊天切分为会话块，支持摘要和向量索引。
- 未配置 embedding 时仍可使用全文检索。
- 通过 LangChain tools 让 Agent 自动选择关键词检索、语义检索、上下文追溯、时间浏览和统计工具。
- FastAPI 后端提供 SSE 流式对话接口，支持 Web 前端接入。
- 本地隐私数据默认不提交到 Git。

## 目录结构

```text
wechat_agent/
  core/      # 核心 Python 库（Agent、检索、导入、数据层）
  backend/               # FastAPI 后端（SSE 对话、设置、导入、统计）
  docs/                  # 技术文档
  runtime/               # 本地 SQLite 数据库，Git 忽略
  local/                 # 本地聊天 JSON 和私有资料，Git 忽略
  requirements.txt       # Python 依赖
  .env.example           # 环境变量模板，不包含真实密钥
```

建议把 WeFlow 导出的 JSON 放在 `local/data/` 下。`runtime/`、`local/`、`.env`、数据库、日志和缓存都不会提交。

## 快速开始

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env
# 编辑 .env，至少填写 CHAT_BASE_URL / CHAT_API_KEY / CHAT_MODEL

python -m core.scripts.check
python -m core.ingest local/data
```

### 启动 Web 后端

```bash
python -m uvicorn backend.main:app --reload
```

服务默认监听 `http://localhost:8000`。API 文档见 [backend/API_DOCS.md](backend/API_DOCS.md)，交互式文档访问 `http://localhost:8000/docs`。

### 启动命令行交互

```bash
python -m core.cli
```

## 常用命令

检查模型端点：

```bash
python -m core.scripts.check
```

导入目录或单个 JSON：

```bash
python -m core.ingest local/data
python -m core.ingest path\to\chat.json
```

跳过摘要：

```bash
python -m core.ingest local/data --no-summary
```

强制重建部分索引：

```bash
python -m core.ingest local/data --force-fts
python -m core.ingest local/data --force-chunks
python -m core.ingest local/data --force-summary
python -m core.ingest local/data --force-embeddings
```

全量重建：

```bash
python -m core.ingest local/data --force-rebuild
```

## 配置说明

主聊天模型是交互式 Agent 必需配置：

```env
CHAT_BASE_URL=https://example.com/v1
CHAT_API_KEY=sk-...
CHAT_MODEL=your-chat-model
```

Embedding 是可选配置。未配置时，`semantic_search` 会自动退化为全文检索：

```env
EMBED_BASE_URL=https://example.com/v1
EMBED_API_KEY=sk-...
EMBED_MODEL=your-embedding-model
EMBED_DIM=4096
```

摘要也是可选配置：

```env
SUMMARY_MODEL=your-chat-model
```

完整环境变量说明见 [docs/TECHNICAL.md](docs/TECHNICAL.md#5-配置项)。

## 开发验证

```bash
python -m compileall -q core backend
```

如果已有本地数据库，也可以运行：

```bash
python -m core.scripts.smoke
```
