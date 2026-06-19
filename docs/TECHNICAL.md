# 微信聊天记录检索 Agent 技术文档

本文档面向开发、维护和二次扩展，尽量把项目的运行机制、数据结构、关键取舍和优化方向写清楚。项目当前是一个完整的 RAG 应用系统，包含：
1. **底层核心 (Core)**：导入 WeFlow 导出的微信聊天 JSON，写入 SQLite，构建全文索引和可选向量索引，再通过 LangChain 工具调用让聊天模型查询本地聊天记录。
2. **后端服务 (Backend)**：提供 FastAPI REST 接口与 SSE 流式推送接口，允许任何 Web UI 接入。

## 1. 项目目标

本项目解决的问题是：把个人微信聊天记录变成一个可以自然语言查询、可追溯原文、可浏览上下文的本地知识库。

核心能力包括：

- 导入 WeFlow 导出的微信聊天 JSON。
- 保留文本、引用以及带可读文本的链接、转账、位置、表情说明、图片 OCR、语音转写和视频说明等消息；纯图片、视频、语音等没有 OCR/转写文本的富媒体仍跳过。
- 将消息写入本地 SQLite 数据库。
- 构建 FTS5 全文索引，用于关键词和原文片段检索。
- 将聊天按时间、长度和消息数切分为会话块。
- 调用大模型生成会话块摘要与向量索引（可选）。
- 暴露为 LangChain Agent Tools，支持检索、上下文追踪、时间浏览和统计查询。
- **FastAPI 提供 SSE 接口流式输出 Agent 思考过程（Tool Call/Result）和生成文本。**
- **支持后台异步建库及上传功能，便于 Web 化一键式部署。**

隐私保证：`.env`、`runtime/`、`local/` 目录默认在 `.gitignore` 中被排除了，任何隐私信息均不会提交到 Git。

## 2. 总体架构

```mermaid
flowchart LR
    subgraph Ingestion Pipeline
        A["WeFlow JSON"] --> B["core.parser"]
        B --> C["messages table"]
        C --> D["messages_fts FTS5"]
        C --> E["core.chunker"]
        E --> F["sessions table"]
        F --> G["summary generation"]
        G --> F
        F --> H["embedding generation"]
        H --> I["sessions_vec sqlite-vec"]
    end

    subgraph Query Execution
        D --> J["core.store & core.retrieval"]
        I --> J
        J --> K["LangChain Tools"]
        K --> L["core.agent"]
    end
    
    subgraph APIs & Interfaces
        M["Web UI / HTTP Client"] <-->|SSE Stream| N["backend.agent_stream"]
        N --> L
        M <-->|REST API| O["backend.routers (ingest, settings, stats)"]
        O --> J
        O --> Ingestion Pipeline
    end
```

## 3. 目录结构

```text
wechat_agent/
  backend/            # FastAPI 服务端点层
    __init__.py
    main.py           # FastAPI 入口，挂载所有路由
    agent_stream.py   # SSE Agent 流式引擎
    schemas.py        # Pydantic 进出模型定义
    API_DOCS.md       # 后端 API 文档
    routers/
      chat.py         # 对话端点
      ingest.py       # 文件上传、异步处理
      settings.py     # 动态设定系统Prompt及模型配置
      stats.py        # 首页大盘数据接口
  core/               # 核心库，被 backend 依赖
    __init__.py
    agent.py          # Agent 主循环、系统提示词、工具调用调度
    chunker.py        # 将连续聊天消息切分为会话块
    cli.py            # 供调试和本地使用的交互式命令行入口
    console.py        # Windows/终端 UTF-8 输出设置
    ingest.py         # 导入、增量索引、摘要和 embedding 流水线
    llm.py            # ChatOpenAI/OpenAIEmbeddings 客户端和重试封装
    parser.py         # WeFlow JSON 标准化解析
    retrieval.py      # 语义检索、FTS+向量融合
    store.py          # SQLite schema、读写、全文检索、向量检索
    tools.py          # LangChain tool 定义和参数校验
    scripts/          # 维护脚本
      check.py        # 模型/embedding 端点连通性检查
      smoke.py        # 本地检索链路冒烟脚本
  docs/               # 技术文档
    TECHNICAL.md      # 当前技术文档
    CHANGELOG.md      # 更新日志
  local/              # 本地原始数据，Git 忽略
  runtime/            # 本地数据库，Git 忽略
  .env.example        # 环境变量模板
  requirements.txt
  README.md
```

