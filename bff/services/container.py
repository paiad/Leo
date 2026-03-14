from __future__ import annotations

from pathlib import Path

from bff.repositories.model_store import create_model_store
from bff.repositories.store import store
from bff.services.chat.chat_service import ChatService
from bff.services.memory.memory_sync_service import MemorySyncService
from bff.services.models.model_service import ModelService
from bff.services.rag.rag_service import RagRuntimeService
from bff.services.runtime.agent_runtime import ManusRuntime
from bff.services.tooling.tooling_service import ToolingService

tooling_service = ToolingService(store=store)
model_service = ModelService(store=create_model_store())
memory_sync_service = MemorySyncService(store=store)
rag_service = RagRuntimeService(root_path=Path(__file__).resolve().parents[2])
chat_service = ChatService(
    store=store,
    runtime=ManusRuntime(store=store),
    model_service=model_service,
    memory_sync=memory_sync_service,
)
