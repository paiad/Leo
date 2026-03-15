import pytest

import bff.api.integration.feishu as feishu_module


def test_extract_audio_file_key_from_audio_message():
    message = {
        "message_type": "audio",
        "content": '{"file_key":"file_v3_audio_123","duration":1200}',
    }
    assert feishu_module._extract_audio_file_key(message) == "file_v3_audio_123"


@pytest.mark.asyncio
async def test_resolve_user_input_text_from_text_message():
    message = {
        "message_type": "text",
        "content": '{"text":"hello world"}',
    }
    assert await feishu_module._resolve_user_input_text(message) == "hello world"


@pytest.mark.asyncio
async def test_resolve_user_input_text_from_audio_message(monkeypatch):
    async def fake_download_audio_resource(message_id: str, file_key: str) -> bytes:
        assert message_id == "om_xxx"
        assert file_key == "file_v3_audio_456"
        return b"fake-audio-binary"

    class _FakeAsrEngine:
        async def transcribe_audio(self, audio_bytes: bytes) -> str:
            assert audio_bytes == b"fake-audio-binary"
            return "这是语音转写文本"

    monkeypatch.setattr(feishu_module, "_download_audio_resource", fake_download_audio_resource)
    monkeypatch.setattr(feishu_module, "_local_asr_engine", _FakeAsrEngine())
    monkeypatch.setattr(feishu_module, "_env_bool", lambda _name, _default: True)

    message = {
        "message_id": "om_xxx",
        "message_type": "audio",
        "content": '{"file_key":"file_v3_audio_456"}',
    }
    assert await feishu_module._resolve_user_input_text(message) == "这是语音转写文本"


@pytest.mark.asyncio
async def test_handle_receive_event_audio_resolve_exception_returns_gracefully(monkeypatch):
    async def fake_should_reply(_event: dict, _message: dict) -> bool:
        return True

    async def fake_resolve_user_input_text(_message: dict) -> str:
        raise RuntimeError("asr error")

    sent_messages: list[str] = []

    async def fake_send_text_message(_chat_id: str, text: str) -> None:
        sent_messages.append(text)

    monkeypatch.setattr(feishu_module, "_should_reply", lambda _event, _message: True)
    monkeypatch.setattr(feishu_module, "_resolve_user_input_text", fake_resolve_user_input_text)
    monkeypatch.setattr(feishu_module, "_send_text_message", fake_send_text_message)

    event = {
        "message": {
            "chat_id": "oc_123",
            "chat_type": "p2p",
            "message_id": "om_123",
            "message_type": "audio",
            "content": '{"file_key":"file_v3_audio_789"}',
        },
        "sender": {"sender_type": "user"},
    }
    await feishu_module._handle_receive_event(event)
    assert any("语音识别失败" in item for item in sent_messages)


@pytest.mark.asyncio
async def test_download_audio_resource_uses_file_type(monkeypatch):
    class _Resp:
        status_code = 200
        headers = {"content-type": "audio/ogg"}
        content = b"ok"
        text = ""

        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, *args, **kwargs):
            self.last_params = None

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, _url, params=None, headers=None):
            self.last_params = params
            assert params == {"type": "file"}
            assert headers and "Authorization" in headers
            return _Resp()

    class _TokenStore:
        async def get(self) -> str:
            return "token_xxx"

    monkeypatch.setattr(feishu_module, "_token_store", _TokenStore())
    monkeypatch.setattr(feishu_module.httpx, "AsyncClient", _Client)

    data = await feishu_module._download_audio_resource("om_xxx", "file_xxx")
    assert data == b"ok"