## 4. 运行环境和依赖

项目依赖在 `requirements.txt` 中声明：

| 依赖 | 用途 |
| --- | --- |
| `fastapi`, `uvicorn`, `python-multipart` | 提供 Web 路由、API、SSE 事件及异步请求 |
| `langchain`, `langchain-openai` | Agent 消息结构、Tool Calling 及大模型交互 |
| `openai` | 底层 OpenAI 兼容 SDK |
| `python-dotenv`, `pydantic` | 从 `.env` 加载本地配置以及 Schema 校验 |
| `sqlite-vec` | SQLite 的轻量级本地向量检索扩展 |
| `ruff` | 开发期静态检查，确保后端和核心库保持一致风格 |

最低建议环境：
- Python 3.10 或更高版本。
- SQLite 支持 FTS5。
- 需要加载 `sqlite-vec`，否则语义检索将退化为 FTS5 关键词回退查询。

## 5. 配置项

配置通过 `.env` 读取。详情也可参考后端 `/api/settings` 的动态配置项。
设置页会返回当前生效配置和只读 `available_tools` 工具清单，但持久化文件只保存非密钥运行时覆盖值；当 `chat_model` 或 `chat_timeout` 与 `.env` / 环境变量一致时不会写成覆盖值，因此后续修改 `.env` 后重启仍会生效。

### 5.1 主聊天模型
| 变量 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `CHAT_BASE_URL` | 是 | 无 | OpenAI 兼容聊天模型 API 地址 |
| `CHAT_API_KEY` | 是 | 无 | 聊天模型 API Key |
| `CHAT_MODEL` | 是 | 无 | 交互式 Agent 使用的模型 |
| `CHAT_TIMEOUT` | 否 | `300` | 单次聊天请求超时时间，秒 |
| `CHAT_MAX_RETRIES` | 否 | `3` | OpenAI SDK 层重试次数 |
| `CHAT_LOCAL_RETRIES` | 否 | `3` | 本地调用包装的总尝试次数，包含第一次请求 |
| `CHAT_RETRY_SLEEP` | 否 | `1` | 本地重试间隔，秒 |

### 5.2 数据库
| 变量 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `CHAT_DB` | 否 | `runtime/chat.db` | SQLite 数据库路径。建议留在 `runtime/`；也支持 `file:` SQLite URI |
| `BACKEND_CHAT_DB` | 否 | `runtime/backend_chat.db` | Web 对话会话持久化数据库路径；也支持 `file:` SQLite URI |
| `BACKEND_SETTINGS_FILE` | 否 | `runtime/backend_settings.json` | 设置页保存的非密钥运行时配置路径；重置设置会删除该文件 |

### 5.3 Embedding
| 变量 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `EMBED_BASE_URL` | 否 | 无 | OpenAI 兼容 embedding API 地址 |
| `EMBED_API_KEY` | 否 | 无 | Embedding API Key |
| `EMBED_MODEL` | 否 | 无 | Embedding 模型名称 |
| `EMBED_DIM` | 否 | `1024` | 初始化向量表维度，导入时按接口响应自动修正 |
| `EMBED_TIMEOUT` | 否 | `90` | 单次 embedding 请求超时时间，秒 |
| `EMBED_MAX_RETRIES` | 否 | `0` | OpenAI SDK 层重试次数 |
| `EMBED_LOCAL_RETRIES` | 否 | `3` | 本地调用包装的总尝试次数，包含第一次请求 |
| `EMBED_RETRY_SLEEP` | 否 | `1` | 本地重试间隔，秒 |

