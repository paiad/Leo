from types import SimpleNamespace

from bff.services.agent_runtime import ManusRuntime


def test_mcp_selection_ignores_history_wrapper_noise():
    runtime = ManusRuntime(store=None)
    server = SimpleNamespace(
        serverId="github",
        name="GitHub",
        description="",
        discoveredTools=[],
    )
    prompt = (
        "[Recent Session Context]\n"
        "[assistant] 我可以使用很多工具\n\n"
        "[Current User Request]\n"
        "你好"
    )

    assert runtime._should_connect_server(prompt, server) is False


def test_mcp_selection_uses_current_request_alias_match():
    runtime = ManusRuntime(store=None)
    server = SimpleNamespace(
        serverId="github",
        name="GitHub",
        description="",
        discoveredTools=[],
    )
    prompt = (
        "[Recent Session Context]\n"
        "[assistant] 之前聊了别的\n\n"
        "[Current User Request]\n"
        "帮我看看 github 上的仓库"
    )

    assert runtime._should_connect_server(prompt, server) is True


def test_mcp_selection_does_not_fanout_on_generic_mcp_tool_prompt():
    runtime = ManusRuntime(store=None)
    github = SimpleNamespace(
        serverId="github",
        name="GitHub",
        description="",
        discoveredTools=[],
    )
    trendradar = SimpleNamespace(
        serverId="trendradar",
        name="TrendRadar",
        description="",
        discoveredTools=[],
    )
    prompt = (
        "[Recent Session Context]\n"
        "[assistant] 之前聊了别的\n\n"
        "[Current User Request]\n"
        "请告诉我有哪些 mcp 工具"
    )

    assert runtime._should_connect_server(prompt, github) is False
    assert runtime._should_connect_server(prompt, trendradar) is False


def test_mcp_selection_stopwords_do_not_trigger_english_overlap():
    runtime = ManusRuntime(store=None)
    server = SimpleNamespace(
        serverId="github",
        name="GitHub MCP Server",
        description="GitHub MCP Server",
        discoveredTools=[],
    )
    prompt = (
        "[Current User Request]\n"
        "what mcp tools do you have"
    )

    assert runtime._should_connect_server(prompt, server) is False
