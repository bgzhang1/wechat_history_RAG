# 微信聊天记录检索 Agent

把 WeFlow 导出的微信聊天 JSON 导入本地 SQLite，然后用自然语言检索、追溯上下文、统计聊天内容。

## 目录结构

```text
wechat-rag-agent/
  wechat_rag_agent/      # 主体 Python 代码，允许提交到远程仓库
  requirements.txt       # Python 依赖
  .env.example           # 环境变量模板，不包含真实密钥
  README.md
  CHANGELOG.md

  runtime/               # 本地数据库目录，已被 Git 忽略
  local/                 # 本地资料目录，已被 Git 忽略
    data/                # 微信导出的原始 JSON
    imports/             # 暂存的额外导出文件
    docs/                # 本地技术手册、草稿、私有文档
    bench/               # 本机 benchmark、日志、基准快照
    legacy-node/         # 旧 TypeScript/npm 资料，仅本地保留
```

远程仓库只应该保留主体代码和必要项目说明。`.env`、`runtime/`、`local/`、数据库、聊天 JSON、日志、依赖缓存都不会被提交。

## 快速开始

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt

copy .env.example .env
# 编辑 .env，填入 CHAT_*；EMBED_* 可选

python -m wechat_rag_agent.scripts.check
python -m wechat_rag_agent.ingest local/data
python -m wechat_rag_agent.cli
```

默认数据库路径是 `runtime/chat.db`。如果需要改到其他位置，可以在 `.env` 中设置 `CHAT_DB`。

## 常用命令

```bash
# 检查模型端点
python -m wechat_rag_agent.scripts.check

# 导入单个 JSON 或目录
python -m wechat_rag_agent.ingest local/data
python -m wechat_rag_agent.ingest path\to\chat.json --no-summary

# 强制重建部分索引
python -m wechat_rag_agent.ingest local/data --force-fts
python -m wechat_rag_agent.ingest local/data --force-chunks
python -m wechat_rag_agent.ingest local/data --force-summary
python -m wechat_rag_agent.ingest local/data --force-embeddings
python -m wechat_rag_agent.ingest local/data --force-rebuild

# 启动聊天检索
python -m wechat_rag_agent.cli
```

没有配置 `EMBED_*` 也可以运行，`semantic_search` 会自动退化为全文检索。配置好 embedding 后重新运行 ingest，即可补建向量索引。
