import json

import pytest


@pytest.mark.asyncio
async def test_feishu_dedups_progress_and_final_when_identical(monkeypatch):
    from bff.api.integration import feishu as feishu_mod

    monkeypatch.setenv("FEISHU_SEND_STEP_PROGRESS", "1")
    monkeypatch.setenv("FEISHU_PROGRESS_MODE", "thoughts")

    sent: list[str] = []

    async def fake_send(chat_id: str, text: str) -> None:
        sent.append(text)

    async def fake_stream_message(_payload):
        yield "event: progress\ndata: " + json.dumps(
            {"phase": "thinking", "message": "同一段内容"}, ensure_ascii=False
        ) + "\n\n"
        yield "event: chunk\ndata: " + json.dumps(
            {"content": "同一段内容"}, ensure_ascii=False
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

    assert sent == ["同一段内容"]


@pytest.mark.asyncio
async def test_feishu_dedups_progress_and_final_when_semantically_similar(monkeypatch):
    from bff.api.integration import feishu as feishu_mod

    monkeypatch.setenv("FEISHU_SEND_STEP_PROGRESS", "1")
    monkeypatch.setenv("FEISHU_PROGRESS_MODE", "thoughts")

    sent: list[str] = []

    async def fake_send(chat_id: str, text: str) -> None:
        sent.append(text)

    async def fake_stream_message(_payload):
        yield "event: progress\ndata: " + json.dumps(
            {
                "phase": "thinking",
                "message": "谢谢 Paiad 的认可！很高兴这份新闻汇总对你有帮助~ 🎉 如果你之后需要：- 深入分析某个话题 - 持续追踪新闻 - 获取其他热点资讯",
            },
            ensure_ascii=False,
        ) + "\n\n"
        yield "event: chunk\ndata: " + json.dumps(
            {
                "content": "谢谢 Paiad 的认可！很高兴这份新闻汇总对你有帮助~\n\n如果你之后需要：\n- 深入分析某个话题\n- 持续追踪新闻\n- 获取其他热点资讯",
            },
            ensure_ascii=False,
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

    assert sent == [
        "谢谢 Paiad 的认可！很高兴这份新闻汇总对你有帮助~\n\n如果你之后需要：\n- 深入分析某个话题\n- 持续追踪新闻\n- 获取其他热点资讯"
    ]


@pytest.mark.asyncio
async def test_feishu_only_filters_last_thinking_when_similar_to_final(monkeypatch):
    from bff.api.integration import feishu as feishu_mod

    monkeypatch.setenv("FEISHU_SEND_STEP_PROGRESS", "1")
    monkeypatch.setenv("FEISHU_PROGRESS_MODE", "thoughts")

    sent: list[str] = []

    async def fake_send(chat_id: str, text: str) -> None:
        sent.append(text)

    async def fake_stream_message(_payload):
        yield "event: progress\ndata: " + json.dumps(
            {"phase": "thinking", "message": "我先整理一下核心点"},
            ensure_ascii=False,
        ) + "\n\n"
        yield "event: progress\ndata: " + json.dumps(
            {
                "phase": "thinking",
                "message": "结论：可以从三方面推进：目标、节奏、风险控制",
            },
            ensure_ascii=False,
        ) + "\n\n"
        yield "event: chunk\ndata: " + json.dumps(
            {"content": "结论：可以从三方面推进：目标、节奏、风险控制"},
            ensure_ascii=False,
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
            "message_id": "om_test_2",
            "message_type": "text",
            "content": json.dumps({"text": "hi"}, ensure_ascii=False),
        }
    }

    await feishu_mod._handle_receive_event(event)

    assert sent == ["我先整理一下核心点", "结论：可以从三方面推进：目标、节奏、风险控制"]
