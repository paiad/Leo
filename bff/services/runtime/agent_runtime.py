from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Callable, Protocol

from app.agent.manus import Manus
from app.logger import logger
from app.schema import AgentState
from app.schema import Memory
from app.tool.tool_collection import ToolCollection
from bff.repositories.store import InMemoryStore
from bff.services.runtime.runtime_finalizer import RuntimeFinalizer
from bff.services.runtime.runtime_mcp_router import RuntimeMcpRouter
from bff.services.runtime.runtime_progress import RuntimeProgressEmitter


class _RuntimeStallDetector:
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


class AgentRuntime(Protocol):
    async def ask(
        self,
        prompt: str,
        *,
        max_steps: int | None = None,
        progress_callback: Callable[[dict[str, Any]], Any] | None = None,
    ) -> str: ...


class ManusRuntime:
    def __init__(self, store: InMemoryStore | None = None):
        self._store = store
        self._shared_agent: Manus | None = None
        self._shared_agent_lock = asyncio.Lock()
        self._progress = RuntimeProgressEmitter()
        self._mcp_router = RuntimeMcpRouter(store)
        self._finalizer = RuntimeFinalizer(self._extract_current_user_request)

    @staticmethod
    def _is_truthy_env(value: str | None) -> bool:
        return (value or "").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _extract_current_user_request(prompt: str) -> str:
        """
        Runtime prompts may include history wrappers. Keep MCP routing focused on
        the current user request to avoid accidental server fan-out.
        """
        marker = "[Current User Request]"
        if marker not in prompt:
            return prompt
        return prompt.rsplit(marker, 1)[-1].strip()

    @staticmethod
    def _select_final_assistant_text(messages: list[Any]) -> str | None:
        """
        Backward-compatible selector kept for existing tests/callers.
        Runtime main flow uses RuntimeFinalizer.
        """
        preferred = RuntimeFinalizer.select_final_assistant_text(messages)
        if preferred is not None:
            return preferred

        fallback_texts: list[str] = []
        for message in messages:
            role = getattr(message, "role", None)
            role_value = getattr(role, "value", role)
            content = getattr(message, "content", None)
            if role_value != "assistant" or not isinstance(content, str):
                continue
            text = content.strip()
            if text:
                fallback_texts.append(text)
        if fallback_texts:
            return fallback_texts[-1]
        return None

    @staticmethod
    def _clear_current_task_cancellation() -> None:
        task = asyncio.current_task()
        if task is None or not hasattr(task, "uncancel"):
            return
        while task.cancelling():
            task.uncancel()

    @staticmethod
    def _env_int(name: str, default: int, minimum: int | None = None) -> int:
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

    def _resolve_time_budget_seconds(self) -> int:
        # <=0 disables time budget.
        return self._env_int("BFF_RUNTIME_TIME_BUDGET_SECONDS", 180)

    def _estimate_dynamic_steps(self, prompt: str, base_steps: int) -> int:
        """
        Estimate step budget from request complexity.
        Keep heuristics transparent and bounded so behavior remains predictable.
        """
        text = self._extract_current_user_request(prompt).strip()
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

        dynamic_cap = self._env_int("BFF_RUNTIME_DYNAMIC_STEPS_MAX", 20, minimum=6)
        dynamic_floor = self._env_int("BFF_RUNTIME_DYNAMIC_STEPS_MIN", 4, minimum=1)
        return max(dynamic_floor, min(dynamic_cap, base_steps + score))

    def _resolve_max_steps(self, prompt: str, max_steps: int | None) -> int:
        # Explicit API argument takes highest priority.
        if max_steps is not None:
            return max(1, max_steps)

        base_steps = self._env_int("BFF_MANUS_MAX_STEPS", 10, minimum=1)
        dynamic_enabled = self._is_truthy_env(
            os.getenv("BFF_RUNTIME_DYNAMIC_STEPS_ENABLED", "1")
        )
        if not dynamic_enabled:
            return base_steps
        return self._estimate_dynamic_steps(prompt, base_steps)

    async def _emit_progress(
        self,
        callback: Callable[[dict[str, Any]], Any] | None,
        payload: dict[str, Any],
    ) -> None:
        await self._progress.emit(callback, payload)

    def _build_runtime_event_callback(
        self,
        *,
        agent: Manus,
        user_callback: Callable[[dict[str, Any]], Any] | None,
        stall_detector: _RuntimeStallDetector | None,
    ) -> Callable[[dict[str, Any]], Any]:
        async def _wrapped(event: dict[str, Any]) -> None:
            if stall_detector is not None:
                stall_reason = stall_detector.observe(event)
                if stall_reason and agent.state != AgentState.FINISHED:
                    logger.warning(f"Runtime stall detector triggered: {stall_reason}")
                    agent.state = AgentState.FINISHED
                    await self._emit_progress(
                        user_callback,
                        {
                            "type": "progress",
                            "phase": "terminated",
                            "reason": "stall_detected",
                            "message": "检测到重复无效工具调用，已提前结束执行。",
                        },
                    )

            if user_callback is None:
                return
            maybe_awaitable = user_callback(event)
            if asyncio.iscoroutine(maybe_awaitable):
                await maybe_awaitable

        return _wrapped

    # Backward-compatible shim for existing tests/callers.
    def _should_connect_server(self, prompt: str, server: Any) -> bool:
        return self._mcp_router._should_connect_server(prompt, server)

    @staticmethod
    def _reset_agent_for_new_request(agent: Manus, *, max_steps: int) -> None:
        """
        Reusing one agent instance requires explicit state reset between requests
        to avoid step counters and message history leaking across turns.
        """
        agent.max_steps = max_steps
        agent.current_step = 0
        agent.memory = Memory()
        agent.tool_calls = []
        # Event callback may differ between stream and non-stream calls.
        agent.event_callback = None

    @staticmethod
    def _log_final_answer(final_text: str) -> None:
        text = (final_text or "").strip()
        if not text:
            logger.info("🍃 Manus final answer: <empty>")
            return
        logger.info(f"🍃 Manus final answer:\n{text}")

    async def ask(
        self,
        prompt: str,
        *,
        max_steps: int | None = None,
        progress_callback: Callable[[dict[str, Any]], Any] | None = None,
    ) -> str:
        steps = self._resolve_max_steps(prompt, max_steps)
        time_budget_seconds = self._resolve_time_budget_seconds()
        stall_enabled = self._is_truthy_env(os.getenv("BFF_RUNTIME_STALL_DETECTOR_ENABLED", "1"))
        stall_same_tool_limit = self._env_int(
            "BFF_RUNTIME_STALL_SAME_TOOL_LIMIT", 3, minimum=2
        )
        stall_error_limit = self._env_int(
            "BFF_RUNTIME_STALL_TOOL_ERROR_LIMIT", 3, minimum=2
        )
        reuse_agent = self._is_truthy_env(os.getenv("BFF_RUNTIME_REUSE_AGENT", "0"))
        await self._emit_progress(
            progress_callback,
            {
                "type": "progress",
                "phase": "runtime_start",
                "maxSteps": steps,
                "timeBudgetSeconds": time_budget_seconds,
                "message": f"运行中，最多 {steps} 步，时间预算 {time_budget_seconds} 秒",
            },
        )
        await self._emit_progress(
            progress_callback,
            {
                "type": "progress",
                "phase": "plan",
                "maxSteps": steps,
                "message": "PLAN 阶段：解析目标与约束",
            },
        )

        # BFF runtime connects MCP servers on-demand based on current request.
        # Skip Manus config-time auto-connect to avoid unnecessary MCP sessions per message.
        if reuse_agent:
            if self._shared_agent is None:
                self._shared_agent = await Manus.create(
                    max_steps=steps,
                    event_callback=None,
                    initialize_mcp=False,
                )
                self._shared_agent.cleanup_on_run_finish = False
            agent = self._shared_agent
            self._reset_agent_for_new_request(agent, max_steps=steps)
        else:
            agent = await Manus.create(
                max_steps=steps,
                event_callback=None,
                initialize_mcp=False,
            )
        stall_detector = (
            _RuntimeStallDetector(
                same_tool_limit=stall_same_tool_limit,
                tool_error_limit=stall_error_limit,
            )
            if stall_enabled
            else None
        )
        agent.event_callback = self._build_runtime_event_callback(
            agent=agent,
            user_callback=progress_callback,
            stall_detector=stall_detector,
        )

        run_invoked = False
        try:
            # Web API context has no interactive stdin. Remove ask_human to avoid EOF hangs.
            filtered_tools = tuple(
                tool
                for tool in agent.available_tools.tools
                if getattr(tool, "name", "") != "ask_human"
            )
            agent.available_tools = ToolCollection(*filtered_tools)
            await self._emit_progress(
                progress_callback,
                {
                    "type": "progress",
                    "phase": "act",
                    "maxSteps": steps,
                    "message": "ACT 阶段：执行工具操作",
                },
            )

            if reuse_agent:
                async with self._shared_agent_lock:
                    await self._mcp_router.connect_enabled_mcp_servers(agent, prompt)
                    run_prompt = self._mcp_router.augment_prompt_with_mcp_catalog(prompt)
                    run_invoked = True
                    if time_budget_seconds > 0:
                        raw = await asyncio.wait_for(
                            agent.run(run_prompt), timeout=time_budget_seconds
                        )
                    else:
                        raw = await agent.run(run_prompt)
            else:
                await self._mcp_router.connect_enabled_mcp_servers(agent, prompt)
                run_prompt = self._mcp_router.augment_prompt_with_mcp_catalog(prompt)
                run_invoked = True
                if time_budget_seconds > 0:
                    raw = await asyncio.wait_for(
                        agent.run(run_prompt), timeout=time_budget_seconds
                    )
                else:
                    raw = await agent.run(run_prompt)

            await self._emit_progress(
                progress_callback,
                {
                    "type": "progress",
                    "phase": "verify",
                    "maxSteps": steps,
                    "message": "VERIFY 阶段：检查执行结果",
                },
            )
            await self._emit_progress(
                progress_callback,
                {
                    "type": "progress",
                    "phase": "finalize",
                    "maxSteps": steps,
                    "message": "FINALIZE 阶段：整理最终答复",
                },
            )
            candidate_final = self._finalizer.select_final_assistant_text(agent.messages)
            final_text = await self._finalizer.finalize_response(
                agent=agent,
                user_prompt=prompt,
                run_result=raw,
                candidate_final_text=candidate_final,
            )
            self._log_final_answer(final_text)

            await self._emit_progress(
                progress_callback,
                {
                    "type": "progress",
                    "phase": "runtime_done",
                    "message": "执行完成，最终答复已生成",
                },
            )
            return final_text
        except asyncio.TimeoutError:
            logger.warning(
                "Runtime timed out by time budget "
                f"({time_budget_seconds}s); finalizing with current messages."
            )
            agent.state = AgentState.FINISHED
            await self._emit_progress(
                progress_callback,
                {
                    "type": "progress",
                    "phase": "terminated",
                    "reason": "time_budget_exceeded",
                    "timeBudgetSeconds": time_budget_seconds,
                    "message": f"超出时间预算 {time_budget_seconds} 秒，已提前结束执行。",
                },
            )
            candidate_final = self._finalizer.select_final_assistant_text(agent.messages)
            final_text = await self._finalizer.finalize_response(
                agent=agent,
                user_prompt=prompt,
                run_result=f"Terminated: time budget exceeded ({time_budget_seconds}s)",
                candidate_final_text=candidate_final,
            )
            self._log_final_answer(final_text)
            return final_text
        finally:
            # agent.run() already performs cleanup in ToolCallAgent.run().
            # Only perform explicit cleanup when run() was never reached.
            if not run_invoked:
                try:
                    await agent.cleanup()
                except asyncio.CancelledError as exc:
                    logger.warning(f"Agent cleanup cancelled and ignored: {exc}")
                    self._clear_current_task_cancellation()
                except Exception as exc:
                    logger.warning(f"Agent cleanup failed and was ignored: {exc}")
