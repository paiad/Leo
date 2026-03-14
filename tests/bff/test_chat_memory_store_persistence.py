import pytest

from bff.domain.models import SessionRecord, now_iso, new_id
from bff.repositories.store import InMemoryStore
from bff.api.integration.feishu import _SessionMap


def test_store_loads_sessions_from_persistent_file(tmp_path):
    store_path = tmp_path / "chat-memory-store.json"
    session = SessionRecord(
        id=new_id(),
        title="Browser Session",
        createdAt=now_iso(),
        updatedAt=now_iso(),
        messages=[],
    )

    first = InMemoryStore(persistence_path=str(store_path))
    first.sessions[session.id] = session
    first.persist_sessions()

    second = InMemoryStore(persistence_path=str(store_path))
    assert session.id in second.sessions
    assert second.sessions[session.id].title == "Browser Session"


@pytest.mark.asyncio
async def test_feishu_session_map_restores_existing_session(monkeypatch):
    chat_id = "oc_xxx_123"
    target_title = f"Feishu-{chat_id}"
    existing_session = SessionRecord(
        id=new_id(),
        title=target_title,
        createdAt=now_iso(),
        updatedAt=now_iso(),
        messages=[],
    )
    store = InMemoryStore(enable_persistence=False)
    store.sessions[existing_session.id] = existing_session

    class _FakeChatService:
        def __init__(self):
            self._store = store
            self.create_calls = 0

        def create_session(self, title: str | None = None, *, source: str = "browser"):
            self.create_calls += 1
            created = SessionRecord(
                id=new_id(),
                title=title or "New Chat",
                createdAt=now_iso(),
                updatedAt=now_iso(),
                source=source,
                messages=[],
            )
            self._store.sessions[created.id] = created
            return created.model_dump(exclude={"messages"})

    import bff.api.integration.feishu as feishu_module
    fake_chat_service = _FakeChatService()
    monkeypatch.setattr(feishu_module, "chat_service", fake_chat_service)

    session_map = _SessionMap()
    restored = await session_map.get_or_create(chat_id)

    assert restored == existing_session.id
    assert fake_chat_service.create_calls == 0
