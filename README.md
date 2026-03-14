# Leo

Leo 是一个基于 **OpenManus** 演进的工程化版本，目标是在保留 OpenManus 智能体能力的基础上，补齐可接入前端、可运维、可扩展的后端运行层。

## 项目定位

- 基础能力来源：`app/` 下 OpenManus Agent、工具调用、MCP 集成能力
- 工程化增强：`bff/` 下 Leo BFF（Backend For Frontend）服务层
- 目标场景：为前端应用、飞书接入、MCP 工具编排提供稳定 API 与运行时管理

## 与 OpenManus 的关系

Leo 不是对 OpenManus 的替代，而是面向业务接入的增强层：

1. 继承 OpenManus 的智能体内核能力  
2. 新增 BFF 分层架构与统一 API  
3. 增加运行时策略（MCP 按需路由、响应收敛、会话管理）  
4. 增加工程化配置（`.env`、`config/*.toml`、`config/mcp.bff.json`）  
5. 增加飞书 Webhook/长连接集成能力

## 核心目录

```text
.
├─app/                # OpenManus 核心能力（Agent / Tool / Sandbox / Prompt）
├─bff/                # Leo 后端适配层（API / Service / Repository / Domain）
├─frontend/           # 前端项目
├─config/             # 配置模板与运行配置
├─tests/              # 自动化测试
└─README_zh.md        # 上游 OpenManus 中文说明
```

## 项目架构

Leo 采用分层架构，职责边界如下：

1. 接入层（API Layer）  
`bff/api/` 负责 HTTP 路由、请求参数校验、SSE 输出、第三方回调入口（如飞书）。

2. 服务层（Service Layer）  
`bff/services/` 负责会话管理、消息编排、运行时调度、MCP 路由、最终答复收敛。

3. 领域与存储层（Domain/Repository）  
`bff/domain/` 定义请求与会话模型，`bff/repositories/` 提供当前内存存储实现（可替换）。

4. 智能体执行层（Agent Runtime）  
`app/agent/` + `app/tool/` 提供 OpenManus 核心能力：工具调用、浏览器/文件/代码执行、MCP 客户端连接。

5. 配置与状态层（Config/State）  
`config/config.toml` 管模型与基础配置，`.env` 管运行时开关与密钥，`config/mcp.bff.json` 管 MCP 服务状态。

架构链路（详细）：

```mermaid
flowchart LR
    subgraph Client["调用入口层"]
        FE[Frontend]
        FSW[Feishu Webhook]
        FLC[Feishu Long Connection]
    end

    subgraph API["BFF API 层（bff/api）"]
        AR[Router<br/>chat / mcp / health / feishu]
    end

    subgraph Service["BFF 服务层（bff/services）"]
        CS[ChatService<br/>会话编排 / Prompt 构建 / 历史压缩]
        RT[ManusRuntime<br/>Agent 生命周期 / 进度事件]
        MR[RuntimeMcpRouter<br/>按需 MCP 路由与连接]
        RF[RuntimeFinalizer<br/>最终答复收敛]
        TS[ToolingService<br/>MCP 服务管理 / discover]
    end

    subgraph DomainRepo["领域与存储层"]
        DM[Domain Models]
        ST[InMemoryStore<br/>Session / Message / MCP State]
    end

    subgraph Core["OpenManus Core（app）"]
        AG[Agent<br/>Manus / Flow / ToolCall]
        TOOLS[ToolCollection<br/>python / editor / browser / bash / terminate]
        MCPCLI[MCP Clients]
        SBX[Sandbox]
    end

    subgraph External["外部依赖"]
        LLM[LLM Provider]
        MCPS[MCP Servers<br/>stdio / sse]
        PW[Playwright / Browser]
        FSAPI[Feishu Open API]
    end

    subgraph ConfigState["配置与状态"]
        ENV[.env<br/>运行时开关 / 密钥]
        TOML[config/config.toml<br/>模型与系统配置]
        MCPJSON[config/mcp.bff.json<br/>MCP 持久化状态]
    end

    FE --> AR
    FSW --> AR
    FLC --> CS
    AR --> CS
    AR --> TS

    CS --> DM
    CS --> ST
    CS --> RT
    RT --> MR
    RT --> AG
    MR --> MCPCLI
    AG --> TOOLS
    TOOLS --> SBX
    TOOLS --> PW
    MCPCLI --> MCPS
    AG --> LLM
    RT --> RF
    RF --> CS
    CS --> AR
    AR --> FE
    CS --> FSAPI

    TOML -.读取.-> CS
    TOML -.读取.-> RT
    ENV -.读取.-> CS
    ENV -.读取.-> RT
    ENV -.读取.-> MR
    ENV -.读取.-> TS
    MCPJSON -.加载/写回.-> TS
    TS -.同步.-> ST
```