### 5.4 导入与索引
| 变量 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `SUMMARY_MODEL` | 否 | 无 | 摘要生成模型；未设置时跳过索引期摘要 |
| `SUMMARY_WORKERS` | 否 | `2` | 摘要批处理并发数 |
| `SUMMARY_BATCH_SIZE` | 否 | `4` | 单批摘要会话块数量 |
| `SUMMARY_MAX_CHARS` | 否 | `3000` | 摘要输入最大字符数 |
| `SUMMARY_FALLBACK_CHARS` | 否 | `1200` | 长度错误后的摘要输入回退字符数 |
| `EMBED_WORKERS` | 否 | `4` | Embedding 批处理并发数 |
| `EMBED_BATCH_SIZE` | 否 | `32` | 单批 embedding 会话块数量 |
| `PROGRESS_EVERY` | 否 | `50` | 导入 CLI 每处理多少条输出一次进度 |
| `PROGRESS_INTERVAL` | 否 | `15` | 导入 CLI 进度输出最小间隔，秒 |
| `INGEST_KEEP_GOING` | 否 | `false` | 摘要或 embedding 单批失败后是否继续处理其它批次 |
| `INGEST_MAX_UPLOAD_MB` | 否 | `512` | Web 上传 JSON 最大大小，MB |
| `INGEST_MAX_TASKS` | 否 | `100` | 后端进程内保留的导入任务数量 |
| `INGEST_MAX_TASK_LOG_LINES` | 否 | `5000` | 单个导入任务保留的内存日志行数 |
| `INGEST_MAX_TASK_LOG_LINE_CHARS` | 否 | `4000` | 单行导入任务内存日志最大字符数 |

### 5.5 后端与前端
| 变量 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `HOST` | 否 | `0.0.0.0` | 直接运行 `backend.main` 时监听地址 |
| `PORT` | 否 | `8000` | 直接运行 `backend.main` 时监听端口 |
| `CORS_ORIGINS` | 否 | 空 | 额外允许的浏览器来源，多个来源用英文逗号分隔；后端默认已放行本机 Vite 常用端口 `5173-5180` |
| `BACKEND_LOG_FILE` | 否 | `runtime/backend.log.jsonl` | 后端 JSONL 诊断日志路径；近期日志 API 会从当前文件尾部倒序读取，不足时继续读取 `.1` 轮转日志，避免日志变大后刷新面板时全量扫描 |
| `BACKEND_LOG_MAX_MB` | 否 | `10` | 后端日志轮转大小，MB |
| `VITE_API_BASE` | 否 | 当前页面主机名的 `:8000` | 前端构建时使用的后端地址，通常本地开发无需设置；显式设为空值时使用当前同源 `/api`，WebSocket 会自动匹配 `ws://` / `wss://` |

## 6. 数据模型

SQLite 位置由 `CHAT_DB` 控制。普通文件路径会自动创建父目录；以 `file:` 开头时按 SQLite URI 打开，便于高级部署或测试使用共享内存数据库。使用了 `WAL` 模式等高吞吐配置。

### 6.1 `messages`
核心单条消息实体。包括 `id` (主键)、`sender` (发送人)、`timestamp` (ISO时区时间)、`content` (正文)、`thread` (会话名) 及 `seq` (单会话序号) 等。常用索引涉及时间、发送者及所属会话。

### 6.2 `messages_fts`
FTS5 external content 表。使用 `trigram` Tokenizer 构建，确保中文片段不依赖额外分词器即可检索。查询时少于 3 字长度会被 `LIKE` 回退兼容。

### 6.3 `sessions`
会话块聚合。包含 `thread`, `start_time`, `end_time`, `msg_ids`, `text` (前缀及正文)。额外记录 `summary` (大模型摘要) 与 `text_hash` (防复写用 SHA-256 摘要)。

### 6.4 `msg_session`
FTS `messages` 消息映射到 `sessions` 会话块的关联查询表。

### 6.5 `ingest_files`
记录文件级的 `mtime_ns` 及大小。通过它能够确保在增量导入阶段，没有变化的文件直接被 `parser` 跳过。

### 6.6 `sessions_vec`
使用 `sqlite-vec` 创建。它将大模型返回的嵌入向量 `FLOAT[N]` 与 `sessions` 结构建立起一对一关联。若 API 切换模型造成维度不匹配，该表会自动 Drop & Re-create。

## 7. 导入和索引流水线

可以通过后端 API `/api/ingest/start` 触发，也可以直接利用 CLI 触发：
```bash
python -m core.ingest local/data
```
目录参数会递归收集 `.json` 文件，并按路径稳定排序后处理。

流程重点包括：
1. **防重触发**：比对 `ingest_files` 避开未变动的文件。
   `ingest_files` 同时记录解析规则版本；解析器升级后，旧记录会被视为需重新导入，即使源 JSON 的大小和修改时间没有变化。
