from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from typing import Any, Callable

from app.agent.manus import Manus
from app.logger import logger
from app.tool.tool_collection import ToolCollection
from bff.services.runtime.runtime_mcp_router import RuntimeMcpRouter
from bff.services.runtime.runtime_policy import RuntimePolicy


@dataclass
class RuntimeExecutionResult:
    raw: str
    connected_servers: list[str] | None


class RuntimeExecutor:
    def __init__(
        self,
        *,
        mcp_router: RuntimeMcpRouter,
        policy: RuntimePolicy,
    ):
        self._mcp_router = mcp_router
        self._policy = policy

    @staticmethod
    def prepare_non_interactive_tools(agent: Manus) -> None:
        filtered_tools = tuple(
            tool
            for tool in agent.available_tools.tools
            if getattr(tool, "name", "") != "ask_human"
        )
        agent.available_tools = ToolCollection(*filtered_tools)

    async def execute_turn(
        self,
        *,
        agent: Manus,
        prompt: str,
        steps: int,
        time_budget_seconds: int,
        reuse_agent: bool,
        shared_agent_lock: asyncio.Lock,
        progress_callback: Callable[[dict[str, Any]], Any] | None,
        emit_progress: Callable[[Callable[[dict[str, Any]], Any] | None, dict[str, Any]], Any],
        run_state: dict[str, bool],
    ) -> RuntimeExecutionResult:
        await emit_progress(
            progress_callback,
            {
                "type": "progress",
                "phase": "act",
                "maxSteps": steps,
                "message": "ACT 阶段：执行工具操作",
            },
        )

        connected_servers, raw = await self._run_main_round(
            agent=agent,
            prompt=prompt,
            time_budget_seconds=time_budget_seconds,
            reuse_agent=reuse_agent,
            shared_agent_lock=shared_agent_lock,
            run_state=run_state,
        )

        rag_retry_enabled = self._policy.is_truthy_env(
            os.getenv("BFF_RUNTIME_RAG_RETRY_ON_MISS", "1")
        )
        rag_expected = self._mcp_router.should_force_rag_for_prompt(prompt)
        rag_used = self._policy.has_rag_tool_activity(agent.messages)
        logger.info(
            "RAG execution check: "
            f"connected={bool('rag' in (connected_servers or []))}, "
            f"expected={rag_expected}, used={rag_used}, "
            f"retry_enabled={rag_retry_enabled}"
        )
        if rag_retry_enabled and rag_expected and not rag_used:
            logger.warning(
                "RAG expected but no rag tool call detected; triggering one retry with forced RAG instruction."
            )
            await emit_progress(
                progress_callback,
                {
                    "type": "progress",
                    "phase": "act_retry",
                    "maxSteps": steps,
                    "message": "检测到知识问答未触发 RAG，正在执行一次强制检索重试",
                },
            )
            raw = await self._run_rag_retry(
                agent=agent,
                prompt=prompt,
                connected_servers=connected_servers,
                time_budget_seconds=time_budget_seconds,
                reuse_agent=reuse_agent,
                shared_agent_lock=shared_agent_lock,
                run_state=run_state,
            )

        trendradar_retry_enabled = self._policy.is_truthy_env(
            os.getenv("BFF_RUNTIME_TRENDRADAR_RETRY_ON_MISS", "1")
        )
        trendradar_expected = self._mcp_router.should_force_trendradar_for_prompt(prompt)
        trendradar_used = self._policy.has_server_tool_activity(
            agent.messages, "trendradar"
        )
        logger.info(
            "TrendRadar execution check: "
            f"connected={bool('trendradar' in (connected_servers or []))}, "
            f"expected={trendradar_expected}, used={trendradar_used}, "
            f"retry_enabled={trendradar_retry_enabled}"
        )
        if trendradar_retry_enabled and trendradar_expected and not trendradar_used:
            logger.warning(
                "TrendRadar expected but no trendradar tool call detected; triggering one retry with forced TrendRadar instruction."
            )
            await emit_progress(
                progress_callback,
                {
                    "type": "progress",
                    "phase": "act_retry",
                    "maxSteps": steps,
                    "message": "检测到新闻请求未触发 TrendRadar，正在执行一次强制重试",
                },
            )
            raw = await self._run_trendradar_retry(
                agent=agent,
                prompt=prompt,
                connected_servers=connected_servers,
                time_budget_seconds=time_budget_seconds,
                reuse_agent=reuse_agent,
                shared_agent_lock=shared_agent_lock,
                run_state=run_state,
            )

        return RuntimeExecutionResult(raw=raw, connected_servers=connected_servers)

    async def _run_main_round(
        self,
        *,
        agent: Manus,
        prompt: str,
        time_budget_seconds: int,
        reuse_agent: bool,
        shared_agent_lock: asyncio.Lock,
        run_state: dict[str, bool],
    ) -> tuple[list[str] | None, str]:
        async def _inner() -> tuple[list[str] | None, str]:
            connected_servers = await self._mcp_router.connect_enabled_mcp_servers(
                agent, prompt
            )
            run_prompt = self._mcp_router.augment_prompt_with_mcp_catalog(prompt)
            raw = await self._run_agent(
                agent=agent,
                run_prompt=run_prompt,
                time_budget_seconds=time_budget_seconds,
                run_state=run_state,
            )
            return connected_servers, raw

        return await self._run_with_optional_lock(
            reuse_agent=reuse_agent,
            shared_agent_lock=shared_agent_lock,
            action=_inner,
        )

    async def _run_rag_retry(
        self,
        *,
        agent: Manus,
        prompt: str,
        connected_servers: list[str] | None,
        time_budget_seconds: int,
        reuse_agent: bool,
        shared_agent_lock: asyncio.Lock,
        run_state: dict[str, bool],
    ) -> str:
        retry_prompt = self._policy.build_forced_rag_retry_prompt(prompt)
        retry_run_prompt = self._mcp_router.augment_prompt_with_mcp_catalog(retry_prompt)

        async def _inner() -> str:
            if "rag" not in (connected_servers or []):
                connected = await self._mcp_router.connect_server_by_id(agent, "rag")
                logger.info(f"Forced rag connection during retry: connected={connected}")
            return await self._run_agent(
                agent=agent,
                run_prompt=retry_run_prompt,
                time_budget_seconds=time_budget_seconds,
                run_state=run_state,
            )

        return await self._run_with_optional_lock(
            reuse_agent=reuse_agent,
            shared_agent_lock=shared_agent_lock,
            action=_inner,
        )

    async def _run_trendradar_retry(
        self,
        *,
        agent: Manus,
        prompt: str,
        connected_servers: list[str] | None,
        time_budget_seconds: int,
        reuse_agent: bool,
        shared_agent_lock: asyncio.Lock,
        run_state: dict[str, bool],
    ) -> str:
        retry_prompt = self._policy.build_forced_trendradar_retry_prompt(prompt)
        retry_run_prompt = self._mcp_router.augment_prompt_with_mcp_catalog(retry_prompt)

        async def _inner() -> str:
            if "trendradar" not in (connected_servers or []):
                connected = await self._mcp_router.connect_server_by_id(
                    agent, "trendradar"
                )
                logger.info(
                    f"Forced trendradar connection during retry: connected={connected}"
                )
            return await self._run_agent(
                agent=agent,
                run_prompt=retry_run_prompt,
                time_budget_seconds=time_budget_seconds,
                run_state=run_state,
            )

        return await self._run_with_optional_lock(
            reuse_agent=reuse_agent,
            shared_agent_lock=shared_agent_lock,
            action=_inner,
        )

    @staticmethod
    async def _run_with_optional_lock(
        *,
        reuse_agent: bool,
        shared_agent_lock: asyncio.Lock,
        action: Callable[[], Any],
    ) -> Any:
        if reuse_agent:
            async with shared_agent_lock:
                return await action()
        return await action()

    @staticmethod
    async def _run_agent(
        *,
        agent: Manus,
        run_prompt: str,
        time_budget_seconds: int,
        run_state: dict[str, bool],
    ) -> str:
        run_state["run_invoked"] = True
        if time_budget_seconds > 0:
            return await asyncio.wait_for(agent.run(run_prompt), timeout=time_budget_seconds)
        return await agent.run(run_prompt)