## 系统如何运作（消息回复流程）

以 `POST /api/v1/chat/completions` 为例，消息回复主流程如下：

1. 接收请求  
API 层接收用户消息，定位或创建 `session`，写入本轮 user message。

2. 构建运行时提示词  
`ChatService` 合并当前输入、工作区提示、历史上下文（按 token 预算裁剪），并应用输出策略。

3. 初始化/复用 Agent  
`ManusRuntime` 根据环境变量决定复用或新建 agent，设置最大步骤数并绑定进度回调。

4. MCP 按需连接  
`RuntimeMcpRouter` 基于当前请求语义选择需要连接的 MCP server，避免全量连接造成开销和不稳定。

5. 执行推理与工具调用  
Agent 进入 `PLAN -> ACT -> VERIFY -> FINALIZE` 阶段，按需调用本地工具或 MCP 工具完成任务。

6. 收敛最终答复  
`RuntimeFinalizer` 从消息链中选择最终 assistant 内容，做最终规范化后返回给调用方。

7. 持久化与输出  
会话消息保存到 store；若是流式请求，按 SSE 分片输出；若是飞书消息则通过飞书接口回发。

消息回复时序图（详细）：

```mermaid
sequenceDiagram
    autonumber
    participant U as User/Frontend
    participant API as bff/api/chat
    participant CS as ChatService
    participant ST as Store
    participant RT as ManusRuntime
    participant MR as RuntimeMcpRouter
    participant AG as Manus Agent
    participant LLM as LLM Provider
    participant MCP as MCP Server(s)
    participant RF as RuntimeFinalizer

    U->>API: POST /api/v1/chat/completions
    API->>CS: send_message(request)
    CS->>ST: get_or_create_session(sessionId)
    CS->>ST: append user message
    CS->>CS: build prompt (workspace + history + output policy)
    CS->>RT: ask(prompt, max_steps, callback)

    RT->>RT: create/reuse agent (BFF_RUNTIME_REUSE_AGENT)
    RT->>MR: connect_enabled_mcp_servers(prompt)
    MR->>MCP: connect stdio/sse (按需)
    MR-->>RT: connected servers + catalog context

    loop Agent Step Loop (<= max_steps)
        RT->>AG: run step
        AG->>LLM: completion/tool-choice
        alt needs MCP tool
            AG->>MCP: invoke remote tool
            MCP-->>AG: tool result
        else needs builtin tool
            AG-->>AG: run builtin tool (python/editor/browser/bash)
        end
        AG-->>RT: step events / messages
    end

    RT->>RF: finalize_response(messages, run_result)
    RF-->>RT: final assistant text
    RT-->>CS: final text
    CS->>ST: append assistant message

    alt stream=true
        CS-->>API: SSE chunks
        API-->>U: event stream
    else normal response
        CS-->>API: JSON payload
        API-->>U: { success, data, error }
    end
```

飞书消息处理时序图（Webhook + 长连接）：

```mermaid
sequenceDiagram
    autonumber
    participant FG as Feishu Group/User
    participant FE as Feishu Platform
    participant API as bff/api/integration/feishu.py
    participant LC as FeishuLongConnectionService
    participant CS as ChatService
    participant RT as ManusRuntime
    participant FS as Feishu Send API

    alt Webhook 模式
        FG->>FE: 发送消息
        FE->>API: POST /api/v1/feishu/events
        API->>API: token 校验 / 去重(message_id)
        API->>CS: send_message(source=lark)
    else Long Connection 模式
        FG->>FE: 发送消息
        FE-->>LC: WebSocket 事件推送
        LC->>LC: 解析 payload / 去重(message_id)
        LC->>CS: send_message(source=lark)
    end

    CS->>RT: ask(prompt)
    RT-->>CS: assistant text
    CS-->>API: reply text (Webhook)
    CS-->>LC: reply text (Long Connection)

    API->>FS: 调用 Feishu 发消息接口
    LC->>FS: 调用 Feishu 发消息接口
    FS-->>FG: 回发机器人回复
```

