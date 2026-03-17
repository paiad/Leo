import pytest
from datetime import datetime
from zoneinfo import ZoneInfo

from bff.services.runtime.mcp_routing.runtime_plan_models import PlannerFallback, RetrievalResult
from bff.services.runtime.mcp_routing.runtime_planner import RuntimeMcpPlanner


class _FakeLLM:
    def __init__(self, responses: list[str]):
        self._responses = list(responses)
        self.calls = 0

    async def ask(self, **kwargs):  # noqa: ANN003
        self.calls += 1
        if not self._responses:
            raise RuntimeError("no more fake responses")
        return self._responses.pop(0)


class _CaptureLLM:
    def __init__(self, response: str):
        self.response = response
        self.calls: list[dict] = []

    async def ask(self, **kwargs):  # noqa: ANN003
        self.calls.append(kwargs)
        return self.response


def _retrieval() -> RetrievalResult:
    return RetrievalResult(
        intent="general",
        candidate_servers=["rag"],
        candidate_tools={"rag": ["stats"]},
        candidate_tool_profiles={},
        fallback=PlannerFallback(mode="rule_route", server_id="rag", tool_name="stats", reason="fallback"),
    )


@pytest.mark.asyncio
async def test_planner_repairs_invalid_json_output():
    bad = "Here is the plan:\nneed_mcp=true"
    repaired = (
        '{"version":"mcp-plan.v1","need_mcp":true,"plan_steps":[{"goal":"query","server_id":"rag",'
        '"tool_name":"stats","args_hint":{},"confidence":0.9,"reason":"matched"}],'
        '"fallback":{"mode":"rule_route","server_id":"rag","tool_name":"stats","reason":"fallback"}}'
    )
    planner = RuntimeMcpPlanner(llm=_FakeLLM([bad, repaired]))

    result = await planner.create_plan("[Current User Request]\n科技相关的呢？", _retrieval())

    assert result.parsed_json is not None
    assert result.error_code is None


def test_extract_json_from_mixed_text_with_multiple_braces():
    raw = (
        "noise {not-json}\n"
        "```json\n"
        '{"version":"mcp-plan.v1","need_mcp":false,"plan_steps":[],"fallback":{"mode":"no_mcp","reason":"x"}}\n'
        "```\n"
        "tail"
    )

    parsed = RuntimeMcpPlanner._extract_json(raw)

    assert parsed is not None
    assert parsed["version"] == "mcp-plan.v1"
    assert parsed["need_mcp"] is False


@pytest.mark.asyncio
async def test_planner_prompt_includes_shanghai_today_context():
    response = (
        '{"version":"mcp-plan.v1","need_mcp":false,"plan_steps":[],'
        '"fallback":{"mode":"no_mcp","server_id":null,"tool_name":null,"reason":"x"}}'
    )
    llm = _CaptureLLM(response=response)
    planner = RuntimeMcpPlanner(llm=llm)

    result = await planner.create_plan("[Current User Request]\n今日 AI 新闻", _retrieval())

    assert result.parsed_json is not None
    assert len(llm.calls) == 1
    message_text = llm.calls[0]["messages"][0]["content"]
    payload = RuntimeMcpPlanner._extract_json(message_text)
    assert payload is not None
    expected_today = datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()
    assert payload["time_context"]["timezone"] == "Asia/Shanghai"
    assert payload["time_context"]["today"] == expected_today
