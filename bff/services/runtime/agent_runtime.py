from __future__ import annotations

import asyncio
import os
from typing import Any, Callable, Protocol

from app.agent.manus import Manus
from app.logger import logger
from app.schema import Memory
from app.tool.tool_collection import ToolCollection
from bff.repositories.store import InMemoryStore
from bff.services.runtime.runtime_finalizer import RuntimeFinalizer
from bff.services.runtime.runtime_mcp_router import RuntimeMcpRouter
from bff.services.runtime.runtime_progress import RuntimeProgressEmitter


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

    async def ask(
        self,
        prompt: str,
        *,
        max_steps: int | None = None,
        progress_callback: Callable[[dict[str, Any]], Any] | None = None,
    ) -> str:
        steps = max_steps or int(os.getenv("BFF_MANUS_MAX_STEPS", "6"))
        reuse_agent = self._is_truthy_env(os.getenv("BFF_RUNTIME_REUSE_AGENT", "0"))
        await self._progress.emit(
            progress_callback,
            {
                "type": "progress",
                "phase": "runtime_start",
                "maxSteps": steps,
                "message": f"运行中，最多 {steps} 步",
            },
        )
        await self._progress.emit(
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
                    event_callback=progress_callback,
                    initialize_mcp=False,
                )
                self._shared_agent.cleanup_on_run_finish = False
            agent = self._shared_agent
            self._reset_agent_for_new_request(agent, max_steps=steps)
            agent.event_callback = progress_callback
        else:
            agent = await Manus.create(
                max_steps=steps,
                event_callback=progress_callback,
                initialize_mcp=False,
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
            await self._progress.emit(
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
                    raw = await agent.run(run_prompt)
            else:
                await self._mcp_router.connect_enabled_mcp_servers(agent, prompt)
                run_prompt = self._mcp_router.augment_prompt_with_mcp_catalog(prompt)
                run_invoked = True
                raw = await agent.run(run_prompt)

            await self._progress.emit(
                progress_callback,
                {
                    "type": "progress",
                    "phase": "verify",
                    "maxSteps": steps,
                    "message": "VERIFY 阶段：检查执行结果",
                },
            )
            await self._progress.emit(
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

            await self._progress.emit(
                progress_callback,
                {
                    "type": "progress",
                    "phase": "runtime_done",
                    "message": "执行完成，最终答复已生成",
                },
            )
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
