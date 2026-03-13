from types import SimpleNamespace

from bff.services.agent_runtime import ManusRuntime


def _assistant(content: str, *, with_tool_calls: bool) -> SimpleNamespace:
    role = SimpleNamespace(value="assistant")
    tool_calls = [SimpleNamespace(id="call_1")] if with_tool_calls else None
    return SimpleNamespace(role=role, content=content, tool_calls=tool_calls)


def _tool(content: str) -> SimpleNamespace:
    role = SimpleNamespace(value="tool")
    return SimpleNamespace(role=role, content=content, tool_calls=None)


def test_select_final_assistant_prefers_non_tool_call_messages():
    messages = [
        _assistant("让我切换到视频播放页面：", with_tool_calls=True),
        _tool("Observed output of cmd `mcp_playwright_browser_tabs` executed: ..."),
        _assistant("已切换到视频页，正在播放《江南》。", with_tool_calls=False),
    ]

    assert ManusRuntime._select_final_assistant_text(messages) == "已切换到视频页，正在播放《江南》。"


def test_select_final_assistant_falls_back_when_only_tool_call_assistant_exists():
    messages = [
        _assistant("让我切换到视频播放页面：", with_tool_calls=True),
        _tool("Observed output of cmd `mcp_playwright_browser_tabs` executed: ..."),
    ]

    assert ManusRuntime._select_final_assistant_text(messages) == "让我切换到视频播放页面："


def test_select_final_assistant_returns_none_when_no_assistant_content():
    messages = [
        _tool("Observed output"),
        SimpleNamespace(role=SimpleNamespace(value="assistant"), content="  ", tool_calls=None),
    ]

    assert ManusRuntime._select_final_assistant_text(messages) is None
