# WeChat RAG Agent 后端 API 文档

默认服务地址：`http://localhost:8000`

后端基于 FastAPI，前端主要通过 Agent 对微信聊天记录进行检索问答，同时提供会话管理、导入任务、系统设置、统计概览、健康诊断和输入建议能力。

## 通用错误格式

所有 HTTP 异常会尽量返回结构化错误，便于前端判断错误类型和恢复方式。

```json
{
  "error": {
    "code": "HTTP_404",
    "type": "http_error",
    "message": "会话不存在：xxx",
    "recoverable": true,
    "action": "请刷新页面或选择其他项目。",
    "path": "/api/chat/xxx/messages"
  }
}
```

常见状态码：

| 状态码 | 场景 |
|---|---|
| `400` | 参数不合法，例如非法 `file_id` |
| `404` | 会话、文件或任务不存在 |
| `409` | 会话正在生成、已有导入任务运行 |
| `413` | 上传文件超过限制 |
| `422` | 请求体校验失败 |
| `500` | 后端未预期异常 |
| `503` | 模型或服务未配置 |

## 1. 对话接口

### 1.1 发起 Agent 对话

`POST /api/chat`

以 SSE 返回 Agent 的工具调用过程和最终回答。新会话会自动创建并持久化，刷新页面后可通过会话接口恢复。

请求体：

```json
{
  "question": "搜索一下上个月我和张三讨论了什么关于旅游的事情",
  "session_id": "可选；已有会话 ID，不传则自动创建"
}
```

`question` 会去掉首尾空白，长度范围为 `1-8000`；`session_id` 如传入也会去掉首尾空白，长度范围为 `1-120`。

响应类型：`text/event-stream`

事件示例：

```text
event: session
data: {"session_id":"b0f7e...","status":"running"}

event: tool_call
data: {"name":"search_messages","args":"{\"query\":\"旅游\"}"}

event: tool_result
data: {"name":"search_messages","summary":"命中 23 条消息，返回 20 条"}

event: text
data: {"chunk":"上个月你们主要聊到..."}

event: done
data: {"answer":"上个月你们主要聊到...","session_id":"b0f7e..."}
```

SSE 事件：

| 事件 | 说明 |
|---|---|
| `session` | 本轮使用的会话 ID；新会话第一时间可拿到它，用于停止生成 |
| `tool_call` | Agent 准备调用检索工具 |
| `tool_result` | 检索工具完成，并返回摘要 |
| `text` | 模型输出片段 |
| `done` | 本轮完成，返回完整回答和 `session_id` |
| `error` | Agent 执行失败 |

### 1.2 停止生成

`POST /api/chat/{session_id}/abort`

用于前端“停止生成”按钮。远程模型请求本身无法被瞬时杀掉，但后端会在模型返回、工具调用前后、流式输出片段之间尽快停止。

请求体可选。前端如果已经拿到部分回答，可以一并提交，后端会把本轮问答以“已停止生成”的形式保存，避免刷新后丢失这一轮记录。`partial_answer` 会 trim 并限制在 20000 字符内，避免长回答停止请求因校验失败而丢失本地已生成内容：

```json
{
  "question": "刚才问的问题",
  "partial_answer": "已经流式输出的部分回答"
}
```

响应：

```json
{
  "message": "Abort requested.",
  "status": "aborting"
}
```

如果当前没有生成任务：

```json
{
  "message": "No active generation for this session.",
  "status": "idle"
}
```

## 2. 会话管理

会话存储在 `runtime/backend_chat.db`，服务重启后仍可恢复。

### 2.1 列出会话

`GET /api/chat/sessions?limit=100&offset=0`

响应：

```json
{
  "total_count": 1,
  "returned": 1,
  "offset": 0,
  "items": [
    {
      "session_id": "b0f7e...",
      "title": "上个月旅游讨论",
      "status": "idle",
      "last_error": null,
      "created_at": "2026-06-17T08:00:00Z",
      "updated_at": "2026-06-17T08:10:00Z",
      "message_count": 4,
      "last_question": "上个月发生了什么？"
    }
  ]
}
```

### 2.2 获取会话消息

`GET /api/chat/{session_id}/messages?limit=500&offset=0`

查询参数：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `limit` | 不限制 | 可选，返回最近多少条消息，范围 `1-1000` |
| `offset` | `0` | 从最新消息向前分页的偏移；仅传 `limit` 时生效 |

