from mcp.types import TextContent
import pytest

from app.tool.mcp import MCPClientTool


class _FakeResult:
    def __init__(self, text: str):
        self.content = [TextContent(type="text", text=text)]


class _FakeSession:
    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    async def call_tool(self, name: str, tool_input: dict):
        self.calls.append((name, dict(tool_input)))
        if name == "browser_click":
            return _FakeResult("clicked")
        if name == "browser_wait_for":
            return _FakeResult("waited")
        return _FakeResult('{"hasMedia":true,"paused":false,"currentTime":20.1,"readyState":4}')


def test_media_toggle_click_detection():
    assert MCPClientTool._looks_like_media_toggle_click({"element": "播放/暂停"}) is True
    assert MCPClientTool._looks_like_media_toggle_click({"element": "Play/Pause button"}) is True
    assert MCPClientTool._looks_like_media_toggle_click({"element": "下一首"}) is False


@pytest.mark.asyncio
async def test_playwright_browser_click_runs_media_guard_when_toggle():
    tool = MCPClientTool(
        name="mcp_playwright_browser_click",
        description="",
        parameters={},
        session=None,
        server_id="playwright",
        original_name="browser_click",
    )
    tool.session = _FakeSession()  # type: ignore[assignment]

    result = await tool.execute(element="播放/暂停", ref="e123")

    assert result.error is None
    assert "[Playwright Media Guard]" in (result.output or "")

    call_names = [name for name, _ in tool.session.calls]  # type: ignore[union-attr]
    assert call_names[0] == "browser_click"
    assert "browser_evaluate" in call_names
    assert "browser_wait_for" in call_names
