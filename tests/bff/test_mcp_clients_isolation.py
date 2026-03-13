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