响应：

```json
{
  "total_count": 2,
  "returned": 2,
  "offset": 0,
  "items": [
    {
      "id": 1,
      "role": "user",
      "content": "你好",
      "created_at": "2026-06-17T08:00:00Z"
    },
    {
      "id": 2,
      "role": "assistant",
      "content": "你好，有什么想查的聊天记录？",
      "created_at": "2026-06-17T08:00:02Z"
    }
  ]
}
```

### 2.3 获取会话状态

`GET /api/chat/{session_id}/status`

响应：

```json
{
  "session_id": "b0f7e...",
  "title": "上个月旅游讨论",
  "status": "running",
  "last_error": null,
  "created_at": "2026-06-17T08:00:00Z",
  "updated_at": "2026-06-17T08:10:00Z"
}
```

`status` 可能值：`idle`、`running`、`aborting`、`error`。

### 2.4 重命名会话

`PATCH /api/chat/{session_id}`

请求体：

```json
{
  "title": "上个月旅游讨论"
}
```

响应为更新后的会话对象。

如果会话正在生成或正在中止，接口返回 `409`，需要先停止生成再重命名。

### 2.5 删除单个会话

`DELETE /api/chat/{session_id}`

如果会话正在生成或正在中止，接口返回 `409`，需要先停止生成再删除。

响应：

```json
{
  "message": "会话 b0f7e... 已删除。"
}
```

### 2.6 批量删除会话

`DELETE /api/chat/sessions`

部分客户端不方便给 `DELETE` 传 body 时，可使用等价接口：

`POST /api/chat/sessions/delete`

请求体：

```json
{
  "session_ids": ["b0f7e...", "a1c2e..."]
}
```

每个 `session_id` 长度范围为 `1-120`，单次最多删除 `200` 个会话；正在生成或停止中的会话会保留在 `active` 中。

响应：

```json
{
  "deleted": ["b0f7e..."],
  "missing": ["a1c2e..."],
  "active": []
}
```

`active` 表示正在生成或正在中止、因此被保留的会话 ID。

## 3. 系统设置

### 3.1 获取设置

`GET /api/settings`

响应：

```json
{
  "system_prompt": "你是微信聊天记录检索助手...",
  "max_rounds": 12,
  "max_history_messages": 40,
  "chat_model": "gpt-4o",
  "summary_model": "gpt-4o-mini",
  "chat_timeout": 300.0,
  "chat_temperature": 0.0,
  "enabled_tools": ["search_messages", "semantic_search", "get_context", "browse_by_time", "get_stats"],
  "available_tools": ["search_messages", "semantic_search", "get_context", "browse_by_time", "get_stats"]
}
```

### 3.2 更新设置

`POST /api/settings`

所有字段均可选，只传需要修改的字段。设置页保存的都是非密钥运行时配置，会持久化到 `runtime/backend_settings.json`（可用 `BACKEND_SETTINGS_FILE` 调整路径）；API Key 仍只从 `.env` / 环境变量读取。`chat_model`、`summary_model` 和 `chat_timeout` 只有在不同于当前环境变量值时才作为运行时覆盖值持久化，避免普通保存操作把 `.env` 中的模型配置冻结成旧值。`summary_model` 保存后会作为后续导入子进程的 `SUMMARY_MODEL` 使用。`enabled_tools` 是 Agent 的最终可调用工具白名单，每轮对话会自动把当前启用/停用工具策略追加到系统提示词，避免自定义提示词继续引导模型调用已停用工具。响应中的 `available_tools` 是只读字段，用于前端渲染当前后端注册的工具列表，不会写入持久化设置文件。

```json
{
  "chat_temperature": 0.5,
  "chat_timeout": 120,
  "enabled_tools": ["search_messages", "semantic_search", "get_context"]
}
```

响应为更新后的完整设置。

约束说明：

| 字段 | 约束 |
|---|---|
| `system_prompt` | 最长 20000 字符 |
| `max_rounds` | 1-200 |
| `max_history_messages` | 0-200；为 0 时本轮不携带历史消息 |
| `chat_model` | 最长 200 字符 |
| `summary_model` | 最长 200 字符 |
| `chat_timeout` | 1-1800 秒 |
| `chat_temperature` | 0-2 |
| `enabled_tools` | 至少 1 个，且必须是已知工具名 |
| `available_tools` | 只读响应字段，列出当前后端已注册工具；更新请求不需要传 |

