import pytest

from bff.domain.models import McpDiscoveredTool, McpServerRecord
from bff.repositories.store import InMemoryStore
from bff.services.runtime.mcp_routing.runtime_mcp_router import RuntimeMcpRouter
from bff.services.runtime.mcp_routing.runtime_plan_models import PlannerRawResult
from bff.services.runtime.mcp_routing.runtime_planning import RuntimeMcpPlanningOrchestrator


def _server(server_id: str) -> McpServerRecord:
    return McpServerRecord(
        serverId=server_id,
        name=server_id,
        type="stdio",
        command="dummy",
        enabled=True,
        discoveredTools=[
            McpDiscoveredTool(
                name="browser_navigate",
                description="navigate",
                inputSchema={"type": "object", "properties": {"url": {"type": "string"}}},
                enabled=True,
            )
        ],
    )


@pytest.mark.asyncio
async def test_planning_orchestrator_falls_back_when_planner_json_invalid(monkeypatch, tmp_path):
    store = InMemoryStore(enable_persistence=False)
    store.mcp_servers["playwright"] = _server("playwright")

    router = RuntimeMcpRouter(store=store)
    orchestrator = RuntimeMcpPlanningOrchestrator(router=router, store=store)

    async def _invalid_plan(prompt, retrieval):
        return PlannerRawResult(raw_text="not-json", parsed_json=None, error_code="E1001_INVALID_JSON")

    monkeypatch.setattr(orchestrator._planner, "create_plan", _invalid_plan)
    monkeypatch.setenv("BFF_RUNTIME_PLANNER_ENABLED", "1")
    monkeypatch.setenv("BFF_MCP_TOOL_INDEX_SQLITE_PATH", str(tmp_path / "mcp_tool_index.sqlite3"))
    monkeypatch.setenv("BFF_MCP_TOOL_INDEX_EMBEDDINGS_ENABLED", "0")

    decision = await orchestrator.decide("[Current User Request]\n打开B站并播放视频")

    assert decision.execute_source == "rule"
    assert decision.execute_plan.need_mcp is True
    assert decision.gate_error_code == "E1001_INVALID_JSON"


@pytest.mark.asyncio
async def test_planning_orchestrator_returns_no_mcp_for_meta_query(monkeypatch, tmp_path):
    store = InMemoryStore(enable_persistence=False)
    store.mcp_servers["github"] = _server("github")

    router = RuntimeMcpRouter(store=store)
    orchestrator = RuntimeMcpPlanningOrchestrator(router=router, store=store)

    monkeypatch.setenv("BFF_RUNTIME_PLANNER_ENABLED", "1")
    monkeypatch.setenv("BFF_MCP_TOOL_INDEX_SQLITE_PATH", str(tmp_path / "mcp_tool_index.sqlite3"))
    monkeypatch.setenv("BFF_MCP_TOOL_INDEX_EMBEDDINGS_ENABLED", "0")

    decision = await orchestrator.decide("[Current User Request]\n请告诉我有哪些 mcp tools")

    assert decision.execute_source == "no_mcp"
    assert decision.execute_plan.need_mcp is False
