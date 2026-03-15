import json

import pytest


@pytest.mark.asyncio
async def test_feishu_skips_truncated_thoughts_progress(monkeypatch):
    from bff.api.integration import feishu as feishu_mod

    monkeypatch.setenv("FEISHU_SEND_STEP_PROGRESS", "1")
    monkeypatch.setenv("FEISHU_PROGRESS_MODE", "thoughts")

    sent: list[str] = []

    async def fake_send(chat_id: str, text: str) -> None:
        sent.append(text)

    async def fake_stream_message(_payload):
        yield "event: progress\ndata: " + json.dumps(
            {"phase": "thinking", "message": "很长很长... [truncated]"}, ensure_ascii=False
        ) + "\n\n"
        yield "event: chunk\ndata: " + json.dumps(
            {"content": "最终总结"}, ensure_ascii=False
        ) + "\n\n"
        yield "event: done\ndata: " + json.dumps({"done": True}, ensure_ascii=False) + "\n\n"

    monkeypatch.setattr(feishu_mod, "_send_text_message", fake_send, raising=True)
    monkeypatch.setattr(
        feishu_mod.chat_service, "stream_message", fake_stream_message, raising=True
    )

    event = {
        "message": {
            "chat_id": "oc_test",
            "chat_type": "p2p",
            "message_id": "om_test",
            "message_type": "text",
            "content": json.dumps({"text": "hi"}, ensure_ascii=False),
        }
    }

    await feishu_mod._handle_receive_event(event)

    assert sent == ["最终总结"]

