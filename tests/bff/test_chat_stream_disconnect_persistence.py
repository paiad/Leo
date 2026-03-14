import asyncio

import pytest

from bff.domain.models import ChatRequest
from bff.repositories.store import InMemoryStore
from bff.services.chat.chat_service import ChatService


class _FakeRuntime:
    async def ask(self, prompt: str, progress_callback=None) -> str:
        await asyncio.sleep(0.1)
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


@pytest.mark.asyncio
async def test_stream_message_persists_reply_when_client_disconnects():
    service = ChatService(
        store=InMemoryStore(enable_persistence=False),
        runtime=_FakeRuntime(),
        model_service=_FakeModelService(),
    )
    stream = service.stream_message(ChatRequest(content="hello"))

    first_event = await anext(stream)
    assert "event: progress" in first_event

    next_event_task = asyncio.create_task(anext(stream))
    await asyncio.sleep(0.01)
    next_event_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await next_event_task

    await asyncio.sleep(0.15)

    sessions = list(service._store.sessions.values())
    assert len(sessions) == 1
    session = sessions[0]
    assert [message.role for message in session.messages] == ["user", "assistant"]
    assert session.messages[1].content.startswith("assistant:")