2. **消息归一化**：文本、引用、系统消息、聊天记录、链接、转账、位置、小程序、文件名、通话记录、名片、表情说明以及 JSON 已带的图片 OCR、语音转写、视频说明等可读文本会被录入；纯图片、视频、语音等没有 OCR/转写文本的富媒体仍跳过，裸 `[表情包]` 这类无语义占位符和空记录也会丢弃。解析器兼容顶层 `messages/messageList/msgList/chatRecords/records` 消息列表别名，兼容 `type/msgType/messageType/typeName`、数字消息码、`createTime/timestamp/createdAt/formattedTime/msgCreateTime` 等常见导出字段别名，以及秒/毫秒/微秒 Unix 时间戳和 ISO 时间字符串；如果较早的别名字段为空、`null` 或时间无效，会继续尝试后续有效别名，避免真实导出中的空占位字段导致消息漏导。结构化 `content/text/message/body/msgContent/plainText` 会优先抽取正文、标题、摘要、链接、文件名、地址、金额等字段并合并为可检索文本；媒体消息只读取 `ocrText`、`captionText`、`transcription`、`voiceText` 等明确可读字段，不把原始图片、语音或视频 payload 当正文索引。引用消息既支持顶层 `quotedContent/quotedSender`，也支持 `quote/refer/reply` 等嵌套引用对象；空的嵌套引用对象会被跳过，发送人和正文会从同一个有效对象读取，避免错配。所有导出消息 ID（包括 `platformMessageId/serverId/newMsgId/msgSvrId`、`msgId/messageId/clientMsgId/id` 和 `localId/localMsgId`）都会加上文件作用域，避免多个 JSON 中相同 ID 互相覆盖；旧库中的未加作用域 ID 会在重导时按时间、类型、会话和内容匹配后升级。部分导出只包含新增回复、缺少被回复消息时，入库层会把 raw 或 basename scoped `reply_to` 解析到同文件作用域下已存在的消息 ID，避免上下文追溯断链。文件来源映射只记录当前数据库中真实存在的消息 ID，避免旧脚本或异常重导留下悬空映射后误导文件级索引状态。群聊发送人会按 `senderDisplayName`、`senderRemark`、`senderNickname`、`senderUsername` 逐级回退，避免缺少显示名时误把所有消息归到群名下。
3. **Chunking**：分块前会按时间和消息 ID 稳定排序。如果两句话间歇大(>30min)、或者聚合字节大(>800字符)、或者消息条数到达上限(60)，则切分为独立 Chunk。每个 Chunk 追加前块部分对话作为重叠层（Overlap Context）。多行消息在会话块文本里会给每一行重复发送人前缀，避免摘要和 embedding 把续行误解为无归属文本。
4. **LLM 并发与摘要补全**：若不存在对应 Hash，则请求 LLM 将 Chunk 精简成 `summary`。使用批处理减小连接数，如果遭遇 `422/BadRequest` 长度报错则自我截断 Fallback。
5. **Vec 映射**：随后立即交给 Embedding 模型，完成后补发到 `sessions_vec`。

当只需要修复已有数据库上的单项索引时，可以追加 `--skip-import`，例如 `python -m core.ingest local/data --skip-import --force-fts`。后端导入面板里的“仅 FTS / 仅向量 / 仅分块 / 仅摘要”模式也使用该语义，避免用户选择单项构建时意外导入尚未入库的 JSON。显式单项模式只执行用户选择的阶段，不会借自愈逻辑顺手调用摘要模型或 embedding；普通增量、全流程和强制重建仍会按需补齐缺失 FTS、分块、摘要和向量。目标是单个 JSON 时，单项索引会按该 JSON 的来源映射和消息 ID 作用域收敛到关联消息/会话块；如果旧库缺少来源映射且无法从消息 ID 前缀定位目标范围，前端会把文件视为“索引状态未知”并只开放增量、全流程或强制重建，后端也会拒绝单项构建，要求先重新解析补齐映射。目标是目录时，单项构建至少要求目录内一个 JSON 已经能定位到已入库消息范围；仅摘要或仅向量还要求已有会话分块，避免未导入目录静默完成 0 条修复。缺失 `seq` 的自愈重算也只触碰目标 JSON 关联会话；如果重新解析期间发现已有消息内容被修正，FTS 会回退到全量刷新以避免外部内容索引残留旧 token。