### 3.3 重置设置

`POST /api/settings/reset`

删除持久化运行时设置，恢复代码默认值和 `.env` / 环境变量中的模型配置。

## 4. 数据统计

会话和发送人统计会按修剪后的非空名称聚合；原始消息里缺失或纯空白的 `thread` / `sender` 仍计入消息总数，但不会出现在排行榜和输入建议中。

### 4.1 获取轻量概览

`GET /api/stats`

默认只返回概览，不返回完整 `threads` / `senders` 大数组，适合首页初始化。

等价接口：

`GET /api/stats/summary`

响应：

```json
{
  "total_messages": 123170,
  "time_span": {
    "earliest": "2021-01-29T10:00:00",
    "latest": "2026-06-13T18:30:00"
  },
  "message_types": [
    { "msg_type": "text", "count": 120000 }
  ],
  "indexed_session_chunks": 2789,
  "thread_count": 120,
  "sender_count": 80
}
```

### 4.2 获取带分页详情的统计

`GET /api/stats?include_details=true&thread_limit=50&thread_offset=0&sender_limit=50&sender_offset=0`

响应会额外包含：

```json
{
  "threads": [],
  "threads_page": {
    "total_count": 120,
    "returned": 50,
    "offset": 0
  },
  "senders": [],
  "senders_page": {
    "total_count": 80,
    "returned": 50,
    "offset": 0
  }
}
```

### 4.3 分页获取会话统计

`GET /api/stats/threads?limit=50&offset=0`

响应：

```json
{
  "total_count": 120,
  "returned": 50,
  "offset": 0,
  "items": [
    {
      "thread": "家庭群",
      "count": 12000,
      "earliest": "2021-01-29T10:00:00",
      "latest": "2026-06-13T18:30:00"
    }
  ]
}
```

### 4.4 分页获取发送人统计

`GET /api/stats/senders?limit=50&offset=0`

响应：

```json
{
  "total_count": 80,
  "returned": 50,
  "offset": 0,
  "items": [
    {
      "sender": "ZBG",
      "is_self": true,
      "count": 80171
    }
  ]
}
```

## 5. 健康检查与诊断

### 5.1 健康检查

`GET /api/health`

响应保留旧字段，并新增 `checks` 供前端做细粒度展示。

```json
{
  "status": "degraded",
  "chat_model_configured": true,
  "chat_model": "gpt-4o",
  "chat_model_missing": [],
  "summary_model_configured": true,
  "summary_model": "gpt-4o-mini",
  "summary_model_missing": [],
  "embedding_configured": true,
  "embedding_model": "BAAI/bge-m3",
  "embedding_missing": [],
  "vector_index_available": true,
  "vector_search_available": true,
  "total_messages": 123170,
  "indexed_session_chunks": 2789,
  "thread_count": 120,
  "sender_count": 80,
  "has_data": true,
  "checks": [
    {
      "component": "database",
      "status": "ok",
      "detail": "已索引 123170 条消息。",
      "recoverable": false,
      "action": null,
      "action_target": null
    }
  ]
}
```

`status` 可能值：

| 值 | 说明 |
|---|---|
| `ok` | 核心组件正常 |
| `degraded` | 可用但存在降级，例如向量索引不完整 |
| `error` | 存在阻断性错误 |

`summary_model_configured` 表示当前是否能执行“仅摘要”构建；`vector_index_available` 表示 `sqlite-vec` 能否使用。`vector_search_available` 表示当前已有可用于语义检索的向量；仅安装 `sqlite-vec` 或仅配置 `EMBED_*` 不会让该字段变为 `true`，需要导入流程实际生成至少一个会话块向量。

如果数据库尚未导入任何聊天记录，健康检查会返回 `degraded`，并在 `checks` 中把 `database` 标为 `warning`，提示用户先通过数据导入面板或 `python -m core.ingest local/data` 导入 WeFlow JSON。

`checks[].action_target` 是前端可点击跳转目标；只有能在当前 Web 应用内处理的建议才会返回 `settings`、`ingest` 或 `logs`。例如仅缺 `CHAT_MODEL` 时会跳转设置页，缺 `CHAT_API_KEY` / `CHAT_BASE_URL` 或 `EMBED_*` 时只显示文字建议，因为这些敏感配置仍需在本地环境变量中设置。

