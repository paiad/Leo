import pytest

from bff.domain.models import MessageRecord, now_iso
from bff.repositories.store import InMemoryStore
from bff.services.chat.chat_service import ChatService


class _FakeRuntime:
    async def ask(self, prompt: str, *, session_id: str | None = None, progress_callback=None) -> str:
        return f"assistant:{prompt}"


class _FakeModelService:
    def get_runtime_model_name(self) -> str:
        return "test-model"

    def chat_model_config(self) -> dict:
        return {
            "provider": "openai-compatible",
            "defaultBaseUrl": "",
            "defaultModel": "test-model",
            "availableModels": ["test-model"],
            "activeModelId": "test",
            "activeApiKey": "",
        }


class _FakeContextMemory:
    def __init__(self) -> None:
        self.purged_session_ids: list[str] = []

    def purge_session_memory(self, *, session_id: str) -> None:
        self.purged_session_ids.append(session_id)


class _FakeMemorySync:
    def __init__(self) -> None:
        self.forget_calls: list[tuple[str, str]] = []

    async def forget_session(self, *, source: str, session_id: str) -> None:
        self.forget_calls.append((source, session_id))


@pytest.mark.asyncio
async def test_clear_session_messages_purges_all_memory_layers():
    fake_memory_sync = _FakeMemorySync()
    store = InMemoryStore(enable_persistence=False)
    service = ChatService(
        store=store,
        runtime=_FakeRuntime(),
        model_service=_FakeModelService(),
        memory_sync=fake_memory_sync,
    )
    fake_context = _FakeContextMemory()
    service._context_memory = fake_context  # type: ignore[assignment]

    created = service.create_session(source="browser")
    session_id = str(created["id"])
    session = service._store.sessions[session_id]
    session.messages.append(
        MessageRecord(
            id="msg-1",
            role="user",
            content="hello",
            createdAt=now_iso(),
        )
    )
    store.record_mcp_routing_event(
        {
            "event_type": "outcome",
            "session_id": session_id,
            "prompt_hash": "p",
            "intent": "web_search",
            "success": True,
        }
    )

    deleted = await service.clear_session_messages(session_id)

    assert deleted == 1
    assert session.messages == []
    assert not any(
        str((event or {}).get("session_id") or "") == session_id
        for event in store.mcp_routing_events
    )
    assert fake_context.purged_session_ids == [session_id]
    assert fake_memory_sync.forget_calls == [("browser", session_id)]


@pytest.mark.asyncio
async def test_clear_session_messages_returns_none_for_missing_session():
    fake_memory_sync = _FakeMemorySync()
    service = ChatService(
        store=InMemoryStore(enable_persistence=False),
        runtime=_FakeRuntime(),
        model_service=_FakeModelService(),
        memory_sync=fake_memory_sync,
    )
    fake_context = _FakeContextMemory()
    service._context_memory = fake_context  # type: ignore[assignment]

    deleted = await service.clear_session_messages("missing-session")

    assert deleted is None
    assert fake_context.purged_session_ids == []
    assert fake_memory_sync.forget_calls == []
