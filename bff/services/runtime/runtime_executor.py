from __future__ import annotations

import asyncio
import hashlib
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.agent.manus import Manus
from app.logger import logger
from app.tool.tool_collection import ToolCollection
from bff.services.runtime.mcp_routing.runtime_mcp_router import RuntimeMcpRouter
from bff.services.runtime.mcp_routing.runtime_logviz import render_ascii_box
from bff.services.runtime.mcp_routing.runtime_plan_models import PlannerStep
from bff.services.runtime.mcp_routing.runtime_planning import RuntimeMcpPlanningOrchestrator
from bff.services.runtime.runtime_policy import RuntimePolicy
from bff.utils.env import get_env


@dataclass
class RuntimeExecutionResult:
    raw: str
    connected_servers: list[str] | None
    expected_steps: list[tuple[str, str]] = field(default_factory=list)


class RuntimeExecutor:
    def __init__(
        self,
        *,
        mcp_router: RuntimeMcpRouter,
        policy: RuntimePolicy,
        planning: RuntimeMcpPlanningOrchestrator | None = None,
    ):
        self._mcp_router = mcp_router
        self._policy = policy
        self._planning = planning

    @staticmethod
    def prepare_non_interactive_tools(agent: Manus) -> None:
        filtered_tools = tuple(
            tool
            for tool in agent.available_tools.tools
            if getattr(tool, "name", "") != "ask_human"
        )
        agent.available_tools = ToolCollection(*filtered_tools)

    @staticmethod
    def strip_mcp_tools(agent: Manus) -> None:
        """
        Hard-disable any MCP tools for this turn.
        This enforces routing decisions where need_mcp=false so the model cannot
        accidentally call already-available `mcp_*` tools from a reused agent.
        """
        from app.tool.mcp import MCPClientTool

        filtered_tools = tuple(
            tool for tool in agent.available_tools.tools if not isinstance(tool, MCPClientTool)
        )
        agent.available_tools = ToolCollection(*filtered_tools)

    @staticmethod
    def _scope_tools_to_server(agent: Manus, server_id: str) -> ToolCollection:
        """
        Keep only tools from the target MCP server (+ terminate) for the current plan step.
        This prevents the model from drifting to unrelated local tools (e.g. python/editor).
        """
        from app.tool.mcp import MCPClientTool

        sid = (server_id or "").strip().lower()
        keep_non_mcp = {"terminate"}
        filtered = []
        for tool in agent.available_tools.tools:
            name = str(getattr(tool, "name", "") or "").strip().lower()
            if isinstance(tool, MCPClientTool):
                if getattr(tool, "server_id", "").strip().lower() == sid:
                    filtered.append(tool)
                continue
            if name in keep_non_mcp:
                filtered.append(tool)
        return ToolCollection(*tuple(filtered))

    async def execute_turn(
        self,
        *,
        agent: Manus,
        prompt: str,
        session_id: str | None = None,
        steps: int,
        time_budget_seconds: int,
        reuse_agent: bool,
        shared_agent_lock: asyncio.Lock,
        progress_callback: Callable[[dict[str, Any]], Any] | None,
        emit_progress: Callable[[Callable[[dict[str, Any]], Any] | None, dict[str, Any]], Any],
        run_state: dict[str, bool],
    ) -> RuntimeExecutionResult:
        turn_started = time.perf_counter()
        timing_ms: dict[str, int] = {
            "connect_ms": 0,
            "agent_run_ms": 0,
            "agent_runs": 0,
            "planning_total_ms": 0,
            "planning_retrieval_ms": 0,
            "planning_planner_ms": 0,
            "planning_gatekeeper_ms": 0,
        }
        await emit_progress(
            progress_callback,
            {
                "type": "progress",
                "phase": "act",
                "maxSteps": steps,
                "message": "ACT 阶段：执行工具操作",
            },
        )

        execution = await self._run_main_round(
            agent=agent,
            prompt=prompt,
            session_id=session_id,
            time_budget_seconds=time_budget_seconds,
            reuse_agent=reuse_agent,
            shared_agent_lock=shared_agent_lock,
            run_state=run_state,
            timing_ms=timing_ms,
        )

        raw = await self._enforce_expected_steps(
            agent=agent,
            prompt=prompt,
            initial_raw=execution.raw,
            connected_servers=execution.connected_servers or [],
            expected_steps=execution.expected_steps,
            steps=steps,
            time_budget_seconds=time_budget_seconds,
            reuse_agent=reuse_agent,
            shared_agent_lock=shared_agent_lock,
            progress_callback=progress_callback,
            emit_progress=emit_progress,
            run_state=run_state,
            timing_ms=timing_ms,
        )

        elapsed_ms = int((time.perf_counter() - turn_started) * 1000)
        self._mcp_router.record_routing_outcome(
            prompt=prompt,
            session_id=session_id,
            connected_server_ids=execution.connected_servers,
            messages=agent.messages,
            latency_ms=elapsed_ms,
            success=bool((raw or "").strip()),
        )

        # Important: when reusing an agent across requests, disconnect MCP sessions
        # at the end of the turn. Some MCP transports (notably streamablehttp/anyio)
        # require enter/exit to happen in the same asyncio task; persisting sessions
        # across requests can lead to cancel-scope errors during cleanup.
        if reuse_agent:
            try:
                await agent.disconnect_mcp_server()
            except Exception as exc:
                logger.warning(f"Failed to disconnect MCP servers after turn, ignored: {exc}")

        if self._policy.is_truthy_env(os.getenv("BFF_RUNTIME_TIMING_LOG_ENABLED", "1")):
            total_ms = int((time.perf_counter() - turn_started) * 1000)
            lines = [
                f"turn.total_ms: {total_ms}",
                (
                    "planning_ms: "
                    f"total={timing_ms.get('planning_total_ms', 0)}, "
                    f"retrieval={timing_ms.get('planning_retrieval_ms', 0)}, "
                    f"planner={timing_ms.get('planning_planner_ms', 0)}, "
                    f"gatekeeper={timing_ms.get('planning_gatekeeper_ms', 0)}"
                ),
                f"connect_ms: {timing_ms.get('connect_ms', 0)}",
                f"agent_run_ms: {timing_ms.get('agent_run_ms', 0)} (runs={timing_ms.get('agent_runs', 0)})",
            ]
            logger.info("\n" + render_ascii_box("RUNTIME TIMING (ACT)", lines))

        return RuntimeExecutionResult(
            raw=raw,
            connected_servers=execution.connected_servers,
            expected_steps=execution.expected_steps,
        )

    async def _run_main_round(
        self,
        *,
        agent: Manus,
        prompt: str,
        session_id: str | None,
        time_budget_seconds: int,
        reuse_agent: bool,
        shared_agent_lock: asyncio.Lock,
        run_state: dict[str, bool],
        timing_ms: dict[str, int],
    ) -> RuntimeExecutionResult:
        async def _inner() -> RuntimeExecutionResult:
            if not self._planning:
                connect_started = time.perf_counter()
                connected_servers = await self._mcp_router.connect_enabled_mcp_servers(
                    agent,
                    prompt,
                    session_id=session_id,
                )
                timing_ms["connect_ms"] += int((time.perf_counter() - connect_started) * 1000)
                run_prompt = self._mcp_router.augment_prompt_with_mcp_catalog(prompt)
                agent_started = time.perf_counter()
                raw = await self._run_agent(
                    agent=agent,
                    run_prompt=run_prompt,
                    time_budget_seconds=time_budget_seconds,
                    run_state=run_state,
                )
                timing_ms["agent_run_ms"] += int((time.perf_counter() - agent_started) * 1000)
                timing_ms["agent_runs"] += 1
                expected_steps = [(server_id, "auto") for server_id in (connected_servers or [])]
                return RuntimeExecutionResult(
                    raw=raw,
                    connected_servers=connected_servers,
                    expected_steps=expected_steps,
                )

            decision = await self._planning.decide(prompt, session_id=session_id)
            for key in (
                "planning_total_ms",
                "planning_retrieval_ms",
                "planning_planner_ms",
                "planning_gatekeeper_ms",
            ):
                if key in decision.timing_ms:
                    timing_ms[key] = int(decision.timing_ms.get(key) or 0)
            plan = decision.execute_plan
            multi_step_raw = get_env("BFF_RUNTIME_MULTI_STEP_EXECUTION", "0")
            multi_step_enabled = self._policy.is_truthy_env(multi_step_raw)
            logger.info(
                "MCP execution decision: "
                f"source={decision.execute_source}, "
                f"shadow_only={decision.shadow_only}, "
                f"gate_error={decision.gate_error_code}, "
                f"need_mcp={plan.need_mcp}, "
                f"multi_step_enabled={multi_step_enabled}({multi_step_raw}), "
                f"steps={[{'server': s.server_id, 'tool': s.tool_name} for s in plan.plan_steps]}"
            )

            if not plan.need_mcp:
                # Enforce strict "no MCP" execution: ensure reused agents cannot
                # call already-available MCP tools.
                self.strip_mcp_tools(agent)
                try:
                    await agent.disconnect_mcp_server()
                except Exception as exc:
                    logger.warning(f"Failed to disconnect MCP servers for no_mcp turn, ignored: {exc}")
                no_mcp_prompt = self._policy.build_no_mcp_execution_prompt(
                    prompt,
                    reason=(plan.fallback.reason if plan.fallback else ""),
                )
                run_prompt = self._mcp_router.augment_prompt_with_mcp_catalog(no_mcp_prompt)
                agent_started = time.perf_counter()
                raw = await self._run_agent(
                    agent=agent,
                    run_prompt=run_prompt,
                    time_budget_seconds=time_budget_seconds,
                    run_state=run_state,
                )
                timing_ms["agent_run_ms"] += int((time.perf_counter() - agent_started) * 1000)
                timing_ms["agent_runs"] += 1
                return RuntimeExecutionResult(raw=raw, connected_servers=[])

            if multi_step_enabled and len(plan.plan_steps) > 1:
                logger.info(
                    "MCP execution: running multi-step plan with "
                    f"{len(plan.plan_steps)} steps"
                )
                return await self._run_multi_step_plan(
                    agent=agent,
                    prompt=prompt,
                    session_id=session_id,
                    plan_steps=list(plan.plan_steps),
                    fallback_server_id=plan.fallback.server_id,
                    fallback_tool_name=plan.fallback.tool_name,
                    time_budget_seconds=time_budget_seconds,
                    run_state=run_state,
                    timing_ms=timing_ms,
                )

            step = plan.plan_steps[0]
            logger.info(
                "MCP execution: running single-step plan "
                f"server={step.server_id}, tool={step.tool_name}"
            )
            connected_servers = await self._connect_servers_by_ids(
                agent=agent,
                server_ids=[step.server_id],
                connected_servers=[],
                timing_ms=timing_ms,
            )
            step_prompt = self._policy.build_plan_step_prompt(
                prompt,
                server_id=step.server_id,
                tool_name=step.tool_name,
                goal=step.goal,
                reason=step.reason,
                step_index=1,
                total_steps=1,
                retry=False,
            )
            run_prompt = self._mcp_router.augment_prompt_with_mcp_catalog(step_prompt)
            original_tools = agent.available_tools
            agent.available_tools = self._scope_tools_to_server(agent, step.server_id)
            try:
                agent_started = time.perf_counter()
                raw = await self._run_agent(
                    agent=agent,
                    run_prompt=run_prompt,
                    time_budget_seconds=time_budget_seconds,
                    run_state=run_state,
                )
            finally:
                agent.available_tools = original_tools
            timing_ms["agent_run_ms"] += int((time.perf_counter() - agent_started) * 1000)
            timing_ms["agent_runs"] += 1
            return RuntimeExecutionResult(
                raw=raw,
                connected_servers=connected_servers,
                expected_steps=[(step.server_id, step.tool_name)],
            )

        return await self._run_with_optional_lock(
            reuse_agent=reuse_agent,
            shared_agent_lock=shared_agent_lock,
            action=_inner,
        )

    async def _run_multi_step_plan(
        self,
        *,
        agent: Manus,
        prompt: str,
        session_id: str | None,
        plan_steps: list[PlannerStep],
        fallback_server_id: str | None,
        fallback_tool_name: str | None,
        time_budget_seconds: int,
        run_state: dict[str, bool],
        timing_ms: dict[str, int],
    ) -> RuntimeExecutionResult:
        connected_servers: list[str] = []
        expected_steps: list[tuple[str, str]] = []
        raw = ""
        total_steps = len(plan_steps)

        for index, step in enumerate(plan_steps, start=1):
            logger.info(
                "MCP step start: "
                f"{index}/{total_steps}, server={step.server_id}, tool={step.tool_name}, goal={step.goal}"
            )
            connected_servers = await self._connect_servers_by_ids(
                agent=agent,
                server_ids=[step.server_id],
                connected_servers=connected_servers,
                timing_ms=timing_ms,
            )

            before_count = len(agent.messages)
            step_prompt = self._policy.build_plan_step_prompt(
                prompt,
                server_id=step.server_id,
                tool_name=step.tool_name,
                goal=step.goal,
                reason=step.reason,
                step_index=index,
                total_steps=total_steps,
                retry=False,
            )
            run_prompt = self._mcp_router.augment_prompt_with_mcp_catalog(step_prompt)
            original_tools = agent.available_tools
            agent.available_tools = self._scope_tools_to_server(agent, step.server_id)
            try:
                agent_started = time.perf_counter()
                raw = await self._run_agent(
                    agent=agent,
                    run_prompt=run_prompt,
                    time_budget_seconds=time_budget_seconds,
                    run_state=run_state,
                )
            finally:
                agent.available_tools = original_tools
            timing_ms["agent_run_ms"] += int((time.perf_counter() - agent_started) * 1000)
            timing_ms["agent_runs"] += 1

            delta_messages = list(agent.messages[before_count:])
            step_done = self._is_step_done(delta_messages, step.server_id, step.tool_name)
            if not step_done:
                # Retry same step once with stronger enforcement.
                logger.warning(
                    "MCP step miss: first attempt did not call expected server/tool; "
                    f"step={index}, server={step.server_id}, tool={step.tool_name}, retry_once=true"
                )
                retry_before = len(agent.messages)
                retry_prompt = self._policy.build_plan_step_prompt(
                    prompt,
                    server_id=step.server_id,
                    tool_name=step.tool_name,
                    goal=step.goal,
                    reason=step.reason,
                    step_index=index,
                    total_steps=total_steps,
                    retry=True,
                )
                retry_run_prompt = self._mcp_router.augment_prompt_with_mcp_catalog(retry_prompt)
                original_tools = agent.available_tools
                agent.available_tools = self._scope_tools_to_server(agent, step.server_id)
                try:
                    retry_started = time.perf_counter()
                    raw = await self._run_agent(
                        agent=agent,
                        run_prompt=retry_run_prompt,
                        time_budget_seconds=time_budget_seconds,
                        run_state=run_state,
                    )
                finally:
                    agent.available_tools = original_tools
                timing_ms["agent_run_ms"] += int((time.perf_counter() - retry_started) * 1000)
                timing_ms["agent_runs"] += 1
                retry_delta = list(agent.messages[retry_before:])
                step_done = self._is_step_done(retry_delta, step.server_id, step.tool_name)

            if (not step_done) and fallback_server_id and fallback_server_id != step.server_id:
                logger.warning(
                    "MCP step fallback: "
                    f"step={index}, expected={step.server_id}/{step.tool_name}, "
                    f"fallback={fallback_server_id}/{fallback_tool_name or 'auto'}"
                )
                connected_servers = await self._connect_servers_by_ids(
                    agent=agent,
                    server_ids=[fallback_server_id],
                    connected_servers=connected_servers,
                    timing_ms=timing_ms,
                )
                fallback_prompt = self._policy.build_forced_server_retry_prompt(
                    prompt,
                    server_id=fallback_server_id,
                    tool_name=fallback_tool_name,
                )
                fallback_run_prompt = self._mcp_router.augment_prompt_with_mcp_catalog(fallback_prompt)
                fallback_started = time.perf_counter()
                raw = await self._run_agent(
                    agent=agent,
                    run_prompt=fallback_run_prompt,
                    time_budget_seconds=time_budget_seconds,
                    run_state=run_state,
                )
                timing_ms["agent_run_ms"] += int((time.perf_counter() - fallback_started) * 1000)
                timing_ms["agent_runs"] += 1

            expected_steps.append((step.server_id, step.tool_name))
            self._record_step_outcome(
                prompt=prompt,
                step_index=index,
                step=step,
                connected_servers=connected_servers,
                session_id=session_id,
                success=step_done,
            )

        return RuntimeExecutionResult(
            raw=raw,
            connected_servers=connected_servers,
            expected_steps=expected_steps,
        )

    async def _enforce_expected_steps(
        self,
        *,
        agent: Manus,
        prompt: str,
        initial_raw: str,
        connected_servers: list[str],
        expected_steps: list[tuple[str, str]],
        steps: int,
        time_budget_seconds: int,
        reuse_agent: bool,
        shared_agent_lock: asyncio.Lock,
        progress_callback: Callable[[dict[str, Any]], Any] | None,
        emit_progress: Callable[[Callable[[dict[str, Any]], Any] | None, dict[str, Any]], Any],
        run_state: dict[str, bool],
        timing_ms: dict[str, int],
    ) -> str:
        if not expected_steps:
            return initial_raw

        retry_enabled = self._policy.is_truthy_env(
            os.getenv("BFF_RUNTIME_SERVER_RETRY_ON_MISS", "1")
        )
        raw = initial_raw

        async def _retry_once(server_id: str, tool_name: str) -> str:
            retry_prompt = self._policy.build_forced_server_retry_prompt(
                prompt,
                server_id=server_id,
                tool_name=tool_name,
            )
            retry_run_prompt = self._mcp_router.augment_prompt_with_mcp_catalog(retry_prompt)

            async def _inner_retry() -> str:
                if server_id not in connected_servers:
                    connect_started = time.perf_counter()
                    connected = await self._mcp_router.connect_server_by_id(agent, server_id)
                    timing_ms["connect_ms"] += int((time.perf_counter() - connect_started) * 1000)
                    if connected:
                        connected_servers.append(server_id)
                    logger.info(
                        f"Forced {server_id} connection during retry: connected={connected}"
                    )
                agent_started = time.perf_counter()
                result = await self._run_agent(
                    agent=agent,
                    run_prompt=retry_run_prompt,
                    time_budget_seconds=time_budget_seconds,
                    run_state=run_state,
                )
                timing_ms["agent_run_ms"] += int((time.perf_counter() - agent_started) * 1000)
                timing_ms["agent_runs"] += 1
                return result

            return await self._run_with_optional_lock(
                reuse_agent=reuse_agent,
                shared_agent_lock=shared_agent_lock,
                action=_inner_retry,
            )

        for server_id, tool_name in expected_steps:
            used_tool = self._policy.has_server_specific_tool_activity(
                agent.messages, server_id, tool_name
            )
            used_server = self._policy.has_server_tool_activity(agent.messages, server_id)
            used = used_tool or ((tool_name or "").strip().lower() == "auto" and used_server)
            logger.info(
                "Plan step execution check: "
                f"server={server_id}, tool={tool_name}, used={used}, retry_enabled={retry_enabled}"
            )
            if retry_enabled and not used:
                await emit_progress(
                    progress_callback,
                    {
                        "type": "progress",
                        "phase": "act_retry",
                        "maxSteps": steps,
                        "message": f"检测到计划步骤未触发 {server_id}，正在执行一次强制重试",
                    },
                )
                raw = await _retry_once(server_id, tool_name)

        return raw

    async def _connect_servers_by_ids(
        self,
        *,
        agent: Manus,
        server_ids: list[str],
        connected_servers: list[str],
        timing_ms: dict[str, int],
    ) -> list[str]:
        unique_ids: list[str] = []
        for sid in server_ids:
            server_id = (sid or "").strip().lower()
            if not server_id or server_id in unique_ids:
                continue
            unique_ids.append(server_id)

        current = list(connected_servers)
        for server_id in unique_ids:
            if server_id in current:
                continue
            connect_started = time.perf_counter()
            connected = await self._mcp_router.connect_server_by_id(agent, server_id)
            timing_ms["connect_ms"] += int((time.perf_counter() - connect_started) * 1000)
            if connected:
                current.append(server_id)
        return current

    def _is_step_done(self, messages: list[Any], server_id: str, tool_name: str) -> bool:
        used_tool = self._policy.has_server_specific_tool_activity(
            messages, server_id, tool_name
        )
        if used_tool:
            return True
        return (tool_name or "").strip().lower() == "auto" and self._policy.has_server_tool_activity(
            messages, server_id
        )

    def _record_step_outcome(
        self,
        *,
        prompt: str,
        step_index: int,
        step: PlannerStep,
        connected_servers: list[str],
        session_id: str | None,
        success: bool,
    ) -> None:
        request_preview = self._policy.extract_current_user_request(prompt)
        request_preview = " ".join(request_preview.split())
        if len(request_preview) > 180:
            request_preview = f"{request_preview[:180]}..."
        prompt_hash = hashlib.sha256(
            self._mcp_router.normalized_current_user_request(prompt).encode("utf-8")
        ).hexdigest()
        self._mcp_router.record_runtime_routing_event(
            {
                "event_type": "step_outcome",
                "session_id": session_id,
                "prompt_hash": prompt_hash,
                "intent": "planned_execution",
                "selected_server_id": step.server_id,
                "candidate_servers": [step.server_id],
                "scores": {
                    "step_index": step_index,
                    "tool_name": step.tool_name,
                    "goal": step.goal,
                    "success": success,
                    "request_preview": request_preview,
                },
                "connected_servers": list(connected_servers),
                "used_servers": [step.server_id] if success else [],
                "success": success,
            }
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