### 5.2 详细诊断

`GET /api/health/diagnostics`

响应：

```json
{
  "overall": "degraded",
  "checks": [
    {
      "component": "database",
      "status": "ok",
      "detail": "已索引 123170 条消息。",
      "recoverable": false,
      "action": null,
      "action_target": null
    },
    {
      "component": "vector_index",
      "status": "warning",
      "detail": "sqlite-vec 可用；2777/2789 个会话块已有向量。",
      "recoverable": true,
      "action": "请在数据导入面板执行仅向量构建，或重新运行完整导入以生成缺失向量。",
      "action_target": "ingest"
    }
  ],
  "db_stats": {},
  "chat_status": {},
  "summary_status": {},
  "embed_status": {},
  "vector_index_available": true,
  "vector_search_available": true
}
```

## 6. 数据导入

导入任务通过后台子进程执行，支持取消。任务列表保存在当前后端进程内，可支持页面刷新后的任务状态恢复；如果后端进程重启，历史任务列表会清空。后端默认保留最近 100 个任务，可用 `INGEST_MAX_TASKS` 调整；单个任务的内存日志默认保留最近 5000 行、每行最多 4000 字符，可用 `INGEST_MAX_TASK_LOG_LINES` 和 `INGEST_MAX_TASK_LOG_LINE_CHARS` 调整。

### 6.1 列出可导入文件

`GET /api/ingest/files?limit=100&offset=0`

扫描项目 `local/` 目录下后缀为 `.json`/`.JSON` 的文件，不返回服务器绝对路径。上传文件在磁盘上使用 UUID 安全文件名保存，但列表中的 `filename` 会显示原始上传文件名，便于识别；实际导入请使用 `upload_id` 或 `file_id`。上传文件的 sidecar 元数据会记录稳定聊天 scope；该 scope 优先使用 WeFlow 会话稳定 ID，缺失时退回会话显示名。同一聊天记录重复上传或追加导出时仍会使用同一消息 ID 前缀，避免因物理 UUID 文件名变化造成重复入库。早期已按随机上传路径入库的消息，在重新导入同一聊天的稳定 scope 上传文件时会按 raw 消息 ID 后缀和消息字段安全升级，避免旧数据与新上传并存成重复消息。

每个文件会返回当前导入状态：

- `never`：尚未导入
- `up_to_date`：数据库中的导入记录与当前文件大小、mtime 一致
- `changed`：文件已修改，或当前解析规则版本高于上次导入版本，需要重新导入
- `running` / `cancel_requested`：该文件当前有导入任务

最近一次导入任务的失败状态通过 `task_status: "error"` 和任务详情返回；它不会覆盖文件本身的同步状态。

响应：

```json
{
  "total_count": 2,
  "returned": 2,
  "offset": 0,
  "items": [
    {
      "file_id": "uploads/99ca3f....json",
      "filename": "wechat_history.json",
      "size": 1048576,
      "modified_at": "2026-06-17T08:00:00Z",
      "source": "upload",
      "upload_id": "99ca3f...",
      "ingest_status": "up_to_date",
      "ingest_status_reason": null,
      "last_ingested_at": "2026-06-17 08:10:00",
      "ingest_total": 12000,
      "ingest_included": 11880,
      "ingest_changed": 11880,
      "ingest_inserted": 11880,
      "session_chunks": 320,
      "missing_summary_chunks": 0,
      "missing_vector_chunks": 12,
      "task_id": "a1b2c3...",
      "task_status": "completed",
      "task_mode": "full"
    }
  ]
}
```

`ingest_changed` 表示最近一次成功解析该文件时新增或更新的消息数。`ingest_inserted` 是早期兼容字段，当前与 `ingest_changed` 相同。解析规则升级后，即使文件大小和修改时间不变，旧记录也会显示为 `changed`，以提示重新导入并应用新的消息类型、发送人归属或引用字段识别规则。

