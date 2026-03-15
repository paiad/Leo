from types import SimpleNamespace

from bff.services.runtime.agent_runtime import ManusRuntime
from bff.services.runtime.runtime_mcp_router import RuntimeMcpRouter


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


def test_mcp_selection_auto_routes_definition_query_to_rag():
    runtime = ManusRuntime(store=None)
    rag = SimpleNamespace(
        serverId="rag",
        name="RAG MCP",
        description="RAG MCP server",
        discoveredTools=[],
    )
    github = SimpleNamespace(
        serverId="github",
        name="GitHub",
        description="",
        discoveredTools=[],
    )
    prompt = (
        "[Current User Request]\n"
        "津液的意思"
    )

    assert runtime._should_connect_server(prompt, rag) is True
    assert runtime._should_connect_server(prompt, github) is False


def test_mcp_selection_does_not_force_rag_on_tooling_meta_query():
    runtime = ManusRuntime(store=None)
    rag = SimpleNamespace(
        serverId="rag",
        name="RAG MCP",
        description="RAG MCP server",
        discoveredTools=[],
    )
    prompt = (
        "[Current User Request]\n"
        "请告诉我有哪些 mcp tools"
    )

    assert runtime._should_connect_server(prompt, rag) is False


def test_mcp_ranking_prefers_playwright_for_browser_actions():
    router = RuntimeMcpRouter(store=None)
    servers = [
        SimpleNamespace(serverId="exa"),
        SimpleNamespace(serverId="github"),
        SimpleNamespace(serverId="playwright"),
        SimpleNamespace(serverId="rag"),
    ]
    prompt = "[Current User Request]\n打开B站并播放周杰伦稻香"

    ranked = router._rank_selected_servers(prompt, servers)
    assert ranked[0].serverId == "playwright"


def test_mcp_ranking_prefers_exa_for_search_request():
    router = RuntimeMcpRouter(store=None)
    servers = [
        SimpleNamespace(serverId="exa"),
        SimpleNamespace(serverId="trendradar"),
        SimpleNamespace(serverId="playwright"),
    ]
    prompt = "[Current User Request]\n帮我搜索今天AI新闻"

    ranked = router._rank_selected_servers(prompt, servers)
    assert ranked[0].serverId == "trendradar"


def test_mcp_selection_prefers_trendradar_for_douyin_hot_news():
    runtime = ManusRuntime(store=None)
    trendradar = SimpleNamespace(
        serverId="trendradar",
        name="trendradar",
        description="TrendRadar MCP Server",
        discoveredTools=[],
    )
    exa = SimpleNamespace(
        serverId="exa",
        name="exa",
        description="web search server",
        discoveredTools=[],
    )
    prompt = (
        "[Current User Request]\n"
        "搜索抖音今日热点top5"
    )

    assert runtime._should_connect_server(prompt, trendradar) is True
    assert runtime._should_connect_server(prompt, exa) is False


def test_tooling_meta_detection_ignores_wrapper_noise_when_task_is_news():
    router = RuntimeMcpRouter(store=None)
    prompt = (
        "[Current User Request]\n"
        "你是助手，可以使用 mcp tools server。请搜索抖音今日热点top5"
    )

    assert (
        router._classify_intent(
            router._normalize_text(router._extract_current_user_request(prompt))
        )
        == "web_search"
    )


def test_tooling_meta_query_with_news_keywords_should_not_override_to_meta():
    router = RuntimeMcpRouter(store=None)
    prompt = (
        "[Current User Request]\n"
        "你是助手，可以使用 mcp tools server，并检查配置。抖音新闻top20"
    )

    assert (
        router._classify_intent(
            router._normalize_text(router._extract_current_user_request(prompt))
        )
        == "web_search"
    )


def test_browser_intent_blocks_exa_default_match():
    runtime = ManusRuntime(store=None)
    exa = SimpleNamespace(
        serverId="exa",
        name="exa",
        description="web search server",
        discoveredTools=[],
    )
    prompt = (
        "[Current User Request]\n"
        "打开B站并播放周杰伦稻香"
    )

    assert runtime._should_connect_server(prompt, exa) is False


def test_browser_intent_allows_explicitly_named_nondefault_server():
    runtime = ManusRuntime(store=None)
    exa = SimpleNamespace(
        serverId="exa",
        name="exa",
        description="web search server",
        discoveredTools=[],
    )
    prompt = (
        "[Current User Request]\n"
        "用 exa 帮我打开B站并搜索稻香"
    )

    assert runtime._should_connect_server(prompt, exa) is True


def test_news_keywords_override_browser_wording_to_trendradar():
    router = RuntimeMcpRouter(store=None)
    prompt = "[Current User Request]\n打开抖音今日热点top5"

    intent = router._classify_intent(
        router._normalize_text(router._extract_current_user_request(prompt))
    )
    assert intent == "web_search"
