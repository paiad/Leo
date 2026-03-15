# Memory Architecture (BFF)

## Goals

- Separate responsibilities: session persistence vs. long-term memory sync.
- Keep runtime behavior predictable and failure-safe.
- Make configuration explicit and centralized.

## Layers

1. Session Persistence Layer
- File: `bff/repositories/store.py`
- Responsibility: persist full chat sessions (`browser` / `lark`) to JSON snapshot.
- Data shape: `SessionRecord` + `MessageRecord`.
- Path source: `bff/services/memory/settings.py::chat_session_store_path()`

2. Conversation Runtime Layer
- File: `bff/services/chat/chat_service.py`
- Responsibility: append user/assistant messages, stream response, write chat record logs.
- Memory integration entrypoint: `_schedule_memory_sync(...)` (single place).

3. Long-Term Memory Layer (MCP)
- File: `bff/services/memory/memory_sync_service.py`
- Responsibility: best-effort async sync to `memory` MCP server.
- Input contract: `TurnMemoryPayload` (`bff/services/memory/types.py`).
- Failure policy: never block or fail chat response.

## Configuration

- `BFF_CHAT_MEMORY_STORE_PATH`:
  where session snapshot file is stored.
- `BFF_MEMORY_SYNC_ENABLED`:
  `1/true/yes/on` enables MCP sync.
- MCP `memory` server enable switch:
  `mcpServers.memory.enabled`（Postgres 模式在 DB；非 Postgres 模式在 `config/mcp.bff.json`）。

## Data Flow

1. User message appended to session.
2. Agent produces assistant response.
3. Session snapshot persisted.
4. Chat log record persisted.
5. Async `MemorySyncService.schedule_sync_turn(...)` triggered.
6. Memory MCP write attempted; failures only logged.

## Development Rules

- Do not write to memory MCP directly from controllers/routes.
- Route-level code should not parse memory env vars.
- Keep memory payload schema changes in `TurnMemoryPayload`.
- New memory backends should implement service-level adapter, not touch `ChatService` control flow.