`ingest_status_reason` 用于解释 `changed` 的原因：`file_changed` 表示文件大小或修改时间变化；`parser_version_stale` 表示解析规则升级，需要重新解析以纳入新的消息内容和字段归属识别。
解析规则升级可能来自新增字段别名、数字 `type/msgType/messageType` 消息码、顶层消息列表别名、发送人归属、引用字段、嵌套引用对象或媒体可读文本识别，例如 `messageList`、`msgList`、`chatRecords`、`typeName`、`formattedTime`、`msgContent`、`newMsgId`、`quote/refer/reply`、`ocrText`、`transcription` 等导出形态。解析器也会跳过空字符串、`null`、无效时间主字段和空的嵌套引用对象，继续尝试后续有效别名，避免真实导出中的占位字段导致消息漏导或引用发送人错配；图片、语音和视频消息只在 JSON 已带 OCR、转写或说明字段时入库，避免把原始媒体 payload 当成聊天正文。

如果 JSON 已经成功解析并写入消息库，但后续摘要或 embedding 阶段因为模型/API 错误停止，文件导入记录仍会保存；此时文件可显示为 `up_to_date`，同时通过 `missing_summary_chunks` / `missing_vector_chunks` 暴露当前环境可执行的单项修复缺口。

`session_chunks`、`missing_summary_chunks`、`missing_vector_chunks` 只基于已有数据库索引和文件来源映射计算。未导入文件不会返回索引计数；旧版本导入的数据如果缺少来源映射，文件列表也不会为了补齐状态而重新解析大 JSON。未配置 `SUMMARY_MODEL` 时 `missing_summary_chunks` 会返回 `null`，embedding 配置或 `sqlite-vec` 不可用时 `missing_vector_chunks` 会返回 `null`，避免前端把当前无法修复的缺口放入批量待处理。这些场景下用户补齐配置或执行全流程导入/重建后会自动刷新状态。

### 6.2 上传 JSON 文件

`POST /api/ingest/upload`

请求类型：`multipart/form-data`

字段：`file`

接口每次接收一个文件；前端多文件上传会把多个 JSON 按队列逐个调用该接口。

默认最大上传大小为 512MB，可用 `INGEST_MAX_UPLOAD_MB` 调整。

上传内容必须是合法 JSON，且符合 WeFlow 微信聊天导出结构（顶层包含 `weflow`、`session`，以及 `messages` / `messageList` / `msgList` / `chatRecords` / `records` 之一作为消息列表）。空文件、损坏 JSON 或非 WeFlow JSON 会返回 `400`，不会保留在上传目录。
后端会先写入临时上传文件，完成 JSON 校验、显示名和稳定聊天 scope 元数据写入后再替换成最终 `.json`；文件列表只会看到已校验完成的上传文件。

响应不包含服务器绝对路径：

```json
{
  "upload_id": "99ca3f...",
  "filename": "wechat_history.json",
  "size": 1048576,
  "message": "文件上传成功。"
}
```

### 6.3 删除可导入源文件

`POST /api/ingest/files/delete`

必须且只能提供 `upload_id` 或 `file_id` 中的一项。删除的是 `local/` 下的源 JSON 文件或上传目录中的源 JSON 及其 sidecar 元数据；已经解析入库的聊天消息、会话块、摘要和向量不会自动删除。

如果该文件当前有运行中或正在取消的导入任务，接口返回 `409`，避免删除正在被读取的源文件。

请求体示例：

```json
{
  "upload_id": "99ca3f..."
}
```

或：

```json
{
  "file_id": "data/wechat_history.json"
}
```

响应：

```json
{
  "file_id": "uploads/99ca3f....json",
  "message": "源 JSON 文件已删除；已入库的聊天记录不会自动删除。"
}
```

### 6.4 启动导入任务

`POST /api/ingest/start`

必须且只能提供 `upload_id`、`file_id`、`file_path` 中的一项；目标字段会去掉首尾空白，纯空白会按未提供处理，长度上限为 2048 字符，同时提供多个目标会返回 `400`，避免误导入错误文件。推荐使用 `upload_id` 或 `file_id`。`file_id` 必须来自 `/api/ingest/files` 返回的单个 `.json` 文件；目录递归导入仅保留给兼容旧调用的 `file_path`。

可选字段 `mode` 用于选择导入/重建方式，默认 `incremental`：

