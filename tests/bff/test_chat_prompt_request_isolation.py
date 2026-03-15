from bff.domain.models import MessageRecord, now_iso
from bff.repositories.store import InMemoryStore
from bff.services.chat.chat_service import ChatService
from bff.services.chat.context_memory_service import ContextBundle
from bff.services.runtime.mcp_routing.runtime_mcp_router import RuntimeMcpRouter


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


class _FakeContextMemory:
    def __init__(self, text: str) -> None:
        self._text = text
        self.audit_calls: list[dict] = []

    def build_context_bundle(self, *, session_id: str, current_user_text: str) -> ContextBundle:
        return ContextBundle(
            text=self._text,
            summary_ids=[1] if self._text else [],
            fact_ids=[],
            used_tokens=32 if self._text else 0,
            budget_tokens=256,
        )

    def persist_injection_audit(
        self,
        *,
        session_id: str,
        request_message_id: str | None,
        query_text: str,
        bundle: ContextBundle,
    ) -> None:
        self.audit_calls.append(
            {
                "session_id": session_id,
                "request_message_id": request_message_id,
                "query_text": query_text,
                "bundle_text": bundle.text,
            }
        )


def _service_with_context(summary_text: str) -> ChatService:
    service = ChatService(
        store=InMemoryStore(enable_persistence=False),
        runtime=_FakeRuntime(),
        model_service=_FakeModelService(),
    )
    service._context_memory = _FakeContextMemory(summary_text)  # type: ignore[assignment]
    return service


def test_runtime_prompt_keeps_current_request_clean_when_summary_injected():
    service = _service_with_context(
        "[Session Summary:rolling]\nGoals:\n- 获取今日抖音Top17热搜新闻"
    )
    created = service.create_session(source="lark")
    session_id = str(created["id"])
    session = service._store.sessions[session_id]

    current_user_text = "播放周杰伦的稻香[派对]"
    session.messages.extend(
        [
            MessageRecord(
                id="u-old",
                role="user",
                content="可以给我今日抖音top17的新闻么？",
                createdAt=now_iso(),
            ),
            MessageRecord(
                id="a-old",
                role="assistant",
                content="已返回抖音Top17热搜。",
                createdAt=now_iso(),
            ),
            MessageRecord(
                id="u-now",
                role="user",
                content=current_user_text,
                createdAt=now_iso(),
            ),
        ]
    )

    runtime_prompt = service._build_runtime_prompt(
        session,
        current_user_text,
        None,
        source="lark",
        request_message_id="req-1",
    )

    router = RuntimeMcpRouter(store=None)
    extracted = router.current_user_request(runtime_prompt)

    assert "[Session Summary:rolling]" in runtime_prompt
    assert extracted == current_user_text
    assert "抖音" not in extracted
    assert router.classify_prompt_intent(runtime_prompt) == "browser_automation"


def test_runtime_prompt_still_has_current_request_marker_without_history():
    service = _service_with_context("[Session Summary:rolling]\n- 抖音热点历史")
    created = service.create_session(source="lark")
    session = service._store.sessions[str(created["id"])]
    current_user_text = "播放周杰伦的稻香"

    runtime_prompt = service._build_runtime_prompt(
        session,
        current_user_text,
        None,
        source="lark",
        request_message_id="req-2",
    )
    router = RuntimeMcpRouter(store=None)
    extracted = router.current_user_request(runtime_prompt)

    assert "[Current User Request]" in runtime_prompt
    assert extracted == current_user_text
