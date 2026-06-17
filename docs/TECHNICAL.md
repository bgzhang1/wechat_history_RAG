# 微信聊天记录检索 Agent 技术文档

本文档面向开发、维护和二次扩展，尽量把项目的运行机制、数据结构、关键取舍和优化方向写清楚。项目当前是一个完整的 RAG 应用系统，包含：
1. **底层核心 (Core)**：导入 WeFlow 导出的微信聊天 JSON，写入 SQLite，构建全文索引和可选向量索引，再通过 LangChain 工具调用让聊天模型查询本地聊天记录。
2. **后端服务 (Backend)**：提供 FastAPI REST 接口与 SSE 流式推送接口，允许任何 Web UI 接入。

## 1. 项目目标

本项目解决的问题是：把个人微信聊天记录变成一个可以自然语言查询、可追溯原文、可浏览上下文的本地知识库。

核心能力包括：

- 导入 WeFlow 导出的微信聊天 JSON。
- 只保留文本消息和引用消息，跳过图片、表情、文件等暂未解析类型。
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

最低建议环境：
- Python 3.10 或更高版本。
- SQLite 支持 FTS5。
- 需要加载 `sqlite-vec`，否则语义检索将退化为 FTS5 关键词回退查询。

## 5. 配置项

配置通过 `.env` 读取。详情也可参考后端 `/api/settings` 的动态配置项。

### 5.1 主聊天模型
| 变量 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `CHAT_BASE_URL` | 是 | 无 | OpenAI 兼容聊天模型 API 地址 |
| `CHAT_API_KEY` | 是 | 无 | 聊天模型 API Key |
| `CHAT_MODEL` | 是 | 无 | 交互式 Agent 使用的模型 |
| `CHAT_TIMEOUT` | 否 | `300` | 单次聊天请求超时时间，秒 |

### 5.2 数据库
| 变量 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `CHAT_DB` | 否 | `runtime/chat.db` | SQLite 数据库路径。建议留在 `runtime/` |

### 5.3 Embedding
| 变量 | 必填 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `EMBED_BASE_URL` | 否 | 无 | OpenAI 兼容 embedding API 地址 |
| `EMBED_API_KEY` | 否 | 无 | Embedding API Key |
| `EMBED_MODEL` | 否 | 无 | Embedding 模型名称 |
| `EMBED_DIM` | 否 | `1024` | 初始化向量表维度，导入时按接口响应自动修正 |

*(其余配置请参看 `.env.example`)*

## 6. 数据模型

SQLite 位置由 `CHAT_DB` 控制。使用了 `WAL` 模式等高吞吐配置。

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
使用 `sqlite-vec` 创建。它将大模型返回的嵌入向量 `FLOAT[N]` 与 `sessions` 结构建立起一对一关联。若 API 切换模型造成维度不匹配，该表会自动 Drip & Re-create。

## 7. 导入和索引流水线

可以通过后端 API `/api/ingest/start` 触发，也可以直接利用 CLI 触发：
```bash
python -m core.ingest local/data
```

流程重点包括：
1. **防重触发**：比对 `ingest_files` 避开未变动的文件。
2. **文本清洗**：仅 `TEXT` 与引用等基础类别会被录入；丢弃空记录。
3. **Chunking**：如果两句话间歇大(>30min)、或者聚合字节大(>800字符)、或者消息条数到达上限(60)，则切分为独立 Chunk。每个 Chunk 追加前块部分对话作为重叠层（Overlap Context）。
4. **LLM 并发与摘要补全**：若不存在对应 Hash，则请求 LLM 将 Chunk 精简成 `summary`。使用批处理减小连接数，如果遭遇 `422/BadRequest` 长度报错则自我截断 Fallback。
5. **Vec 映射**：随后立即交给 Embedding 模型，完成后补发到 `sessions_vec`。

整个建库设计了**自愈(Self-healing)**：任意一次断电，下次跑脚本时系统会主动寻找存在消息但缺失 Summary/Embed 的 Chunk 并进行重算补齐。

## 8. 检索设计

### 8.1 FTS 与语义检索 (`semantic_search`)
结合了 `messages_fts` 关键词硬查与 `sessions_vec` 语义余弦相似。
通过 **Reciprocal Rank Fusion (RRF)** 整合两端的得分，排序之后截取。由于是查询 `sessions` 粒度，检索到后会获得该话题内的多条聊天记录。

### 8.2 消息上下文 (`get_context`)
在检索到某个确切的回答节点后，可通过在 DB 提取对应 `thread` 中 `seq` 上下前 15 条甚至 50 条消息作为佐证。

### 8.3 时段浏览 (`browse_by_time`)
通过针对时间索引的过滤，获取一整片日期的日志记录流。配合 Agent，常用于解答“上周聊过啥”的问题。

## 9. FastAPI 后端与流式 Agent

在 `backend/agent_stream.py` 内实现，是对旧的 CLI 同步 Agent 循环的 SSE 改造。

### 9.1 SSE 通信契约
- **`tool_call`**: 触发工具执行前，向下吐出名称及请求参数供用户界面提示。
- **`tool_result`**: 工具收口。由于 RAG 工具响应非常厚重(常有几百甚至上万 Token)，系统只生成摘要摘要如 `{"summary": "获取了 20 条消息"}`。
- **`text`**: 大模型的自然语言推理切片打字机吐字。
- **`done`**: 全局完结信标，且会携带当前上下文保存使用的 `session_id`。
- **`error`**: 阻断级系统异常抛出。

### 9.2 Agent Tool Loop
Agent `run_agent()` / `stream_agent()` 工作逻辑为标准 Tool Call Loop。内置 `search_messages`, `semantic_search`, `get_context`, `browse_by_time`, `get_stats` 五个可由用户通过 `/api/settings` 动态关停的 Tools 工具组。若达到最大的请求循环（`MAX_ROUNDS` 为100），系统会强制阻断并在当前已持有的 Context 中命令大模型强行得出归纳（Synthesize）。

## 10. 常用命令维护流程

请确保你不在项目中含有 `local/` `runtime/` `.env` 的情况下往外分发镜像或推入公有仓库。

- 开发检查后端与库体编译验证：
  ```bash
  python -m compileall -q core backend
  ```
- 强行修复/更新本地 Embedding：
  ```bash
  python -m core.ingest local/data --force-embeddings
  ```
- 强行彻底清洗重做所有的 RAG 元数据：
  ```bash
  python -m core.ingest local/data --force-rebuild
  ```
- 启动 Uvicorn 后端：
  ```bash
  python -m uvicorn backend.main:app --reload
  ```
- CLI 无端点游玩模式：
  ```bash
  python -m core.cli
  ```

## 11. 未来优化方向

1. **富媒体处理**：针对目前抛弃的 `[图片]` `[文件]` 类型进行外部 OCR / Hash 定位记录，存入占位符内供 FTS 查找。
2. **多线程安全性**：目前 API 不支持并发插入 Ingest。SQLite 对于并发请求写时 WAL 已足够支撑普通前端访问，但异步多源插入可以依靠消息队列或外部任务池（例如 Celery）重构 `ingest.py`。
3. **更细化的脱敏导出**：如果为了共享库，则应当基于 `ingest.py` 再写一套混淆映射生成出可被外发的新版 `sqlite` 源文件。