- `incremental`：增量导入，先检查目标 JSON，只有文件变化或解析规则过期时才重新解析，并会补齐缺失索引
- `full`：全流程导入，会重新解析目标 JSON 并核对消息入库；发现新增/更新或缺失索引时会继续补齐必要的 FTS、分块、摘要、向量流程
- `rebuild`：重新解析目标 JSON，并强制重建其关联范围的会话分块、摘要和向量；FTS 优先刷新目标 JSON 关联消息，若已有消息内容被修正则全量刷新以清理旧 token
- `fts`：跳过 JSON 解析，仅基于已有数据库强制重建目标 JSON 关联消息的 FTS
- `chunks`：跳过 JSON 解析，仅基于已有数据库强制重建目标 JSON 关联会话的会话分块
- `summary`：跳过 JSON 解析，仅基于已有数据库强制重新生成目标 JSON 关联会话块的摘要
- `embeddings` / `vector`：跳过 JSON 解析，仅基于已有数据库强制重建目标 JSON 关联会话块的向量索引

单项构建模式适用于文件已经完成导入、文件未变化且后端能定位该 JSON 来源映射后的索引修复；首次导入、需重新导入或“索引状态未知”的文件应使用 `incremental`、`full` 或 `rebuild` 先补齐来源映射。如果对未导入、需重新导入或缺少可定位来源映射的文件请求单项构建，接口会返回 `400`。目录目标的单项构建至少需要目录内一个 JSON 已有可定位的入库消息范围；`summary`、`embeddings` / `vector` 还要求目标范围已有会话分块。

请求体示例：

```json
{
  "upload_id": "99ca3f...",
  "mode": "full"
}
```

或：

```json
{
  "file_id": "data/wechat_history.json",
  "mode": "rebuild"
}
```

兼容旧调用：

`POST /api/ingest/start?file_path=data/wechat_history.json`

旧 `file_path` 允许指向项目 `local/` 目录内的 `.json` 文件或目录；目录会交给 `core.ingest` 递归扫描 JSON，但空目录或不含 `.json` 的目录会在创建任务前返回 `400`。目录目标的单项构建模式用于修复已有数据库索引，不要求某个单文件处于已同步状态，但会拒绝没有任何已入库消息/会话块范围的目录，避免静默完成 0 条修复。

响应：

```json
{
  "task_id": "a1b2c3...",
  "mode": "full",
  "message": "导入任务已启动。"
}
```

同一时间只允许一个导入任务运行；重复启动会返回 `409`。

### 6.5 获取任务状态

`GET /api/ingest/status/{task_id}`

响应：

```json
{
  "task_id": "a1b2c3...",
  "status": "running",
  "logs": "正在处理...\n",
  "created_at": "2026-06-17T08:00:00Z",
  "updated_at": "2026-06-17T08:01:00Z",
  "file_id": "uploads/99ca3f....json",
  "mode": "full",
  "error": null,
  "can_cancel": true,
  "progress": 45,
  "stage": "chunking",
  "message": "会话分块完成：2789 个块",
  "eta": 120,
  "log_tail": "最近日志..."
}
```

`status` 可能值：

| 值 | 说明 |
|---|---|
| `running` | 正在导入 |
| `cancel_requested` | 已请求取消，等待子进程退出 |
| `cancelled` | 已取消 |
| `completed` | 已完成 |
| `error` | 导入失败 |

### 6.6 列出任务

`GET /api/ingest/tasks?limit=50&offset=0`

响应不包含完整日志，用于页面刷新后恢复任务列表。运行中或取消中的任务会优先排在最前面，其余任务按更新时间/创建时间倒序排列。

```json
{
  "total_count": 1,
  "returned": 1,
  "offset": 0,
  "items": [
    {
      "task_id": "a1b2c3...",
      "status": "running",
      "logs": "",
      "created_at": "2026-06-17T08:00:00Z",
      "updated_at": "2026-06-17T08:01:00Z",
      "file_id": "uploads/99ca3f....json",
      "mode": "full",
      "error": null,
      "can_cancel": true,
      "progress": 45,
      "stage": "chunking",
      "message": "会话分块完成：2789 个块",
      "eta": 120,
      "log_tail": ""
    }
  ]
}
```

任务列表不会返回完整 `logs`，但会返回轻量进度字段，供页面刷新或 WebSocket 暂未连接时继续显示进度条。

### 6.7 取消导入任务

`POST /api/ingest/tasks/{task_id}/cancel`

如果任务仍在运行，后端会终止导入子进程。

响应为任务状态对象。

### 6.8 WebSocket 实时导入进度

