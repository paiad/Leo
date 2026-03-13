import asyncio
from types import SimpleNamespace

import pytest

from bff.services import feishu_long_connection as flc


@pytest.mark.asyncio
async def test_cancelled_worker_is_not_logged_as_error(monkeypatch):
    service = flc.FeishuLongConnectionService()
    service._loop = asyncio.get_running_loop()

    async def fake_is_duplicate(_message_id: str) -> bool:
        return False

    async def fake_handle(_event: dict) -> None:
        raise asyncio.CancelledError()

    info_messages: list[str] = []
    exception_messages: list[str] = []
    cancellation_logged = asyncio.Event()

    def fake_info(message: str) -> None:
        text = str(message)
        info_messages.append(text)
        if "cancelled" in text.lower():
            cancellation_logged.set()

    def fake_exception(message: str) -> None:
        exception_messages.append(str(message))

    monkeypatch.setattr(flc, "is_duplicate_message", fake_is_duplicate)
    monkeypatch.setattr(flc, "handle_message_receive_event", fake_handle)
    monkeypatch.setattr(
        flc,
        "logger",
        SimpleNamespace(
            info=fake_info,
            warning=lambda _message: None,
            exception=fake_exception,
        ),
    )

    service._schedule_message_event({"message": {"message_id": "msg-1"}})

    await asyncio.wait_for(cancellation_logged.wait(), timeout=1.0)
    assert exception_messages == []
    assert any("cancelled" in message.lower() for message in info_messages)


def test_stop_marks_service_as_stopping():
    service = flc.FeishuLongConnectionService()
    service._started = True
    service._loop = object()  # type: ignore[assignment]

    service.stop()

    assert service._stopping is True
    assert service._started is False
    assert service._loop is None