Web 上传文件仍以随机 UUID 保存，避免暴露或信任客户端文件名；同时上传 sidecar 元数据会记录根据 WeFlow 会话身份生成的稳定 scope。scope 优先使用 `wxid/userName` 等稳定会话标识；导出缺少这些字段时退回会话显示名，以支持同一聊天的追加导出。解析器优先使用该 scope 生成消息 ID 前缀，因此同一聊天记录重复上传、改名上传或追加导出时会按同一逻辑会话增量合并，而不是因为新的上传 UUID 产生重复消息。早期已用随机上传路径入库的消息，在重新导入稳定 scope 上传文件时会按同一 raw 消息 ID 后缀和时间、类型、会话、reply 等字段安全升级到新前缀，并保留会话块与文件来源映射。

Web 导入任务由 `backend.routers.ingest` 启动后台子进程执行。后端会给子进程设置 `INGEST_PROGRESS_JSON=true`，此时 `core.ingest` 会额外输出以 `__INGEST_PROGRESS__ ` 开头的结构化进度事件；这些事件只更新任务内存状态，不进入用户可见日志。普通 CLI 不设置该变量，因此仍保持原有纯文本输出。前端通过 `WS /api/ws/ingest/{task_id}` 获取 `stage`、`progress`、`message`、`eta` 和 `log_tail`，页面刷新时可从 `/api/ingest/tasks` 恢复轻量进度。若 JSON 已解析并写入消息库，但后续摘要或 embedding 阶段因模型/API 错误停止，导入记录会先落库；文件列表仍能显示该 JSON 已同步以及当前环境可修复的缺摘要/缺向量数量，用户可直接执行单项修复。未配置 `SUMMARY_MODEL` 时缺摘要不会被当作批量待处理缺口；embedding 配置或 `sqlite-vec` 不可用时缺向量也会显示为不可用，避免重复启动不会修复任何内容的任务。

整个建库设计了**自愈(Self-healing)**：任意一次断电，下次跑脚本时系统会主动寻找存在消息但缺失 Summary/Embed 的 Chunk 并进行重算补齐；普通增量导入会先检查文件状态，只有源文件变化或解析规则过期时才重新解析。

## 8. 检索设计

### 8.1 FTS 与语义检索 (`semantic_search`)
结合了 `messages_fts` 关键词硬查与 `sessions_vec` 语义余弦相似。
通过 **Reciprocal Rank Fusion (RRF)** 整合两端的得分，排序之后截取。由于是查询 `sessions` 粒度，检索到后会获得该话题内的多条聊天记录。
当用户提供会话名或时间范围时，FTS 与向量检索都会把过滤条件下推到候选召回阶段，而不是只在最终结果里过滤；这样大库中某个群聊或日期范围内的相关会话块不会被全库 top-k 向量结果挤掉。若过滤范围内没有语义/全文命中，系统才会返回该范围内最近会话块作为明确标注的上下文兜底。

### 8.2 消息上下文 (`get_context`)
在检索到某个确切的回答节点后，可通过在 DB 提取对应 `thread` 中 `seq` 上下前 15 条甚至 50 条消息作为佐证。

### 8.3 时段浏览 (`browse_by_time`)
通过针对时间索引的过滤，获取一整片日期的日志记录流。配合 Agent，常用于解答“上周聊过啥”的问题。

## 9. FastAPI 后端与流式 Agent

在 `backend/agent_stream.py` 内实现，是对旧的 CLI 同步 Agent 循环的 SSE 改造。

### 9.1 SSE 通信契约
- **`tool_call`**: 触发工具执行前，向下吐出名称及请求参数供用户界面提示。
- **`tool_result`**: 工具收口。由于 RAG 工具响应非常厚重(常有几百甚至上万 Token)，系统只生成轻量摘要，例如 `{"summary": "命中 23 条消息，返回 20 条"}`。
- **`text`**: 大模型的自然语言推理切片打字机吐字。
- **`done`**: 全局完结信标，且会携带当前上下文保存使用的 `session_id`。
- **`error`**: 阻断级系统异常抛出。