`WS /api/ws/ingest/{task_id}`

用于导入大文件时实时接收任务状态，避免前端频繁轮询 `GET /api/ingest/status/{task_id}`。

连接成功后，服务端会立即推送一次当前状态，之后约每秒推送一次。任务进入 `completed`、`error` 或 `cancelled` 后，服务端会发送最终状态并关闭连接。

消息格式：

```json
{
  "task_id": "a1b2c3...",
  "status": "running",
  "progress": 45,
  "stage": "chunking",
  "message": "会话分块完成：2789 个块",
  "eta": 120,
  "updated_at": "2026-06-17T08:01:00Z",
  "file_id": "uploads/99ca3f....json",
  "mode": "full",
  "error": null,
  "can_cancel": true,
  "log_tail": "会话分块完成：重建..."
}
```

字段说明：

| 字段 | 说明 |
|---|---|
| `status` | 与任务状态接口一致：`running`、`cancel_requested`、`cancelled`、`completed`、`error` |
| `progress` | 0-100 的进度；后端子进程优先发送结构化进度事件，缺失时再根据日志关键词保守推断 |
| `stage` | 当前阶段，例如 `starting`、`parsing`、`indexing`、`chunking`、`summary`、`embedding`、`completed` |
| `message` | 当前阶段的可读说明，例如正在解析第几个 JSON、摘要或 embedding 完成数量 |
| `eta` | 估算剩余秒数；无法估算时为 `null` |
| `mode` | 当前任务使用的导入模式，例如 `full`、`rebuild`、`fts`、`summary`、`embeddings` |
| `log_tail` | 最近一段任务日志，用于前端展示实时细节 |

如果任务不存在，服务端会发送错误消息并关闭连接：

```json
{
  "task_id": "missing-task",
  "status": "error",
  "progress": 0,
  "stage": "missing",
  "eta": null,
  "error": "导入任务不存在。"
}
```

## 7. 输入建议

建议接口只返回联系人和会话名，不直接检索聊天内容。聊天记录检索仍通过 Agent 对话完成。

`query` 会压缩空白并限制为前 120 个字符；`limit` 范围为 1-50。

### 7.1 HTTP 建议

`GET /api/suggestions?query=张&limit=10`

响应：

```json
{
  "query": "张",
  "items": [
    {
      "type": "sender",
      "value": "张三",
      "count": 1024,
      "is_self": null
    },
    {
      "type": "thread",
      "value": "家庭群",
      "count": 12000,
      "is_self": null
    }
  ]
}
```

### 7.2 WebSocket 建议

`WS /api/ws/suggestions`

客户端可发送纯文本：

```text
张
```

也可发送 JSON：

```json
{
  "query": "张",
  "limit": 10
}
```

服务端返回与 `GET /api/suggestions` 相同的 JSON 结构。

## 8. 错误日志

后端将关键事件和异常写入 `runtime/backend.log.jsonl`。日志接口用于前端展示最近错误，帮助用户排查配置、导入、模型调用等问题。写入日志时会尽量避免记录密钥等敏感字段；`/api/logs` 返回给前端前还会再次脱敏消息、详情和 traceback。读取近期日志时会从当前日志文件尾部倒序扫描；如果当前文件不足 `limit` 且存在 `.1` 轮转日志，会继续从轮转日志尾部补足。日志文件默认超过 10MB 时轮转为 `.1` 文件，可用 `BACKEND_LOG_MAX_MB` 调整。

### 8.1 获取最近日志

`GET /api/logs?level=error&limit=100`

查询参数：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `level` | `error` | 最低日志级别，可选 `debug`、`info`、`warning`、`error` |
| `limit` | `100` | 返回条数，范围 `1-1000` |

响应：

```json
[
  {
    "timestamp": "2026-06-17T08:01:00.123Z",
    "level": "error",
    "logger": "wechat_backend",
    "message": "Unhandled exception",
    "module": "errors",
    "function": "unhandled_exception_handler",
    "line": 103,
    "details": {
      "path": "/api/chat",
      "status_code": 500
    },
    "traceback": "Traceback (most recent call last):\n..."
  }
]
```

级别过滤是“最低等级”语义：

| `level` | 返回内容 |
|---|---|
| `debug` | debug、info、warning、error |
| `info` | info、warning、error |
| `warning` | warning、error |
| `error` | error |
