import pytest

from app.agent.toolcall import ToolCallAgent
from app.schema import Function, ToolCall


class _StubResponse:
    def __init__(self, *, content: str, tool_calls: list[ToolCall]):
        self.content = content
        self.tool_calls = tool_calls


def _make_tool_call() -> ToolCall:
    return ToolCall(
        id="call-1",
        function=Function(name="noop", arguments="{}"),
    )


@pytest.mark.asyncio
async def test_toolcall_think_emits_progress_thinking_when_enabled(monkeypatch):
    monkeypatch.setenv("OPENMANUS_STREAM_THOUGHTS", "1")
    monkeypatch.setenv("OPENMANUS_STREAM_THOUGHTS_MAX_CHARS", "200")

    agent = ToolCallAgent()
    events: list[dict] = []
    agent.event_callback = lambda event: events.append(event)

    async def fake_ask_tool(*args, **kwargs):
        return _StubResponse(
            content="好的！\n\n现在让我打开B站搜索 OpenManus！",
            tool_calls=[_make_tool_call()],
        )

    monkeypatch.setattr(agent.llm, "ask_tool", fake_ask_tool, raising=True)

    await agent.think()

    thinking = [
        event
        for event in events
        if event.get("type") == "progress" and event.get("phase") == "thinking"
    ]
    assert len(thinking) == 1
    assert thinking[0]["message"] == "好的！ 现在让我打开B站搜索 OpenManus！"


@pytest.mark.asyncio
async def test_toolcall_think_does_not_emit_progress_thinking_when_disabled(monkeypatch):
    monkeypatch.setenv("OPENMANUS_STREAM_THOUGHTS", "0")

    agent = ToolCallAgent()
    events: list[dict] = []
    agent.event_callback = lambda event: events.append(event)

    async def fake_ask_tool(*args, **kwargs):
        return _StubResponse(
            content="This should not be streamed",
            tool_calls=[_make_tool_call()],
        )

    monkeypatch.setattr(agent.llm, "ask_tool", fake_ask_tool, raising=True)

    await agent.think()

    assert not any(
        event.get("type") == "progress" and event.get("phase") == "thinking"
        for event in events
    )


@pytest.mark.asyncio
async def test_toolcall_think_does_not_emit_progress_thinking_without_tool_calls(monkeypatch):
    monkeypatch.setenv("OPENMANUS_STREAM_THOUGHTS", "1")

    agent = ToolCallAgent()
    events: list[dict] = []
    agent.event_callback = lambda event: events.append(event)

    async def fake_ask_tool(*args, **kwargs):
        return _StubResponse(
            content="这其实是最终答案，不应该当作 thinking 发出",
            tool_calls=[],
        )

    monkeypatch.setattr(agent.llm, "ask_tool", fake_ask_tool, raising=True)

    await agent.think()

    assert not any(
        event.get("type") == "progress" and event.get("phase") == "thinking"
        for event in events
    )


@pytest.mark.asyncio
async def test_toolcall_think_replaces_trailing_colon_in_progress_text(monkeypatch):
    monkeypatch.setenv("OPENMANUS_STREAM_THOUGHTS", "1")

    agent = ToolCallAgent()
    events: list[dict] = []
    agent.event_callback = lambda event: events.append(event)

    async def fake_ask_tool(*args, **kwargs):
        return _StubResponse(
            content="准备开始：",
            tool_calls=[_make_tool_call()],
        )

    monkeypatch.setattr(agent.llm, "ask_tool", fake_ask_tool, raising=True)

    await agent.think()

    thinking = [
        event
        for event in events
        if event.get("type") == "progress" and event.get("phase") == "thinking"
    ]
    assert thinking and thinking[0]["message"] == "准备开始。"
