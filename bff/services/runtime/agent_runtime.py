from __future__ import annotations

import asyncio
import os
from typing import Any, Callable, Protocol

from app.agent.manus import Manus
from app.logger import logger
from app.schema import AgentState
from app.schema import Memory
from bff.repositories.store import InMemoryStore
from bff.services.runtime.runtime_events import RuntimeEventManager
from bff.services.runtime.runtime_executor import RuntimeExecutor
from bff.services.runtime.runtime_finalizer import RuntimeFinalizer
from bff.services.runtime.runtime_mcp_router import RuntimeMcpRouter
from bff.services.runtime.runtime_planning import RuntimeMcpPlanningOrchestrator
from bff.services.runtime.runtime_policy import RuntimePolicy, RuntimeStallDetector
from bff.services.runtime.runtime_progress import RuntimeProgressEmitter

# Backward-compatible export kept for existing tests/importers.
_RuntimeStallDetector = RuntimeStallDetector


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

        self._mcp_router = RuntimeMcpRouter(store)
        self._planning = RuntimeMcpPlanningOrchestrator(
            router=self._mcp_router,
            store=store,
        )
        self._policy = RuntimePolicy()
        self._events = RuntimeEventManager(RuntimeProgressEmitter())
        self._executor = RuntimeExecutor(
            mcp_router=self._mcp_router,
            policy=self._policy,
            planning=self._planning,
        )
        self._finalizer = RuntimeFinalizer(self._extract_current_user_request)

    @staticmethod
    def _is_truthy_env(value: str | None) -> bool:
        return RuntimePolicy.is_truthy_env(value)

    @staticmethod
    def _extract_current_user_request(prompt: str) -> str:
        """
        Runtime prompts may include history wrappers. Keep MCP routing focused on
        the current user request to avoid accidental server fan-out.
        """
        return RuntimePolicy.extract_current_user_request(prompt)

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
        return RuntimePolicy.env_int(name, default, minimum)

    def _resolve_time_budget_seconds(self) -> int:
        return self._policy.resolve_time_budget_seconds()

    def _estimate_dynamic_steps(self, prompt: str, base_steps: int) -> int:
        return self._policy.estimate_dynamic_steps(prompt, base_steps)

    def _resolve_max_steps(self, prompt: str, max_steps: int | None) -> int:
        return self._policy.resolve_max_steps(prompt, max_steps)

    async def _emit_progress(
        self,
        callback: Callable[[dict[str, Any]], Any] | None,
        payload: dict[str, Any],
    ) -> None:
        await self._events.emit_progress(callback, payload)

    def _build_runtime_event_callback(
        self,
        *,
        agent: Manus,
        user_callback: Callable[[dict[str, Any]], Any] | None,
        stall_detector: RuntimeStallDetector | None,
    ) -> Callable[[dict[str, Any]], Any]:
        return self._events.build_runtime_event_callback(
            agent=agent,
            user_callback=user_callback,
            stall_detector=stall_detector,
        )

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
        agent.event_callback = None

    @staticmethod
    def _log_final_answer(final_text: str) -> None:
        text = (final_text or "").strip()
        if not text:
            logger.info("🍃 Manus final answer: <empty>")
            return
        logger.info(f"🍃 Manus final answer:\n{text}")

    @staticmethod
    def _has_rag_tool_activity(messages: list[Any]) -> bool:
        return RuntimePolicy.has_rag_tool_activity(messages)

    @staticmethod
    def _build_forced_rag_retry_prompt(prompt: str) -> str:
        return RuntimePolicy.build_forced_rag_retry_prompt(prompt)

    async def _create_agent_for_request(self, *, steps: int, reuse_agent: bool) -> Manus:
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
            return agent

        return await Manus.create(
            max_steps=steps,
            event_callback=None,
            initialize_mcp=False,
        )

    async def ask(
        self,
        prompt: str,
        *,
        max_steps: int | None = None,
        progress_callback: Callable[[dict[str, Any]], Any] | None = None,
    ) -> str:
        steps = self._resolve_max_steps(prompt, max_steps)
        time_budget_seconds = self._resolve_time_budget_seconds()
        stall_enabled = self._is_truthy_env(
            os.getenv("BFF_RUNTIME_STALL_DETECTOR_ENABLED", "1")
        )
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

        agent = await self._create_agent_for_request(steps=steps, reuse_agent=reuse_agent)
        stall_detector = (
            RuntimeStallDetector(
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

        run_state = {"run_invoked": False}
        pre_run_message_count = len(agent.messages)
        try:
            RuntimeExecutor.prepare_non_interactive_tools(agent)
            execution = await self._executor.execute_turn(
                agent=agent,
                prompt=prompt,
                steps=steps,
                time_budget_seconds=time_budget_seconds,
                reuse_agent=reuse_agent,
                shared_agent_lock=self._shared_agent_lock,
                progress_callback=progress_callback,
                emit_progress=self._emit_progress,
                run_state=run_state,
            )
            raw = execution.raw

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

            turn_messages = list(agent.messages[pre_run_message_count:])
            candidate_final = self._finalizer.select_final_assistant_text(turn_messages)
            final_text = await self._finalizer.finalize_response(
                agent=agent,
                user_prompt=prompt,
                run_result=raw,
                candidate_final_text=candidate_final,
                messages=turn_messages,
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
            turn_messages = list(agent.messages[pre_run_message_count:])
            candidate_final = self._finalizer.select_final_assistant_text(turn_messages)
            final_text = await self._finalizer.finalize_response(
                agent=agent,
                user_prompt=prompt,
                run_result=f"Terminated: time budget exceeded ({time_budget_seconds}s)",
                candidate_final_text=candidate_final,
                messages=turn_messages,
            )
            self._log_final_answer(final_text)
            return final_text
        finally:
            if not run_state["run_invoked"]:
                try:
                    await agent.cleanup()
                except asyncio.CancelledError as exc:
                    logger.warning(f"Agent cleanup cancelled and ignored: {exc}")
                    self._clear_current_task_cancellation()
                except Exception as exc:
                    logger.warning(f"Agent cleanup failed and was ignored: {exc}")
