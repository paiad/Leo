from __future__ import annotations

import os
import re
from typing import Any


class RuntimeStallDetector:
    def __init__(self, same_tool_limit: int = 3, tool_error_limit: int = 3):
        self._same_tool_limit = max(2, same_tool_limit)
        self._tool_error_limit = max(2, tool_error_limit)
        self._last_tool_signature: str | None = None
        self._same_tool_count = 0
        self._tool_error_count = 0

    @staticmethod
    def _tool_signature(event: dict[str, Any]) -> str:
        tool_name = str(event.get("toolName") or "").strip()
        args = str(event.get("arguments") or "").strip()
        return f"{tool_name}:{args}"

    def observe(self, event: dict[str, Any]) -> str | None:
        event_type = str(event.get("type") or "")

        if event_type == "tool_start":
            signature = self._tool_signature(event)
            if signature and signature == self._last_tool_signature:
                self._same_tool_count += 1
            else:
                self._same_tool_count = 1
                self._last_tool_signature = signature

            if self._same_tool_count >= self._same_tool_limit:
                return (
                    "stall_detected_same_tool_loop:"
                    f" signature repeated {self._same_tool_count} times"
                )
            return None

        if event_type == "tool_done":
            ok = bool(event.get("ok"))
            if ok:
                self._tool_error_count = 0
            else:
                self._tool_error_count += 1
                if self._tool_error_count >= self._tool_error_limit:
                    return (
                        "stall_detected_consecutive_tool_errors:"
                        f" {self._tool_error_count} failures"
                    )
            return None

        return None


class RuntimePolicy:
    @staticmethod
    def is_truthy_env(value: str | None) -> bool:
        return (value or "").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def env_int(name: str, default: int, minimum: int | None = None) -> int:
        raw = os.getenv(name)
        if raw is None:
            value = default
        else:
            try:
                value = int(raw.strip())
            except ValueError:
                value = default
        if minimum is not None:
            return max(minimum, value)
        return value

    @staticmethod
    def extract_current_user_request(prompt: str) -> str:
        marker = "[Current User Request]"
        if marker not in prompt:
            return prompt
        return prompt.rsplit(marker, 1)[-1].strip()

    def resolve_time_budget_seconds(self) -> int:
        return self.env_int("BFF_RUNTIME_TIME_BUDGET_SECONDS", 180)

    def estimate_dynamic_steps(self, prompt: str, base_steps: int) -> int:
        text = self.extract_current_user_request(prompt).strip()
        if not text:
            return base_steps

        lowered = text.lower()
        chars = len(text)
        words = len(re.findall(r"\w+", lowered))
        lines = text.count("\n") + 1

        complex_markers = (
            "implement",
            "debug",
            "refactor",
            "migrate",
            "analyze",
            "design",
            "optimize",
            "集成",
            "重构",
            "排查",
            "修复",
            "设计",
            "实现",
        )
        sequence_markers = (
            " then ",
            " after ",
            " first ",
            " next ",
            "最后",
            "然后",
            "接着",
            "先",
            "再",
        )

        score = 0
        if chars > 600:
            score += 2
        if chars > 1500:
            score += 2
        if words > 120:
            score += 1
        if lines > 8:
            score += 1
        if any(marker in lowered for marker in complex_markers):
            score += 2
        if sum(1 for marker in sequence_markers if marker in lowered) >= 2:
            score += 1
        if chars < 80 and words < 20 and score == 0:
            score -= 2

        dynamic_cap = self.env_int("BFF_RUNTIME_DYNAMIC_STEPS_MAX", 15, minimum=6)
        dynamic_floor = self.env_int("BFF_RUNTIME_DYNAMIC_STEPS_MIN", 4, minimum=1)
        return max(dynamic_floor, min(dynamic_cap, base_steps + score))

    def resolve_max_steps(self, prompt: str, max_steps: int | None) -> int:
        if max_steps is not None:
            return max(1, max_steps)

        base_steps = self.env_int("BFF_MANUS_MAX_STEPS", 10, minimum=1)
        dynamic_enabled = self.is_truthy_env(
            os.getenv("BFF_RUNTIME_DYNAMIC_STEPS_ENABLED", "1")
        )
        if not dynamic_enabled:
            return base_steps
        return self.estimate_dynamic_steps(prompt, base_steps)

    @staticmethod
    def has_rag_tool_activity(messages: list[Any]) -> bool:
        return RuntimePolicy.has_server_tool_activity(messages, "rag")

    @staticmethod
    def has_server_tool_activity(messages: list[Any], server_id: str) -> bool:
        server_id = (server_id or "").strip().lower()
        if not server_id:
            return False
        prefix = f"mcp_{server_id}_"
        for message in messages:
            role = getattr(message, "role", None)
            role_value = getattr(role, "value", role)
            if role_value == "tool":
                tool_name = str(getattr(message, "name", "") or "").strip().lower()
                if tool_name.startswith(prefix):
                    return True
                continue
            if role_value != "assistant":
                continue
            tool_calls = getattr(message, "tool_calls", None) or []
            for call in tool_calls:
                function = getattr(call, "function", None)
                name = str(getattr(function, "name", "") or "").strip().lower()
                if name.startswith(prefix):
                    return True
        return False

    @staticmethod
    def build_forced_rag_retry_prompt(prompt: str) -> str:
        reminder = (
            "[Runtime Enforcement]\n"
            "你必须至少调用一次 mcp_rag_search 工具，再给出最终答复。\n"
            "调用参数要求：top_k=8, with_rerank=true。\n"
            "若知识库未命中，请明确说明“知识库未命中”，并给出下一步建议。"
        )
        return f"{prompt}\n\n{reminder}"

    @staticmethod
    def build_forced_trendradar_retry_prompt(prompt: str) -> str:
        reminder = (
            "[Runtime Enforcement]\n"
            "你必须至少调用一次 TrendRadar MCP 工具，再给出最终答复。\n"
            "优先调用 mcp_trendradar_get_latest_news；\n"
            "如需关键词检索可调用 mcp_trendradar_search_news。\n"
            "必须基于 TrendRadar 工具返回的数据作答，不要改用 python_execute 抓网页。\n"
        )
        return f"{prompt}\n\n{reminder}"

    @staticmethod
    def build_forced_playwright_retry_prompt(prompt: str) -> str:
        reminder = (
            "[Runtime Enforcement]\n"
            "你必须至少调用一次 Playwright MCP 工具完成网页操作，再给出最终答复。\n"
            "优先调用 mcp_playwright_browser_navigate，然后按需调用 click/type 等工具。\n"
            "禁止把网页操作替换为 str_replace_editor 或 python_execute。\n"
        )
        return f"{prompt}\n\n{reminder}"
