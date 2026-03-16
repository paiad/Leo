from __future__ import annotations

import json
import re
from typing import Any

from app.llm import LLM
from app.logger import logger
from app.prompt.llm_prompts import (
    MCP_PLANNER_REPAIR_SYSTEM_PROMPT,
    MCP_PLANNER_SYSTEM_PROMPT,
    build_mcp_planner_prompt,
)
from bff.services.runtime.mcp_routing.runtime_plan_models import (
    ERROR_INVALID_JSON,
    PlannerRawResult,
    RetrievalResult,
)
from bff.services.runtime.runtime_policy import RuntimePolicy


class RuntimeMcpPlanner:
    def __init__(self, llm: LLM | None = None):
        # Use a dedicated LLM profile for planning when available.
        # Configure via `config/config.toml` section `[llm.planner]`.
        # Falls back to `[llm]` default when the profile is not present.
        self._llm = llm or LLM(config_name="planner")

    async def create_plan(self, prompt: str, retrieval: RetrievalResult) -> PlannerRawResult:
        user_request = RuntimePolicy.extract_current_user_request(prompt).strip()
        planner_prompt = self._build_planner_prompt(user_request, retrieval)

        try:
            response = await self._llm.ask(
                messages=[{"role": "user", "content": planner_prompt}],
                system_msgs=[{"role": "system", "content": MCP_PLANNER_SYSTEM_PROMPT}],
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
            repaired = await self._repair_to_json(response, planner_prompt)
            if repaired is not None:
                parsed = self._extract_json(repaired)
                if parsed is not None:
                    return PlannerRawResult(raw_text=repaired, parsed_json=parsed)

            return PlannerRawResult(
                raw_text=response,
                parsed_json=None,
                error_code=ERROR_INVALID_JSON,
                error_message="planner output is not valid JSON object",
            )

        return PlannerRawResult(raw_text=response, parsed_json=parsed)

    async def _repair_to_json(self, raw_response: str, planner_prompt: str) -> str | None:
        if not raw_response:
            return None

        repair_payload = {
            "task": "repair_planner_json",
            "requirement": "return one strict JSON object matching the planner output contract",
            "planner_prompt": planner_prompt,
            "raw_output": raw_response,
        }
        try:
            return await self._llm.ask(
                messages=[{"role": "user", "content": json.dumps(repair_payload, ensure_ascii=False)}],
                system_msgs=[{"role": "system", "content": MCP_PLANNER_REPAIR_SYSTEM_PROMPT}],
                stream=False,
                temperature=0,
            )
        except Exception as exc:
            logger.warning(f"Planner JSON repair call failed: {exc}")
            return None

    @staticmethod
    def _build_planner_prompt(user_request: str, retrieval: RetrievalResult) -> str:
        return build_mcp_planner_prompt(user_request, retrieval)

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

        for candidate in RuntimeMcpPlanner._extract_balanced_json_objects(text):
            parsed = RuntimeMcpPlanner._try_load_json_object(candidate)
            if parsed is not None:
                return parsed

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

    @staticmethod
    def _extract_balanced_json_objects(text: str) -> list[str]:
        candidates: list[str] = []
        start_idx: int | None = None
        depth = 0
        in_string = False
        escaped = False

        for idx, char in enumerate(text):
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue

            if char == '"':
                in_string = True
                continue

            if char == "{":
                if depth == 0:
                    start_idx = idx
                depth += 1
                continue

            if char == "}" and depth > 0:
                depth -= 1
                if depth == 0 and start_idx is not None:
                    candidates.append(text[start_idx : idx + 1].strip())
                    start_idx = None

        return candidates
