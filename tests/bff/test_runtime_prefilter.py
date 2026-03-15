from bff.domain.models import McpServerRecord
from bff.repositories.store import InMemoryStore
from bff.services.runtime.mcp_routing.runtime_mcp_router import RuntimeMcpRouter
from bff.services.runtime.mcp_routing.runtime_prefilter import RuntimeMcpPrefilter


def _server(server_id: str) -> McpServerRecord:
    return McpServerRecord(
        serverId=server_id,
        name=server_id,
        type="stdio",
        command="dummy",
        enabled=True,
    )


def test_prefilter_mixed_news_and_browser_prioritizes_search_then_browser():
    store = InMemoryStore(enable_persistence=False)
    store.mcp_servers["trendradar"] = _server("trendradar")
    store.mcp_servers["playwright"] = _server("playwright")
    store.mcp_servers["exa"] = _server("exa")

    router = RuntimeMcpRouter(store=store)
    prefilter = RuntimeMcpPrefilter(router=router, store=store)

    result = prefilter.build("[Current User Request]\n先查抖音热点top20，再打开B站播放稻香")

    assert result.need_mcp is True
    assert result.candidate_servers[:2] == ["trendradar", "playwright"]


def test_prefilter_tooling_meta_query_defaults_to_no_mcp():
    store = InMemoryStore(enable_persistence=False)
    store.mcp_servers["github"] = _server("github")

    router = RuntimeMcpRouter(store=store)
    prefilter = RuntimeMcpPrefilter(router=router, store=store)

    result = prefilter.build("[Current User Request]\n有哪些 mcp 工具")

    assert result.need_mcp is False
    assert result.candidate_servers == []
    assert result.rule_fallback.mode == "no_mcp"
