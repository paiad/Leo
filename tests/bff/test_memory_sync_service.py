from bff.services.memory.memory_sync_service import MemorySyncService


def test_build_generic_args_prefers_text_and_session_fields():
    service = MemorySyncService(store=None)
    schema = {
        "properties": {
            "session_id": {"type": "string"},
            "source": {"type": "string"},
            "text": {"type": "string"},
        },
        "required": ["session_id", "source", "text"],
    }

    args = service._build_generic_args(
        schema=schema,
        memory_text="hello",
        source="browser",
        session_id="sid-1",
    )

    assert args == {
        "session_id": "sid-1",
        "source": "browser",
        "text": "hello",
    }


def test_build_generic_args_handles_array_and_object():
    service = MemorySyncService(store=None)
    schema = {
        "properties": {
            "items": {"type": "array"},
            "payload": {"type": "object"},
        },
        "required": ["items", "payload"],
    }

    args = service._build_generic_args(
        schema=schema,
        memory_text="hello",
        source="lark",
        session_id="sid-2",
    )

    assert args == {
        "items": ["hello"],
        "payload": {"text": "hello", "source": "lark", "session_id": "sid-2"},
    }
