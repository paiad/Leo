# PostgreSQL Storage Design (Leo / OpenManus)

## 1. 目标

- 支持 BFF 与 RAG 的生产级持久化
- 保证结构可扩展、可迁移、可审计
- 避免单机 SQLite 在并发与运维上的瓶颈

## 2. 设计原则

- 命名统一：表名 `snake_case`，主键 `id`，时间字段 `created_at/updated_at`
- 约束优先：`NOT NULL`、`UNIQUE`、`FOREIGN KEY` 明确化
- 查询导向索引：先覆盖读路径，再考虑写放大
- 兼容演进：先保证向后兼容字段，再逐步清理历史字段

## 3. 核心表（当前已落地）

### 3.1 `workspace_models`

用途：BFF 可用模型列表与运行配置（base_url/api_key 等）

关键字段：
- `id` `TEXT` PK
- `name` `TEXT NOT NULL`
- `provider` `TEXT NOT NULL`
- `base_url` `TEXT NOT NULL`
- `api_key` `TEXT NOT NULL DEFAULT ''`
- `enabled` `BOOLEAN NOT NULL DEFAULT TRUE`
- `created_at` `TEXT NOT NULL`
- `updated_at` `TEXT NOT NULL`

建议索引：
- `PRIMARY KEY (id)`
- 后续可加：`INDEX idx_workspace_models_enabled (enabled)`

### 3.2 `workspace_settings`

用途：BFF 键值配置（如 `active_model_id`）

关键字段：
- `key` `TEXT` PK
- `value` `TEXT`

### 3.3 `sources`

用途：RAG 文档源元数据，按文件路径去重

关键字段：
- `id` `BIGSERIAL` PK
- `path` `TEXT NOT NULL UNIQUE`
- `checksum` `TEXT NOT NULL`
- `version` `INTEGER NOT NULL DEFAULT 1`
- `updated_at` `TEXT NOT NULL`
- `last_indexed_at` `TEXT NOT NULL`

### 3.4 `chunks`

用途：RAG chunk 元数据与文本内容

关键字段：
- `chunk_id` `TEXT` PK
- `source_id` `BIGINT NOT NULL REFERENCES sources(id) ON DELETE CASCADE`
- `version` `INTEGER NOT NULL`
- `chunk_index` `INTEGER NOT NULL`
- `text` `TEXT NOT NULL`
- `token_count` `INTEGER NOT NULL`
- `metadata_json` `JSONB NOT NULL`

索引：
- `idx_chunks_source (source_id)`
- `idx_chunks_source_version (source_id, version)`

## 4. 会话与消息持久化（已落地）

已新增：
- `chat_sessions`
- `chat_messages`
- `mcp_servers`

当前行为：
- 当设置 `BFF_DATABASE_URL` 时，会话与消息默认持久化到 PostgreSQL
- 未设置时回退到原有 JSON 文件快照

后续可扩展：
- `chat_events`（审计/重放）
- `tenant_id`（多租户隔离）

## 4.1 长上下文分层存储（新增）

在原有 `chat_sessions/chat_messages` 基础上，新增三张表用于“压缩 + 回灌 + 审计”：

1) `chat_session_summaries`
- 用途：滚动摘要（每 N 轮生成 1 条），支持多级压缩（`summary_level`）
- 关键字段：
  - `session_id`
  - `summary_level`（1=基础摘要，2=摘要的摘要）
  - `summary_text` / `summary_json`
  - `message_count` / `approx_tokens`
  - `superseded_by_id` / `status`

2) `chat_memory_facts`
- 用途：稳定事实记忆（偏好、约束、决策、术语）
- 关键字段：
  - `session_id`（可空，空表示全局事实）
  - `fact_type` / `fact_key` / `fact_value` / `fact_json`
  - `confidence` / `priority`
  - `effective_from` / `effective_to` / `status`
- 约束：
  - 活跃事实唯一索引：`(scope, fact_key, status=active)`

3) `chat_context_injections`
- 用途：记录每次提示词回灌使用了哪些摘要与事实，便于审计 token 消耗
- 关键字段：
  - `summary_ids` / `fact_ids`
  - `prompt_budget_tokens` / `used_tokens`
  - `overflow_strategy`

### 推荐回灌策略

- 每轮仅回灌：
  - 最新 1 条 `chat_session_summaries`（必要时 +1 条上一级摘要）
  - `chat_memory_facts` 中 `status=active` 且 `priority` 最高的 Top-K
- 不直接回灌全量 `chat_messages`
- 定期将旧摘要“再摘要”，并将旧摘要标记为 `superseded`

## 5. 分区与归档策略

- `chat_messages` 按月分区（`created_at`）
- 冷数据归档到对象存储（JSONL/GZIP）
- 业务库保留近 3-6 个月热数据

## 6. 迁移规范

- 使用迁移工具（建议 Alembic）
- 每次变更包含：
  - `upgrade` + `downgrade`
  - 索引变更说明
  - 回滚风险说明

## 7. 运维基线

- 必开备份：每日全量 + 每小时 WAL
- 监控指标：连接数、慢查询、锁等待、磁盘增长
- 参数建议：连接池由应用端控制（避免连接风暴）
