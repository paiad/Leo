from __future__ import annotations

from bff.repositories.store import store
from bff.services.agent_runtime import ManusRuntime
from bff.services.chat_service import ChatService
from bff.services.tooling_service import ToolingService

tooling_service = ToolingService(store=store)
chat_service = ChatService(store=store, runtime=ManusRuntime(store=store))