### 9.2 Agent Tool Loop
Agent `run_agent()` / `stream_agent()` 工作逻辑为标准 Tool Call Loop。内置 `search_messages`, `semantic_search`, `get_context`, `browse_by_time`, `get_stats` 五个可由用户通过 `/api/settings` 动态关停的 Tools 工具组；设置响应会从后端注册表暴露 `available_tools`，前端据此渲染可选工具，避免工具新增或下线时产生前后端硬编码漂移。每轮发送给模型的系统提示词都会追加当前启用/停用工具策略，因此即使自定义提示词仍提到已停用工具，模型也只能调用当前启用工具，或明确告知对应能力未启用。若达到最大的请求循环（`MAX_ROUNDS` 默认 12），系统会强制阻断并提示用户缩小时间、人物或关键词范围。
工具参数在进入数据库前会先做 Pydantic 校验和文本归一化：必填查询、消息 ID 会压缩空白并拒绝空值，可选发送人/会话过滤词为空时视为未传；查询文本、过滤词和消息 ID 都有长度上限，返回窗口也会被限制在真实前端可承受的范围内（`search_messages.limit <= 100`、`semantic_search.limit <= 20`、`get_context.before/after <= 50`、`browse_by_time.limit <= 200`）。如果模型生成的工具参数不是 JSON 对象，执行层会返回可读错误，要求重新生成结构化参数。

## 10. 常用命令维护流程

请确保你不在项目中含有 `local/` `runtime/` `.env` 的情况下往外分发镜像或推入公有仓库。

- 开发检查后端与库体编译验证：
  ```bash
  python -m unittest discover -s tests
  python -m ruff check core backend tests
  python -m compileall -q core backend tests
  cd frontend
  npm run build
  ```
- 仅基于已有数据库修复/更新单项索引：
  ```bash
  python -m core.ingest local/data --skip-import --force-fts
  python -m core.ingest local/data --skip-import --force-chunks
  python -m core.ingest local/data --skip-import --force-summary
  python -m core.ingest local/data --skip-import --force-embeddings
  ```
  Web 前端的“设置 -> 数据导入”面板也提供对应的仅 FTS、仅分块、仅摘要和仅向量构建，并会显示每个 JSON 文件的任务进度；针对单个 JSON 操作时，索引构建范围会收敛到该文件关联的消息和会话块。
  `--skip-import` 单项索引构建必须命中至少一个 JSON 目标，并且这些目标已经能定位到已入库消息或会话块范围；若目录或路径没有匹配到 JSON，或 JSON 从未入库且没有可修复范围，命令会直接失败，避免用户传错路径时误触发全库索引重建或静默完成 0 条修复。显式单项命令严格限定阶段，例如 `--force-fts` 不会补摘要或向量，`--force-embeddings` 会直接基于已有会话块文本和已有摘要生成向量，不会为缺摘要块额外调用摘要模型。
- 重新解析 JSON 并核对消息入库，不强制重建完整索引：
  ```bash
  python -m core.ingest local/data --force-import
  ```
  Web 前端的“全流程导入”使用这一语义，会重新读取目标 JSON，并在发现新增/更新或缺失索引时继续补齐后续阶段。
- 按目标 JSON 关联范围强制重建 RAG 元数据：
  ```bash
  python -m core.ingest local/data --force-rebuild
  ```
  `--force-rebuild` 会绕过文件未变化检查，重新解析目标 JSON 后再重建其关联范围的索引数据；FTS 优先刷新目标 JSON 关联消息，若已有消息内容发生修正则全量刷新，适合解析规则升级或需要修复目标文件相关索引时使用。
- 启动 Uvicorn 后端：
  ```bash
  python -m uvicorn backend.main:app --reload
  ```
- CLI 无端点游玩模式：
  ```bash
  python -m core.cli
  ```
- 本地检索链路冒烟检查：
  ```bash
  python -m core.scripts.smoke
  ```
  该脚本会抽样验证关键词检索、上下文追溯、时间浏览、统计和会话块检索；未导入数据或关键步骤失败时返回非零退出码。

## 11. 未来优化方向

1. **富媒体处理**：当前会读取 JSON 已自带的图片 OCR、语音转写和视频说明；后续可接入外部 OCR / ASR / Hash 定位，把生成结果回填为明确文本字段后再进入 FTS。
2. **多线程安全性**：目前 API 不支持并发插入 Ingest。SQLite 对于并发请求写时 WAL 已足够支撑普通前端访问，但异步多源插入可以依靠消息队列或外部任务池（例如 Celery）重构 `ingest.py`。
3. **更细化的脱敏导出**：如果为了共享库，则应当基于 `ingest.py` 再写一套混淆映射生成出可被外发的新版 `sqlite` 源文件。