Agent 阶段状态机（PLAN / ACT / VERIFY / FINALIZE）：

```mermaid
stateDiagram-v2
    [*] --> INIT: 收到 prompt / 参数
    INIT --> PLAN: 初始化 agent / 清理上轮状态

    state PLAN {
      [*] --> P1
      P1: 目标解析\n约束识别\n拆分执行策略
    }

    PLAN --> ACT: 形成可执行步骤

    state ACT {
      [*] --> A1
      A1: 选择工具\n调用 MCP 或内置工具\n写入中间结果
      A1 --> A1: 多步工具迭代\n(直到达到阶段目标或步数上限)
    }

    ACT --> VERIFY: 获得阶段性结果

    state VERIFY {
      [*] --> V1
      V1: 校验结果完整性\n检查失败/空结果\n判断是否需要补执行
    }

    VERIFY --> ACT: 校验不通过，继续执行
    VERIFY --> FINALIZE: 校验通过
    ACT --> FINALIZE: 达到 max_steps 或触发 terminate

    state FINALIZE {
      [*] --> F1
      F1: 选择最终 assistant 消息\n规范化输出\n生成最终答复
    }

    FINALIZE --> [*]: 返回结果并持久化消息
```

补充说明：

- 默认响应结构：`{ success, data, error }`
- 流式接口：`/api/v1/chat/completions?stream=true`
- 飞书入口：`POST /api/v1/feishu/events`（Webhook）或长连接服务

## 快速开始

### 1) 安装依赖

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS/Linux:
# source .venv/bin/activate

pip install -r requirements.txt
```

### 2) 准备配置

1. LLM 配置：复制并编辑 `config/config.toml`
2. 环境变量：编辑根目录 `.env`（飞书、BFF 运行参数等）
3. MCP 配置：按需使用 `config/mcp.bff.json`

### 3) 启动方式

- CLI（OpenManus 主入口）：
```bash
python main.py
```

- MCP 模式：
```bash
python run_mcp.py
```

- 多智能体 Flow：
```bash
python run_flow.py
```

- Leo BFF（推荐用于前端联调）：
```bash
python -m uvicorn bff.main:app --host 0.0.0.0 --port 8000
```

> Windows 下不建议使用 `--reload`，可能导致 Playwright/MCP-stdio 子进程行为异常。

## Leo BFF 能力概览

- 统一 Chat API（含流式 SSE）
- MCP Server 管理与工具发现
- 运行时路由（按请求内容选择 MCP，减少无效连接）
- 会话消息管理与历史压缩策略
- 飞书集成（Webhook + 长连接）
- 统一响应结构：`{ success, data, error }`

主要接口见：[`bff/README.md`](bff/README.md)

## 配置规范

### 配置分层

1. `config/config.toml`：模型、浏览器、sandbox、runflow 等主配置  
2. `.env`：运行时开关、飞书密钥、BFF 行为参数  
3. `config/mcp.bff.json`：MCP 服务状态与工具发现缓存

### 环境变量原则

- 布尔变量统一使用：`1/true/yes/on` 或 `0/false/no/off`
- 路径变量使用绝对路径（特别是 Windows）
- 密钥只放 `.env`，不要写入代码和提交记录

## 开发与测试

运行核心测试：

```bash
pytest tests/bff tests/sandbox -q
```

代码检查：

```bash
pre-commit run --all-files
```

## 版本说明

- 当前仓库为 OpenManus 的衍生工程化版本（Leo）
- 功能边界：保留上游能力，同时优先保证 BFF 可集成性与运行稳定性

## 致谢

- [OpenManus](https://github.com/FoundationAgents/OpenManus)
- [MetaGPT](https://github.com/geekan/MetaGPT)
- [browser-use](https://github.com/browser-use/browser-use)
- [anthropic-computer-use](https://github.com/anthropics/anthropic-quickstarts/tree/main/computer-use-demo)
