# WeChat RAG Agent - 后端 API 接口文档

本接口基于 FastAPI 构建，提供了从微信聊天记录流式问答、会话管理、系统设置到异步数据导入的全套能力。所有的 API 路由前缀默认位于 `http://localhost:8000`（或您指定的服务器地址）。

---

## 1. 核心对话与流式输出 (Chat)

### 1.1 发起对话 (SSE 推流)
- **端点**: `POST /api/chat`
- **说明**: 向大模型发起询问，并以 Server-Sent Events (SSE) 格式实时返回工具调用状态和打字机回复。
- **请求体 (JSON)**:
  ```json
  {
    "question": "搜索一下上个月我和张三讨论了什么关于旅游的事情",
    "session_id": "可选填，留空会自动生成新的 UUID 会话ID"
  }
  ```
- **响应格式 (text/event-stream)**:
  ```text
  event: tool_call
  data: {"name": "search_messages", "args": "{\"query\": \"旅游\", \"contact_name\": \"张三\", ...}"}

  event: tool_result
  data: {"name": "search_messages", "summary": "找到 23 条消息，返回 20 条"}

  event: text
  data: {"chunk": "上"}

  event: text
  data: {"chunk": "个"}

  event: done
  data: {"answer": "上个月你和张三...", "session_id": "b0f7e...-..."}
  ```
- **SSE 事件类型说明**:
  | 事件 | 说明 |
  |------|------|
  | `tool_call` | Agent 发起了一次工具调用（包含工具名和参数预览） |
  | `tool_result` | 工具调用完成，附带结果摘要（如"找到 23 条消息"） |
  | `text` | 模型输出的文本片段（打字机效果） |
  | `done` | 本轮对话完成，附带完整回答和 session_id |
  | `error` | Agent 执行出错 |

### 1.2 终止生成
- **端点**: `POST /api/chat/{session_id}/abort`
- **说明**: 强行中断指定会话当前正在进行的思考或输出。常用于前端的"停止生成"按钮。
- **响应 (JSON)**:
  ```json
  { "message": "已发送终止指令" }
  ```

---

## 2. 会话管理 (Session Management)

### 2.1 获取会话历史记录
- **端点**: `GET /api/chat/{session_id}/messages`
- **说明**: 拉取某个会话的所有历史对话记录，用于刷新页面时恢复聊天现场。
- **响应 (JSON)**:
  ```json
  [
    { "role": "user", "content": "你好" },
    { "role": "assistant", "content": "你好！有什么我可以帮你的吗？" }
  ]
  ```

### 2.2 列出所有活跃会话
- **端点**: `GET /api/chat/sessions`
- **说明**: 获取当前缓存在内存中的所有多轮对话会话列表。
- **响应 (JSON)**:
  ```json
  [
    {
      "session_id": "b0f7e...",
      "message_count": 4,
      "last_question": "上个月发生了什么？"
    }
  ]
  ```

### 2.3 清理指定会话
- **端点**: `DELETE /api/chat/{session_id}`
- **说明**: 从内存中删除指定的对话历史上下文。
- **响应 (JSON)**:
  ```json
  { "message": "会话 b0f7e... 已删除" }
  ```

---

## 3. 动态系统设置 (Settings)

### 3.1 获取当前设置
- **端点**: `GET /api/settings`
- **说明**: 读取当前系统提示词和上下文等参数。
- **响应 (JSON)**:
  ```json
  {
    "system_prompt": "你是微信聊天助手...",
    "max_rounds": 100,
    "max_history_messages": 40,
    "chat_model": "gpt-4o",
    "chat_timeout": 300.0,
    "chat_temperature": 0.0,
    "enabled_tools": ["search_messages", "semantic_search", "get_context", "browse_by_time", "get_stats"]
  }
  ```

### 3.2 修改系统设置
- **端点**: `POST /api/settings`
- **说明**: 动态修改上述参数，修改立即生效而无需重启后端。
- **请求体 (JSON)**:
  ```json
  {
    "system_prompt": "你现在是一个幽默的助手...",
    "chat_temperature": 0.8,
    "chat_timeout": 120.0,
    "enabled_tools": ["semantic_search"]
  }
  ```

---

## 4. 大盘与数据总览 (Stats)

### 4.1 获取数据库概况
- **端点**: `GET /api/stats`
- **说明**: 从 SQLite 获取当前索引的所有微信数据宏观统计，适合渲染数据仪表盘。
- **响应 (JSON)**:
  ```json
  {
    "total_messages": 123170,
    "indexed_session_chunks": 2789,
    "time_span": {
      "earliest": "2021-01-29 10:00:00",
      "latest": "2026-06-13 18:30:00"
    },
    "threads": [ ... ]
  }
  ```

---

## 5. 健康检查 (Health)

### 5.1 系统状态与能力检测
- **端点**: `GET /api/health`
- **说明**: 返回系统运行状态和能力标识，前端可据此决定启用/禁用特定功能（如未配置模型时显示引导页）。
- **响应 (JSON)**:
  ```json
  {
    "status": "ok",
    "chat_model_configured": true,
    "embedding_configured": true,
    "vector_search_available": true,
    "total_messages": 123170,
    "has_data": true
  }
  ```

---

## 6. 数据导入与解析 (Ingest)

*此模块利用后台线程处理耗时的 JSON 解析与向量索引建立任务。*

### 6.1 上传 JSON 备份文件
- **端点**: `POST /api/ingest/upload`
- **说明**: 上传由 WeFlow 或其它工具导出的 `.json` 格式微信聊天记录文件（`multipart/form-data`）。
- **响应 (JSON)**:
  ```json
  {
    "message": "文件上传成功",
    "file_path": "c:\\Desktop\\wechat_agent\\local\\wechat_history.json"
  }
  ```

### 6.2 触发后台导入
- **端点**: `POST /api/ingest/start`
- **说明**: 提供已上传到本地的文件路径，触发大模型摘要和 SQLite 向量化建库任务。此接口**不会阻塞**，立即返回任务 ID。
- **请求参数 (Query Param)**: `?file_path=c:\Desktop\wechat_agent\local\wechat_history.json`
- **响应 (JSON)**:
  ```json
  {
    "task_id": "99ca3f-...",
    "message": "后台导入任务已启动"
  }
  ```

### 6.3 轮询导入进度与日志
- **端点**: `GET /api/ingest/status/{task_id}`
- **说明**: 每隔几秒轮询此接口获取后台处理的进度以及控制台输出的日志。
- **响应 (JSON)**:
  ```json
  {
    "task_id": "99ca3f-...",
    "status": "running", 
    "logs": "正在处理第 1/50 批次...\n已完成 30%..."
  }
  ```
  *(注：status 可能的值为 `running`, `completed`, `error`)*
