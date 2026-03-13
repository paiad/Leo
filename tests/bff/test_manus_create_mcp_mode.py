import pytest

from app.agent.manus import Manus


@pytest.mark.asyncio
async def test_manus_create_can_skip_auto_mcp_init(monkeypatch):
    called = {"value": False}

    async def fake_initialize(self):
        called["value"] = True

    monkeypatch.setattr(Manus, "initialize_mcp_servers", fake_initialize, raising=True)

    agent = await Manus.create(initialize_mcp=False, max_steps=1)
    try:
        assert called["value"] is False
        assert agent._initialized is True
    finally:
        await agent.cleanup()


@pytest.mark.asyncio
async def test_manus_create_auto_mcp_init_enabled(monkeypatch):
    called = {"value": False}

    async def fake_initialize(self):
        called["value"] = True

    monkeypatch.setattr(Manus, "initialize_mcp_servers", fake_initialize, raising=True)

    agent = await Manus.create(initialize_mcp=True, max_steps=1)
    try:
        assert called["value"] is True
        assert agent._initialized is True
    finally:
        await agent.cleanup()
