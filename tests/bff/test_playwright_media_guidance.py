from app.tool.mcp import MCPClients


def test_playwright_media_guidance_contains_toggle_guard():
    description = MCPClients._augment_playwright_tool_description(
        server_id="playwright",
        tool_name="browser_click",
        description="Click an element on the page.",
    )

    assert "TOGGLE control" in description
    assert "never blind-click" in description
    assert "播放" in description
    assert "暂停" in description
    assert "already advancing" in description


def test_non_playwright_description_is_unchanged():
    original = "Generic tool description."
    description = MCPClients._augment_playwright_tool_description(
        server_id="github",
        tool_name="browser_click",
        description=original,
    )
    assert description == original
