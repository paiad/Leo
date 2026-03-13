import asyncio

import pytest

from app.agent.base import BaseAgent
from app.agent.toolcall import ToolCallAgent


class _CleanupCancelledAgent(ToolCallAgent):
    async def cleanup(self):
        raise asyncio.CancelledError()


class _CleanupFailedAgent(ToolCallAgent):
    async def cleanup(self):
        raise RuntimeError("cleanup failed")


@pytest.mark.asyncio
async def test_toolcall_run_ignores_cleanup_cancelled(monkeypatch):
    async def fake_base_run(self, request=None):
        return "ok"

    monkeypatch.setattr(BaseAgent, "run", fake_base_run, raising=True)
    agent = _CleanupCancelledAgent()

    result = await agent.run("hello")
    assert result == "ok"


@pytest.mark.asyncio
async def test_toolcall_run_ignores_cleanup_exception(monkeypatch):
    async def fake_base_run(self, request=None):
        return "ok"

    monkeypatch.setattr(BaseAgent, "run", fake_base_run, raising=True)
    agent = _CleanupFailedAgent()

    result = await agent.run("hello")
    assert result == "ok"
