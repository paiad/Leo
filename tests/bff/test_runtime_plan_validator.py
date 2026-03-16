from bff.domain.models import McpDiscoveredTool, McpServerRecord
from bff.repositories.store import InMemoryStore
from bff.services.runtime.mcp_routing.runtime_plan_models import (
    ERROR_SERVER_NOT_ALLOWED,
    ERROR_TOOL_NOT_ALLOWED,
    PlannerFallback,
    RetrievalResult,
)
from bff.services.runtime.mcp_routing.runtime_plan_validator import RuntimeMcpPlanValidator


def _store() -> InMemoryStore:
    store = InMemoryStore(enable_persistence=False)
    store.mcp_servers["playwright"] = McpServerRecord(
        serverId="playwright",
        name="playwright",
        type="stdio",
        command="dummy",
        enabled=True,
        discoveredTools=[
            McpDiscoveredTool(
                name="browser_navigate",
                description="navigate",
                inputSchema={"type": "object", "properties": {"url": {"type": "string"}}},
                enabled=True,
            ),
            McpDiscoveredTool(
                name="browser_click",
                description="click",
                inputSchema={"type": "object", "properties": {"selector": {"type": "string"}}},
                enabled=True,
            ),
        ],
    )
    return store


def _retrieval() -> RetrievalResult:
    return RetrievalResult(
        intent="browser_automation",
        candidate_servers=["playwright"],
        candidate_tools={"playwright": ["browser_navigate", "browser_click"]},
        candidate_tool_profiles={},
        fallback=PlannerFallback(
            mode="rule_route",
            server_id="playwright",
            tool_name="browser_navigate",
            reason="rule",
        ),
    )


def test_plan_validator_accepts_prefixed_tool_name():
    validator = RuntimeMcpPlanValidator(store=_store())
    result = validator.validate(
        {
            "version": "mcp-plan.v1",
            "need_mcp": True,
            "plan_steps": [
                {
                    "goal": "open site",
                    "server_id": "playwright",
                    "tool_name": "mcp_playwright_browser_navigate",
                    "args_hint": {},
                    "confidence": 0.9,
                    "reason": "browser action",
                }
            ],
            "fallback": {
                "mode": "rule_route",
                "server_id": "playwright",
                "tool_name": "browser_navigate",
                "reason": "fallback",
            },
        },
        retrieval=_retrieval(),
    )

    assert result.plan is not None
    assert result.error_code is None


def test_plan_validator_blocks_unknown_server():
    validator = RuntimeMcpPlanValidator(store=_store())
    result = validator.validate(
        {
            "version": "mcp-plan.v1",
            "need_mcp": True,
            "plan_steps": [
                {
                    "goal": "open site",
                    "server_id": "github",
                    "tool_name": "list_commits",
                    "args_hint": {},
                    "confidence": 0.9,
                    "reason": "repo",
                }
            ],
            "fallback": {
                "mode": "rule_route",
                "server_id": "playwright",
                "tool_name": "browser_navigate",
                "reason": "fallback",
            },
        },
        retrieval=_retrieval(),
    )

    assert result.plan is None
    assert result.error_code == ERROR_SERVER_NOT_ALLOWED


def test_plan_validator_blocks_unknown_tool():
    validator = RuntimeMcpPlanValidator(store=_store())
    result = validator.validate(
        {
            "version": "mcp-plan.v1",
            "need_mcp": True,
            "plan_steps": [
                {
                    "goal": "open site",
                    "server_id": "playwright",
                    "tool_name": "browser_tabs",
                    "args_hint": {},
                    "confidence": 0.9,
                    "reason": "browser",
                }
            ],
            "fallback": {
                "mode": "rule_route",
                "server_id": "playwright",
                "tool_name": "browser_navigate",
                "reason": "fallback",
            },
        },
        retrieval=_retrieval(),
    )

    assert result.plan is None
    assert result.error_code == ERROR_TOOL_NOT_ALLOWED


def test_plan_validator_repairs_null_fallback():
    validator = RuntimeMcpPlanValidator(store=_store())
    result = validator.validate(
        {
            "version": "mcp-plan.v1",
            "need_mcp": True,
            "plan_steps": [
                {
                    "goal": "open site",
                    "server_id": "playwright",
                    "tool_name": "browser_navigate",
                    "args_hint": {},
                    "confidence": 0.9,
                    "reason": "browser action",
                }
            ],
            "fallback": None,
        },
        retrieval=_retrieval(),
    )

    assert result.plan is not None
    assert result.plan.fallback.mode == "rule_route"
    assert result.plan.fallback.server_id == "playwright"
    assert result.plan.fallback.tool_name == "browser_navigate"
