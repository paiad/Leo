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

