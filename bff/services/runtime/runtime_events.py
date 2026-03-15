from __future__ import annotations

import asyncio
from typing import Any, Callable

from app.agent.manus import Manus
from app.logger import logger
from app.schema import AgentState
from bff.services.runtime.runtime_policy import RuntimeStallDetector
from bff.services.runtime.runtime_progress import RuntimeProgressEmitter


class RuntimeEventManager:
    def __init__(self, progress_emitter: RuntimeProgressEmitter | None = None):
        self._progress = progress_emitter or RuntimeProgressEmitter()

    async def emit_progress(
        self,
        callback: Callable[[dict[str, Any]], Any] | None,
        payload: dict[str, Any],
    ) -> None:
        await self._progress.emit(callback, payload)

    def build_runtime_event_callback(
        self,
        *,
        agent: Manus,
        user_callback: Callable[[dict[str, Any]], Any] | None,
        stall_detector: RuntimeStallDetector | None,
    ) -> Callable[[dict[str, Any]], Any]:
        async def _wrapped(event: dict[str, Any]) -> None:
            if stall_detector is not None:
                stall_reason = stall_detector.observe(event)
                if stall_reason and agent.state != AgentState.FINISHED:
                    logger.warning(f"Runtime stall detector triggered: {stall_reason}")
                    agent.state = AgentState.FINISHED
                    await self.emit_progress(
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
