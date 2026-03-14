# Leo BFF

兼容 `frontend` 的后端适配层，采用分层结构以便后续替换实现（数据库、A2A、飞书适配）。

## 目录结构

- `bff/app.py`：应用创建、全局中间件、异常处理
- `bff/api/`：HTTP Router（chat/mcp/health）
- `bff/services/`：业务服务与运行时适配
- `bff/repositories/`：存储访问（当前为内存实现）
- `bff/domain/`：请求与领域模型
- `bff/core/`：统一响应工具

## 运行

```bash
cd E:\Github\OpenManus
.\.venv\Scripts\python -m uvicorn bff.main:app --host 0.0.0.0 --port 8000
```

服务启动时会自动读取项目根目录 `.env`（若存在）并注入进程环境变量。

> Windows 注意：不要使用 `--reload`。  
> `--reload` 会触发 `SelectorEventLoop`，导致 Playwright / MCP-stdio 的子进程能力报 `NotImplementedError`。

## 接口

- `GET /healthz`
- `GET /api/v1/chat/models`
- `GET /api/v1/chat/system-prompt`
- `GET/POST /api/v1/chat/sessions`
- `GET/DELETE /api/v1/chat/sessions/{session_id}/messages`
- `DELETE /api/v1/chat/sessions/{session_id}/messages/{message_id}`
- `POST /api/v1/chat/completions`
- `POST /api/v1/chat/completions?stream=true`（SSE）
- `POST /api/v1/feishu/events`（飞书事件回调）
- `GET /api/v1/mcp/catalog`
- `GET/POST/PUT/DELETE /api/v1/mcp/servers`
- `POST /api/v1/mcp/servers/{server_id}/discover`
- `GET /api/v1/mcp/servers/{server_id}/tools`

兼容别名（建议逐步迁移）：
- `POST /api/v1/chat/messages`
- `POST /api/v1/chat/stream`
- `POST /api/v1/chat/completions/stream`
- `GET /api/v1/tools`

## 当前实现说明

- Chat 调用通过 `ManusRuntime` 适配 `app.agent.manus.Manus`。
- Session 为内存存储。
- MCP Server 状态持久化在 `config/mcp.bff.json`。
- `discover` 会真实连接 MCP（`stdio/sse`）并调用 `tools/list`。
- 默认注入 `leo-local` 模板（`python -m app.mcp.server`，默认禁用）。
- 已统一错误返回结构：`{ success, data, error }`。

## Playwright 登录态持久化

如果使用 `@playwright/mcp`，默认会开临时浏览器上下文。可通过环境变量改为持久化登录态：

```bash
BFF_PLAYWRIGHT_USER_DATA_DIR=E:\Github\OpenManus\.playwright-user-data
# 可选：复用 storageState 文件
# BFF_PLAYWRIGHT_STORAGE_STATE=E:\Github\OpenManus\.playwright-state.json
```

说明：

- 设置 `BFF_PLAYWRIGHT_USER_DATA_DIR` 后，运行时会自动注入 `--user-data-dir`。
- 若原配置里有 `--isolated`，会自动移除，避免与持久化目录冲突。
- `BFF_PLAYWRIGHT_STORAGE_STATE` 会覆盖已有 `--storage-state` 参数。
- 若希望对话结束后浏览器不关闭，可设置 `BFF_RUNTIME_REUSE_AGENT=true`，复用同一个运行时 agent（同进程内生效）。

## 飞书接入（Webhook + 长连接）

默认支持两种模式：

- Webhook：`POST /api/v1/feishu/events`
- 长连接（推荐开发期）：不需要公网回调地址

### 方式一：长连接（推荐）

需要配置的环境变量：

```bash
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_USE_LONG_CONNECTION=true
FEISHU_REPLY_ONLY_WHEN_MENTIONED=true
```

说明：

- 启动 BFF 后会自动建立飞书 SDK 长连接。
- 不需要配置飞书请求地址，不需要内网穿透。
- 仅支持企业自建应用。

### 方式二：Webhook

将飞书「事件与回调」中的请求地址配置为：

```text
POST /api/v1/feishu/events
```

需要配置的环境变量：

```bash
FEISHU_APP_ID=cli_xxx
FEISHU_APP_SECRET=xxx
FEISHU_VERIFICATION_TOKEN=xxx
# 可选：群聊中仅被 @ 时回复，默认 true
FEISHU_REPLY_ONLY_WHEN_MENTIONED=true
```

行为说明：

- 支持 `url_verification`（返回 `challenge`）。
- 监听 `im.message.receive_v1` 并调用 OpenManus ChatService。
- 使用 `message_id` 做去重，避免重复推送导致重复回复。
- 默认只处理文本消息；非文本消息会提示“目前仅支持文本消息”。
- 当前回调适配器未实现 Encrypt Key 解密流程，配置飞书事件回调时请先关闭 Encrypt Key（明文回调模式）。
