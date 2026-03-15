from __future__ import annotations

import asyncio
from typing import Any

from app.logger import logger
from app.tool.mcp import MCPClients
from bff.repositories.store import InMemoryStore
from bff.services.memory.types import TurnMemoryPayload
from bff.utils.memory_settings import is_memory_sync_enabled


class MemorySyncService:
    def __init__(self, store: InMemoryStore | None = None):
        self._store = store

    def schedule_sync_turn(
        self,
        *,
        source: str,
        session_id: str,
        question: str,
        answer: str,
        model: str | None,
    ) -> None:
        task = asyncio.create_task(
            self._sync_turn(
                source=source,
                session_id=session_id,
                question=question,
                answer=answer,
                model=model,
            )
        )
        task.add_done_callback(self._on_done)

    @staticmethod
    def _on_done(task: asyncio.Task[Any]) -> None:
        try:
            task.result()
        except Exception as exc:
            logger.warning(f"Memory MCP sync failed: {exc}")

    async def _sync_turn(
        self,
        *,
        source: str,
        session_id: str,
        question: str,
        answer: str,
        model: str | None,
    ) -> None:
        if not is_memory_sync_enabled():
            return
        if not self._store:
            return

        server = self._store.mcp_servers.get("memory")
        if not server or not server.enabled:
            return

        clients = MCPClients()
        try:
            if server.type == "stdio":
                if not server.command:
                    return
                await clients.connect_stdio(
                    command=server.command,
                    args=server.args or [],
                    server_id=server.serverId,
                    env=server.env or None,
                )
            elif server.type == "streamablehttp":
                if not server.url:
                    return
                await clients.connect_streamable_http(
                    server.url,
                    server.serverId,
                    headers=server.env or None,
                )
            else:
                if not server.url:
                    return
                await clients.connect_sse(
                    server.url,
                    server.serverId,
                    headers=server.env or None,
                )

            session = clients.sessions.get(server.serverId)
            if session is None:
                return

            tools_response = await session.list_tools()
            tool_names = {tool.name for tool in tools_response.tools}
            if not tool_names:
                return

            memory_text = TurnMemoryPayload(
                source=source,
                session_id=session_id,
                question=question,
                answer=answer,
                model=model,
            ).to_json_text()
            synced = await self._sync_with_known_tools(
                session=session,
                tool_names=tool_names,
                memory_text=memory_text,
                source=source,
                session_id=session_id,
            )
            if synced:
                logger.info(
                    "Memory MCP sync success: "
                    f"session_id={session_id}, source={source}, tools={sorted(tool_names)}"
                )
                return

            await self._sync_with_generic_tool(
                session=session,
                tools=tools_response.tools,
                memory_text=memory_text,
                source=source,
                session_id=session_id,
            )
        finally:
            try:
                await clients.disconnect()
            except Exception:
                pass

    async def _sync_with_known_tools(
        self,
        *,
        session: Any,
        tool_names: set[str],
        memory_text: str,
        source: str,
        session_id: str,
    ) -> bool:
        entity_name = f"session:{source}:{session_id}"

        # Common toolset for @modelcontextprotocol/server-memory (knowledge graph).
        if "create_entities" in tool_names:
            await session.call_tool(
                "create_entities",
                {
                    "entities": [
                        {
                            "name": entity_name,
                            "entityType": "conversation_session",
                            "observations": [memory_text],
                        }
                    ]
                },
            )
            return True

        if "add_observations" in tool_names:
            await session.call_tool(
                "add_observations",
                {
                    "observations": [
                        {
                            "entityName": entity_name,
                            "contents": [memory_text],
                        }
                    ]
                },
            )
            return True

        for name in (
            "remember",
            "write_memory",
            "store_memory",
            "upsert_memory",
            "save_memory",
            "add_memory",
        ):
            if name not in tool_names:
                continue
            await session.call_tool(name, {"text": memory_text})
            return True
        return False

    async def _sync_with_generic_tool(
        self,
        *,
        session: Any,
        tools: list[Any],
        memory_text: str,
        source: str,
        session_id: str,
    ) -> None:
        for tool in tools:
            tool_name = str(getattr(tool, "name", "") or "")
            if not tool_name:
                continue
            schema = getattr(tool, "inputSchema", {}) or {}
            args = self._build_generic_args(
                schema=schema,
                memory_text=memory_text,
                source=source,
                session_id=session_id,
            )
            if args is None:
                continue
            await session.call_tool(tool_name, args)
            return

    def _build_generic_args(
        self,
        *,
        schema: dict[str, Any],
        memory_text: str,
        source: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        properties = schema.get("properties")
        required = schema.get("required")
        if not isinstance(properties, dict) or not isinstance(required, list) or not required:
            return None

        args: dict[str, Any] = {}
        for key in required:
            if not isinstance(key, str):
                continue
            prop = properties.get(key)
            if not isinstance(prop, dict):
                args[key] = memory_text
                continue

            key_lower = key.lower()
            prop_type = prop.get("type")
            if "session" in key_lower:
                args[key] = session_id
                continue
            if "source" in key_lower:
                args[key] = source
                continue
            if "text" in key_lower or "content" in key_lower or "memory" in key_lower:
                args[key] = memory_text
                continue
            if prop_type == "array":
                args[key] = [memory_text]
                continue
            if prop_type == "object":
                args[key] = {"text": memory_text, "source": source, "session_id": session_id}
                continue
            args[key] = memory_text

        return args if args else None
