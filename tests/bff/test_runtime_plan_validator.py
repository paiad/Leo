from bff.services.runtime.mcp_routing.runtime_plan_models import (
    ERROR_SERVER_NOT_ALLOWED,
    ERROR_TOOL_NOT_ALLOWED,
    PrefilterResult,
    PlannerFallback,
)
from bff.services.runtime.mcp_routing.runtime_plan_validator import RuntimeMcpPlanValidator


def _prefilter() -> PrefilterResult:
    return PrefilterResult(
        intent="browser_automation",
        need_mcp=True,
        candidate_servers=["playwright"],
        candidate_tools={"playwright": ["browser_navigate", "browser_click"]},
        rule_fallback=PlannerFallback(
            mode="rule_route",
            server_id="playwright",
            tool_name="browser_navigate",
            reason="rule",
        ),
    )


def test_plan_validator_accepts_prefixed_tool_name():
    validator = RuntimeMcpPlanValidator(store=None)
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
        prefilter=_prefilter(),
    )

    assert result.plan is not None
    assert result.error_code is None


def test_plan_validator_blocks_unknown_server():
    validator = RuntimeMcpPlanValidator(store=None)
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
        prefilter=_prefilter(),
    )

    assert result.plan is None
    assert result.error_code == ERROR_SERVER_NOT_ALLOWED


def test_plan_validator_blocks_unknown_tool():
    validator = RuntimeMcpPlanValidator(store=None)
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
        prefilter=_prefilter(),
    )

    assert result.plan is None
    assert result.error_code == ERROR_TOOL_NOT_ALLOWED
