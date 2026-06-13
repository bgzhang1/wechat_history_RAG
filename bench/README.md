# 摄入性能基准测试工具

用于复现 ingest 优化前后的性能对比。`baseline_pkg/` 是优化前代码的冻结快照。

## 使用

```bash
# 1. 生成测试数据（从 data/ 下的真实导出文件派生）
python bench/prep_data.py

# 2. 启动 mock LLM（固定延迟 200ms，统计调用次数；GET /stats 查看）
python bench/mock_llm.py 8765

# 3. 计时运行（--pkg . 为优化版；--pkg bench/baseline_pkg 为基线）
python bench/run_case.py --name s1_opt --pkg . --db bench/dbs/s1.db \
    --env SUMMARY_MODEL= --env EMBED_MODEL= -- <绝对路径>/data

# 带 mock LLM 的完整管线
python bench/run_case.py --name s2_opt --pkg . --db bench/dbs/s2.db \
    --mock-port 8765 -- <绝对路径>/bench/data/p95

# 4. 对比两个库的内容摘要（与 session_id 无关，可校验产出等价性）
python bench/verify_db.py bench/dbs/a.db bench/dbs/b.db
```

注意：本机若开启系统代理（注册表级），httpx 会把 127.0.0.1 的请求发给代理导致 502；
run_case.py 的 mock 模式已自动注入 NO_PROXY 绕过。

## 2026-06-13 实测结果（123,170 条消息 / 2,789 会话块，mock LLM 200ms）

| 场景 | 基线 | 优化后 | 提升 |
|---|---|---|---|
| S1 全量冷导入（无 LLM 阶段） | 485.1s | 6.4s | **76x** |
| S2 重新导出后增量导入（+5,893 条） | 451.4s | 9.5s | **47x** |
| S3a 批量首次导入两个线程（50k 条） | 66.9s | 16.7s | **4.0x** |
| S3b 向库中追加新线程（+8,244 条） | 81.7s | 6.7s | **12x** |

S2 增量导入 LLM 调用数：基线 2,789 次摘要 + 88 批 embedding（全量重做）；
优化后 128 次摘要 + 4 批 embedding（哈希复用未变化块）。
