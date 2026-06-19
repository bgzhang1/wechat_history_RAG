# 微信聊天记录检索 Agent

把 WeFlow 导出的微信聊天 JSON 导入本地 SQLite，然后用自然语言检索、追溯上下文、统计和浏览聊天记录。

详细架构、数据模型、导入流水线、检索策略和优化建议见 [docs/TECHNICAL.md](docs/TECHNICAL.md)。

## 项目能力

- 导入 WeFlow 微信聊天 JSON，保留文本、引用以及带可读文本的链接、转账、位置、表情说明、图片 OCR、语音转写和视频说明等消息。
- 增量写入本地 SQLite，自动跳过未变化文件；文件变化后会更新已有消息并刷新相关索引。
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
# .env.example 里的 example.com、sk-...、your-* 都只是占位值，程序会按未配置处理

python -m core.scripts.check
python -m core.ingest local/data
```

### 启动 Web 后端

```bash
python -m uvicorn backend.main:app --reload
```

服务默认监听 `http://localhost:8000`。API 文档见 [backend/API_DOCS.md](backend/API_DOCS.md)，交互式文档访问 `http://localhost:8000/docs`。

### 启动 Web 前端

```bash
cd frontend
npm install
npm run dev
```

Vite 默认使用 `http://localhost:5173`，如果端口被占用会切到 5174 等备用端口；后端默认允许 `5173-5180` 的本地前端访问。前端未显式配置 `VITE_API_BASE` 时，会连接当前页面同一主机名的 `:8000` 后端，例如从 `127.0.0.1` 打开的页面会请求 `http://127.0.0.1:8000/api`。

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

目录目标会递归扫描其中的 `.json` 文件，适合直接放入按账号或日期分层的 WeFlow 导出目录。

强制重新解析 JSON 并核对消息入库，但不强制重建所有索引：

```bash
python -m core.ingest local/data --force-import
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

仅基于已有数据库补建索引，不重新解析 JSON：

```bash
python -m core.ingest local/data --skip-import --force-fts
python -m core.ingest local/data --skip-import --force-chunks
python -m core.ingest local/data --skip-import --force-summary
python -m core.ingest local/data --skip-import --force-embeddings
```

首次导入或文件内容变化时不要使用 `--skip-import`。

全量重建：

```bash
python -m core.ingest local/data --force-rebuild
```

`--force-rebuild` 会重新解析目标 JSON，再重建其关联范围的会话分块、摘要和向量；FTS 会优先按目标 JSON 关联消息刷新，若本次修正了已有消息内容，则会全量刷新 FTS 以清理旧 token。解析规则升级后，也可用它把旧文件中的新可读消息类型补入数据库。

Web 前端也提供“设置 -> 数据导入”面板，可上传一个或多个 WeFlow JSON 文件，并按文件查看导入状态。导入模式支持全流程导入、强制重建、仅 FTS、仅分块、仅摘要和仅向量；后台任务会通过 WebSocket 显示阶段、百分比、当前处理说明和最近日志。对于已有来源映射的文件，增量导入会先检查目标 JSON，只有文件变化或解析规则过期时才重新解析；旧库缺少文件来源映射时，也会通过增量导入补齐映射和缺失索引。已变化或索引状态未知的文件会默认使用增量导入，用户仍可手动切换为全流程或强制重建。全流程导入会重新解析目标 JSON 并补齐必要索引；强制重建会进一步重建目标 JSON 关联范围的分块、摘要和向量。单项构建只适用于已经完成导入、文件未变化且后端能定位来源映射的 JSON，其中仅 FTS、仅摘要和仅向量都会按目标 JSON 关联范围执行，并且不会额外触发其它模型阶段。

导入记录会保存当前解析规则版本；项目升级后如果解析器能识别更多消息类型、发送人归属或引用字段，或能从更多导出形态里恢复引用正文，旧文件即使大小和修改时间没变，也会在导入面板显示为“需重新导入”。

## 配置说明

主聊天模型是交互式 Agent 必需配置：

```env
CHAT_BASE_URL=https://example.com/v1
CHAT_API_KEY=sk-...
CHAT_MODEL=your-chat-model
```

上面的值只是格式示例。复制 `.env.example` 后必须替换 `example.com`、`sk-...` 和 `your-*`，否则健康检查会把它们视为未配置，避免误连模板地址。

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

常用本地服务配置：

```env
CHAT_DB=runtime/chat.db
BACKEND_CHAT_DB=runtime/backend_chat.db
HOST=0.0.0.0
PORT=8000
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

后端默认已放行本机 Vite 常用端口 `5173-5180`；只有前端部署在其他域名或端口时才需要追加 `CORS_ORIGINS`。

`CHAT_DB` 和 `BACKEND_CHAT_DB` 默认是普通文件路径，会自动创建父目录；高级部署也可以使用 `file:` SQLite URI。

如果前端需要连接非默认后端地址，在 `frontend/.env` 中设置：

```env
VITE_API_BASE=http://localhost:8000
```

通常本地开发不需要设置 `VITE_API_BASE`；前端会按当前页面的主机名自动连接 `:8000`。如果前端和后端由同一个域名反向代理，并且后端挂在同源 `/api` 下，可以在 `frontend/.env` 中设置空值 `VITE_API_BASE=`。前端会按当前页面协议自动把 WebSocket 转成 `ws://` 或 `wss://`。

完整环境变量说明见 [docs/TECHNICAL.md](docs/TECHNICAL.md#5-配置项)。

## 开发验证

```bash
python -m unittest discover -s tests
python -m ruff check core backend tests
python -m compileall -q core backend tests
cd frontend
npm run build
```

如果已有本地数据库，也可以运行：

```bash
python -m core.scripts.smoke
```

`smoke` 会实际抽样验证关键词检索、上下文追溯、时间浏览、统计和会话块检索；未导入数据或关键检索步骤失败时会以非零状态退出。
