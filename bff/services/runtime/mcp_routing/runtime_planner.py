from __future__ import annotations

import json
import re
from typing import Any

from app.llm import LLM
from app.logger import logger
from bff.services.runtime.mcp_routing.runtime_plan_models import (
    ERROR_INVALID_JSON,
    PlannerRawResult,
    PrefilterResult,
)
from bff.services.runtime.runtime_policy import RuntimePolicy


class RuntimeMcpPlanner:
    _SYSTEM_PROMPT = (
        "You are a strict MCP planning engine. "
        "Return JSON only. No markdown, no prose, no code fences."
    )

    def __init__(self, llm: LLM | None = None):
        self._llm = llm or LLM()

    async def create_plan(self, prompt: str, prefilter: PrefilterResult) -> PlannerRawResult:
        user_request = RuntimePolicy.extract_current_user_request(prompt).strip()
        planner_prompt = self._build_planner_prompt(user_request, prefilter)

        try:
            response = await self._llm.ask(
                messages=[{"role": "user", "content": planner_prompt}],
                system_msgs=[{"role": "system", "content": self._SYSTEM_PROMPT}],
                stream=False,
                temperature=0,
            )
        except Exception as exc:
            logger.warning(f"Planner LLM call failed: {exc}")
            return PlannerRawResult(
                raw_text="",
                parsed_json=None,
                error_code=ERROR_INVALID_JSON,
                error_message=f"planner_call_failed: {exc}",
            )

        parsed = self._extract_json(response)
        if parsed is None:
            return PlannerRawResult(
                raw_text=response,
                parsed_json=None,
                error_code=ERROR_INVALID_JSON,
                error_message="planner output is not valid JSON object",
            )

        return PlannerRawResult(raw_text=response, parsed_json=parsed)

    @staticmethod
    def _build_planner_prompt(user_request: str, prefilter: PrefilterResult) -> str:
        payload = {
            "version": "mcp-plan.v1",
            "request": user_request,
            "prefilter": {
                "intent": prefilter.intent,
                "need_mcp": prefilter.need_mcp,
                "candidate_servers": prefilter.candidate_servers,
                "candidate_tools": prefilter.candidate_tools,
                "rule_fallback": prefilter.rule_fallback.model_dump(mode="json"),
            },
            "output_contract": {
                "need_mcp": "boolean",
                "plan_steps": [
                    {
                        "goal": "string",
                        "server_id": "must be in candidate_servers",
                        "tool_name": "must be in candidate_tools[server_id] when non-empty",
                        "args_hint": "object",
                        "confidence": "number in [0,1]",
                        "reason": "string",
                    }
                ],
                "fallback": {
                    "mode": "rule_route|no_mcp|explain_fail",
                    "server_id": "string|null",
                    "tool_name": "string|null",
                    "reason": "string",
                },
            },
            "rules": [
                "output strict JSON object only",
                "do not use keys outside contract",
                "if no MCP needed, set need_mcp=false and plan_steps=[]",
                "prefer shortest correct plan",
            ],
        }
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _extract_json(raw_text: str) -> dict[str, Any] | None:
        if not raw_text:
            return None

        text = raw_text.strip()

        direct = RuntimeMcpPlanner._try_load_json_object(text)
        if direct is not None:
            return direct

        fence_match = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", text, flags=re.IGNORECASE)
        if fence_match:
            fenced = RuntimeMcpPlanner._try_load_json_object(fence_match.group(1).strip())
            if fenced is not None:
                return fenced

        first = text.find("{")
        last = text.rfind("}")
        if first >= 0 and last > first:
            sliced = RuntimeMcpPlanner._try_load_json_object(text[first : last + 1].strip())
            if sliced is not None:
                return sliced

        return None

    @staticmethod
    def _try_load_json_object(candidate: str) -> dict[str, Any] | None:
        try:
            data = json.loads(candidate)
        except Exception:
            return None
        return data if isinstance(data, dict) else None
