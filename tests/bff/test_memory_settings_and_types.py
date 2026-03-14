from bff.utils.memory_settings import chat_session_store_path
from bff.services.memory.types import TurnMemoryPayload


def test_chat_session_store_path_uses_override():
    path = chat_session_store_path("E:/tmp/custom-store.json")
    assert str(path).endswith("custom-store.json")


def test_turn_memory_payload_json_contains_required_fields():
    payload = TurnMemoryPayload(
        source="browser",
        session_id="sid-1",
        question="q",
        answer="a",
        model="m",
    )
    text = payload.to_json_text()
    assert '"source": "browser"' in text
    assert '"session_id": "sid-1"' in text
    assert '"user": "q"' in text
    assert '"assistant": "a"' in text
