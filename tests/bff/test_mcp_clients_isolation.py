import pytest
from contextlib import asynccontextmanager

from app.tool import mcp as mcp_module
from app.tool.mcp import MCPClients


def test_mcp_clients_state_is_per_instance():
    first = MCPClients()
    second = MCPClients()

    first.sessions["github"] = object()  # type: ignore[assignment]
    first.exit_stacks["github"] = object()  # type: ignore[assignment]

    assert first.sessions is not second.sessions
    assert first.exit_stacks is not second.exit_stacks
    assert second.sessions == {}
    assert second.exit_stacks == {}


def test_mcp_clients_disconnect_order_is_lifo():
    clients = MCPClients()
    clients.sessions["github"] = object()  # type: ignore[assignment]
    clients.sessions["trendradar"] = object()  # type: ignore[assignment]
    clients._connection_order = ["github", "trendradar"]

    assert clients._disconnect_all_server_ids() == ["trendradar", "github"]


@pytest.mark.asyncio
async def test_connect_streamable_http_missing_sdk_support_raises():
    clients = MCPClients()
    if mcp_module.streamable_http_client is not None:
        pytest.skip("Installed mcp SDK already supports streamable_http_client")

    with pytest.raises(ValueError, match="不支持 streamablehttp"):
        await clients.connect_streamable_http("https://example.com/mcp", "example")


@pytest.mark.asyncio
async def test_connect_streamable_http_ignores_session_callback_tuple_item(monkeypatch):
    clients = MCPClients()

    @asynccontextmanager
    async def fake_streamable_http_client(*args, **kwargs):
        yield ("read_stream", "write_stream", lambda: "session-id")

    class _DummySession:
        def __init__(self, read, write):
            self.read = read
            self.write = write

        async def initialize(self):
            return None

        async def list_tools(self):
            class _Resp:
                tools = []

            return _Resp()

    @asynccontextmanager
    async def fake_client_session(read, write):
        yield _DummySession(read, write)

    monkeypatch.setattr(mcp_module, "streamable_http_client", fake_streamable_http_client)
    monkeypatch.setattr(mcp_module, "ClientSession", fake_client_session)

    await clients.connect_streamable_http("https://example.com/mcp", "example")

    assert "example" in clients.sessions
