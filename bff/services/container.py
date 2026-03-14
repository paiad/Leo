from __future__ import annotations

from bff.repositories.model_store import ModelSqliteStore
from bff.repositories.store import store
from bff.services.chat.chat_service import ChatService
from bff.services.memory.memory_sync_service import MemorySyncService
from bff.services.models.model_service import ModelService
from bff.services.runtime.agent_runtime import ManusRuntime
from bff.services.tooling.tooling_service import ToolingService

tooling_service = ToolingService(store=store)
model_service = ModelService(store=ModelSqliteStore())
memory_sync_service = MemorySyncService(store=store)
chat_service = ChatService(
    store=store,
    runtime=ManusRuntime(store=store),
    model_service=model_service,
    memory_sync=memory_sync_service,
)
