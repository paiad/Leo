from bff.repositories.store import InMemoryStore
from bff.services.chat.chat_service import ChatService


class _FakeRuntime:
    async def ask(self, prompt: str, *, session_id: str | None = None, progress_callback=None) -> str:
        return prompt


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


class _FakeRagService:
    def search(self, *, query: str, top_k: int | None = None, with_rerank: bool = True) -> dict:
        return {
            "query": query,
            "top_k": top_k or 5,
            "hits": [
                {
                    "chunk_id": "c1",
                    "score": 0.23,
                    "text": "你偏好先做最小可用增量，再快速验证并迭代。",
                    "source_path": "E:/kb/personal.md",
                    "chunk_index": 0,
                }
            ],
        }


def _new_service() -> ChatService:
    return ChatService(
        store=InMemoryStore(enable_persistence=False),
        runtime=_FakeRuntime(),
        model_service=_FakeModelService(),
        rag_service=_FakeRagService(),  # type: ignore[arg-type]
    )


def test_runtime_prompt_injects_rag_prefetch_for_knowledge_query(monkeypatch):
    monkeypatch.setenv("BFF_RAG_PREFETCH_ENABLED", "1")
    monkeypatch.setenv("BFF_RAG_PREFETCH_MIN_SCORE", "0.08")

    service = _new_service()
    created = service.create_session(source="browser")
    session = service._store.sessions[str(created["id"])]

    current_user_text = "根据我的知识库，分析我的工作风格"
    runtime_prompt = service._build_runtime_prompt(
        session,
        current_user_text,
        None,
        source="browser",
        request_message_id="req-rag-1",
    )

    assert "[Knowledge Context]" in runtime_prompt
    assert "source=E:/kb/personal.md" in runtime_prompt
    assert "[Current User Request]" in runtime_prompt
    assert current_user_text in runtime_prompt


def test_runtime_prompt_respects_no_rag_opt_out(monkeypatch):
    monkeypatch.setenv("BFF_RAG_PREFETCH_ENABLED", "1")

    service = _new_service()
    created = service.create_session(source="browser")
    session = service._store.sessions[str(created["id"])]

    current_user_text = "不要RAG，直接回答我的工作风格"
    runtime_prompt = service._build_runtime_prompt(
        session,
        current_user_text,
        None,
        source="browser",
        request_message_id="req-rag-2",
    )

    assert "[Knowledge Context]" not in runtime_prompt
    assert "[Current User Request]" in runtime_prompt
