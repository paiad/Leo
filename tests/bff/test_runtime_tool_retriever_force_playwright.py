from bff.services.runtime.mcp_routing.runtime_mcp_router import RuntimeMcpRouter
from bff.services.runtime.mcp_routing.runtime_tool_index_sqlite import ToolIndexRow
from bff.services.runtime.mcp_routing.runtime_tool_retriever import RuntimeMcpToolRetriever


def test_tool_retriever_forces_playwright_for_browser_prompt(monkeypatch):
    monkeypatch.setenv("BFF_RUNTIME_FORCE_PLAYWRIGHT_FOR_BROWSER", "1")

    router = RuntimeMcpRouter(store=None)
    retriever = RuntimeMcpToolRetriever(router=router, store=None)

    monkeypatch.setattr(retriever._index, "refresh_from_store", lambda _store: False)
    monkeypatch.setattr(
        retriever._index,
        "search",
        lambda **_kwargs: [
            ToolIndexRow(
                server_id="trendradar",
                tool_name="aggregate_news",
                title="trendradar/aggregate_news",
                text="",
                schema_json={},
                score=0.99,
                keyword_score=0.0,
                vector_score=0.99,
            )
        ],
    )
    monkeypatch.setattr(
        retriever._index,
        "list_enabled_tool_names",
        lambda *, server_id, limit=30: ["browser_navigate", "browser_click"]
        if server_id == "playwright"
        else [],
    )
    monkeypatch.setattr(retriever, "_enabled_server_ids", lambda: ["playwright", "trendradar"])

    prompt = "[Current User Request]\n打开B站并播放周杰伦稻香"
    result = retriever.retrieve(prompt)

    assert result.intent == "browser_automation"
    assert result.candidate_servers[0] == "playwright"
    assert result.fallback_server_id == "playwright"
