from __future__ import annotations

from bff.repositories.model_store import ModelSqliteStore
from bff.repositories.store import store
from bff.services.agent_runtime import ManusRuntime
from bff.services.chat_service import ChatService
from bff.services.model_service import ModelService
from bff.services.tooling_service import ToolingService

tooling_service = ToolingService(store=store)
model_service = ModelService(store=ModelSqliteStore())
chat_service = ChatService(store=store, runtime=ManusRuntime(store=store), model_service=model_service)
