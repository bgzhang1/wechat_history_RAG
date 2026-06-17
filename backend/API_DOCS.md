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
    "message": "Session xxx does not exist.",
    "recoverable": true,
    "action": "Refresh the page or select another item.",
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
  "session_id": "可选；不传则自动创建"
}
```

响应类型：`text/event-stream`

事件示例：

```text
event: session
data: {"session_id":"b0f7e...","status":"running"}

event: tool_call
data: {"name":"search_messages","args":"{\"query\":\"旅游\"}"}

event: tool_result
data: {"name":"search_messages","summary":"found 23 messages, returned 20"}

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
[
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
```

### 2.2 获取会话消息

`GET /api/chat/{session_id}/messages`

响应：

```json
[
  {
    "role": "user",
    "content": "你好",
    "created_at": "2026-06-17T08:00:00Z"
  },
  {
    "role": "assistant",
    "content": "你好，有什么想查的聊天记录？",
    "created_at": "2026-06-17T08:00:02Z"
  }
]
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

### 2.5 删除单个会话

`DELETE /api/chat/{session_id}`

响应：

```json
{
  "message": "Session b0f7e... deleted."
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

响应：

```json
{
  "deleted": ["b0f7e..."],
  "missing": ["a1c2e..."]
}
```

## 3. 系统设置

### 3.1 获取设置

`GET /api/settings`

响应：

```json
{
  "system_prompt": "你是微信聊天记录检索助手...",
  "max_rounds": 100,
  "max_history_messages": 40,
  "chat_model": "gpt-4o",
  "chat_timeout": 300.0,
  "chat_temperature": 0.0,
  "enabled_tools": ["search_messages", "semantic_search", "get_context", "browse_by_time", "get_stats"]
}
```

### 3.2 更新设置

`POST /api/settings`

所有字段均可选，只传需要修改的字段。

```json
{
  "chat_temperature": 0.5,
  "chat_timeout": 120,
  "enabled_tools": ["search_messages", "semantic_search", "get_context"]
}
```

响应为更新后的完整设置。

### 3.3 重置设置

`POST /api/settings/reset`

恢复服务启动时的默认设置，并清空运行时模型覆盖配置。

## 4. 数据统计

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
  "embedding_configured": true,
  "embedding_model": "BAAI/bge-m3",
  "embedding_missing": [],
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
      "detail": "123170 messages indexed.",
      "recoverable": false,
      "action": null
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
      "detail": "123170 messages indexed.",
      "recoverable": false,
      "action": null
    },
    {
      "component": "vector_index",
      "status": "warning",
      "detail": "sqlite-vec available; 12 session chunks missing vectors.",
      "recoverable": true,
      "action": "Re-run ingest after embedding configuration is fixed."
    }
  ],
  "db_stats": {},
  "chat_status": {},
  "embed_status": {},
  "vector_search_available": true
}
```

## 6. 数据导入

导入任务通过后台子进程执行，支持取消。任务列表保存在当前后端进程内，可支持页面刷新后的任务状态恢复；如果后端进程重启，历史任务列表会清空。

### 6.1 列出可导入文件

`GET /api/ingest/files?limit=100&offset=0`

扫描项目 `local/` 目录下的 `.json` 文件，不返回服务器绝对路径。

响应：

```json
{
  "total_count": 2,
  "returned": 2,
  "offset": 0,
  "items": [
    {
      "file_id": "uploads/99ca3f....json",
      "filename": "99ca3f....json",
      "size": 1048576,
      "modified_at": "2026-06-17T08:00:00Z",
      "source": "upload",
      "upload_id": "99ca3f..."
    }
  ]
}
```

### 6.2 上传 JSON 文件

`POST /api/ingest/upload`

请求类型：`multipart/form-data`

字段：`file`

响应不包含服务器绝对路径：

```json
{
  "upload_id": "99ca3f...",
  "filename": "wechat_history.json",
  "size": 1048576,
  "message": "File uploaded successfully."
}
```

### 6.3 启动导入任务

`POST /api/ingest/start`

推荐使用 `upload_id` 或 `file_id`。

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

兼容旧调用：

`POST /api/ingest/start?file_path=data/wechat_history.json`

旧 `file_path` 只允许指向项目 `local/` 目录内的 `.json` 文件。

响应：

```json
{
  "task_id": "a1b2c3...",
  "message": "Background ingest task started."
}
```

同一时间只允许一个导入任务运行；重复启动会返回 `409`。

### 6.4 获取任务状态

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
  "error": null,
  "can_cancel": true
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

### 6.5 列出任务

`GET /api/ingest/tasks?limit=50&offset=0`

响应不包含完整日志，用于页面刷新后恢复任务列表。

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
      "error": null,
      "can_cancel": true
    }
  ]
}
```

### 6.6 取消导入任务

`POST /api/ingest/tasks/{task_id}/cancel`

如果任务仍在运行，后端会终止导入子进程。

响应为任务状态对象。

### 6.7 WebSocket 实时导入进度

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
  "eta": 120,
  "updated_at": "2026-06-17T08:01:00Z",
  "file_id": "uploads/99ca3f....json",
  "error": null,
  "can_cancel": true,
  "log_tail": "会话分块完成：重建..."
}
```

字段说明：

| 字段 | 说明 |
|---|---|
| `status` | 与任务状态接口一致：`running`、`cancel_requested`、`cancelled`、`completed`、`error` |
| `progress` | 0-100 的估算进度；当前根据导入状态和日志关键词保守推断 |
| `stage` | 当前阶段，例如 `starting`、`parsing`、`indexing`、`chunking`、`summary`、`embedding`、`completed` |
| `eta` | 估算剩余秒数；无法估算时为 `null` |
| `log_tail` | 最近一段任务日志，用于前端展示实时细节 |

如果任务不存在，服务端会发送错误消息并关闭连接：

```json
{
  "task_id": "missing-task",
  "status": "error",
  "progress": 0,
  "stage": "missing",
  "eta": null,
  "error": "Task not found."
}
```

## 7. 输入建议

建议接口只返回联系人和会话名，不直接检索聊天内容。聊天记录检索仍通过 Agent 对话完成。

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

后端将关键事件和异常写入 `runtime/backend.log.jsonl`。日志接口用于前端展示最近错误，帮助用户排查配置、导入、模型调用等问题。

### 8.1 获取最近日志

`GET /api/logs?level=error&limit=100`

查询参数：

| 参数 | 默认值 | 说明 |
|---|---|---|
| `level` | `error` | 最低日志级别，可选 `debug`、`info`、`error` |
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
| `error` | error |
