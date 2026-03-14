from contextlib import AsyncExitStack
from typing import Any, Dict, List, Optional
import asyncio
import json

from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.types import ListToolsResult, TextContent

from app.logger import logger
from app.tool.base import BaseTool, ToolResult
from app.tool.tool_collection import ToolCollection


class MCPClientTool(BaseTool):
    """Represents a tool proxy that can be called on the MCP server from the client side."""

    session: Optional[ClientSession] = None
    server_id: str = ""  # Add server identifier
    original_name: str = ""

    @staticmethod
    def _infer_expected_types(schema: dict[str, Any] | None) -> set[str]:
        """Infer possible JSON types from a JSON schema fragment."""
        if not schema:
            return set()

        inferred: set[str] = set()
        schema_type = schema.get("type")
        if isinstance(schema_type, str):
            inferred.add(schema_type)
        elif isinstance(schema_type, list):
            inferred.update(t for t in schema_type if isinstance(t, str))

        for key in ("oneOf", "anyOf", "allOf"):
            union_parts = schema.get(key)
            if isinstance(union_parts, list):
                for part in union_parts:
                    if isinstance(part, dict):
                        inferred.update(MCPClientTool._infer_expected_types(part))

        return inferred

    def _normalize_tool_input(self, kwargs: dict[str, Any]) -> dict[str, Any]:
        """
        Normalize tool inputs against tool schema.
        Converts stringified JSON array/object to native list/dict when schema expects them.
        """
        schema_properties = {}
        if isinstance(self.parameters, dict):
            maybe_properties = self.parameters.get("properties")
            if isinstance(maybe_properties, dict):
                schema_properties = maybe_properties

        normalized: dict[str, Any] = {}
        for key, value in kwargs.items():
            if not isinstance(value, str):
                normalized[key] = value
                continue

            property_schema = schema_properties.get(key) if isinstance(schema_properties, dict) else None
            expected_types = self._infer_expected_types(property_schema if isinstance(property_schema, dict) else None)
            if "array" not in expected_types and "object" not in expected_types:
                normalized[key] = value
                continue

            stripped = value.strip()
            if not stripped or (not stripped.startswith("[") and not stripped.startswith("{")):
                normalized[key] = value
                continue

            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                normalized[key] = value
                continue

            if isinstance(parsed, list) and "array" in expected_types:
                logger.info(f"Normalized MCP argument '{key}' from stringified JSON array")
                normalized[key] = parsed
                continue
            if isinstance(parsed, dict) and "object" in expected_types:
                logger.info(f"Normalized MCP argument '{key}' from stringified JSON object")
                normalized[key] = parsed
                continue

            normalized[key] = value

        return normalized

    @staticmethod
    def _extract_text_content(result: Any) -> str:
        return ", ".join(
            item.text for item in getattr(result, "content", []) if isinstance(item, TextContent)
        )

    @staticmethod
    def _looks_like_media_toggle_click(kwargs: dict[str, Any]) -> bool:
        element = str(kwargs.get("element") or "").strip().lower()
        if not element:
            return False
        markers = ("play", "pause", "play/pause", "播放", "暂停")
        return any(marker in element for marker in markers)

    async def _call_tool_text(self, tool_name: str, tool_input: dict[str, Any]) -> str:
        result = await self.session.call_tool(tool_name, tool_input)
        return self._extract_text_content(result) or "No output returned."

    async def _run_playwright_media_guard(self) -> str:
        """
        Best-effort post-click media guard for toggle controls.
        Goal: avoid accidental pause after a blind play/pause click.
        """
        if not self.session:
            return ""

        lines: list[str] = []
        try:
            before = await self._call_tool_text(
                "browser_evaluate",
                {
                    "function": (
                        "() => { const v = document.querySelector('video, audio'); "
                        "if (!v) return { hasMedia:false }; "
                        "return { hasMedia:true, paused:v.paused, currentTime:v.currentTime, readyState:v.readyState }; }"
                    )
                },
            )
            lines.append(f"before={before}")
        except Exception as exc:
            lines.append(f"before_error={exc}")
            return " ; ".join(lines)

        try:
            await self._call_tool_text("browser_wait_for", {"time": 1})
            recover = await self._call_tool_text(
                "browser_evaluate",
                {
                    "function": (
                        "async () => { const v = document.querySelector('video, audio'); "
                        "if (!v) return { hasMedia:false }; "
                        "if (v.paused) { try { await v.play(); } catch (e) { return { hasMedia:true, resumed:false, error:String(e) }; } } "
                        "return { hasMedia:true, resumed:!v.paused, paused:v.paused, currentTime:v.currentTime, readyState:v.readyState }; }"
                    )
                },
            )
            lines.append(f"recover={recover}")
        except Exception as exc:
            lines.append(f"recover_error={exc}")

        try:
            await self._call_tool_text("browser_wait_for", {"time": 1})
            after = await self._call_tool_text(
                "browser_evaluate",
                {
                    "function": (
                        "() => { const v = document.querySelector('video, audio'); "
                        "if (!v) return { hasMedia:false }; "
                        "return { hasMedia:true, paused:v.paused, currentTime:v.currentTime, readyState:v.readyState }; }"
                    )
                },
            )
            lines.append(f"after={after}")
        except Exception as exc:
            lines.append(f"after_error={exc}")

        return " ; ".join(lines)

    async def execute(self, **kwargs) -> ToolResult:
        """Execute the tool by making a remote call to the MCP server."""
        if not self.session:
            return ToolResult(error="Not connected to MCP server")

        try:
            logger.info(f"Executing tool: {self.original_name}")
            normalized_kwargs = self._normalize_tool_input(kwargs)
            result = await self.session.call_tool(self.original_name, normalized_kwargs)
            content_str = self._extract_text_content(result)

            if (
                self.server_id == "playwright"
                and self.original_name == "browser_click"
                and self._looks_like_media_toggle_click(normalized_kwargs)
            ):
                guard_output = await self._run_playwright_media_guard()
                if guard_output:
                    content_str = (
                        (content_str + "\n\n" if content_str else "")
                        + "[Playwright Media Guard]\n"
                        + guard_output
                    )

            return ToolResult(output=content_str or "No output returned.")
        except Exception as e:
            return ToolResult(error=f"Error executing tool: {str(e)}")


class MCPClients(ToolCollection):
    """
    A collection of tools that connects to multiple MCP servers and manages available tools through the Model Context Protocol.
    """

    sessions: Dict[str, ClientSession]
    exit_stacks: Dict[str, AsyncExitStack]
    _connection_order: list[str]
    description: str = "MCP client tools for server interaction"

    def __init__(self):
        super().__init__()  # Initialize with empty tools list
        self.name = "mcp"  # Keep name for backward compatibility
        # Keep MCP transport/session state isolated per MCPClients instance.
        # Shared class-level state causes cross-request disconnect races.
        self.sessions = {}
        self.exit_stacks = {}
        self._connection_order = []

    def _mark_connected(self, server_id: str) -> None:
        # Keep last-connected order for LIFO disconnect.
        self._connection_order = [sid for sid in self._connection_order if sid != server_id]
        self._connection_order.append(server_id)

    def _mark_disconnected(self, server_id: str) -> None:
        self._connection_order = [sid for sid in self._connection_order if sid != server_id]

    @staticmethod
    def _augment_playwright_tool_description(
        server_id: str, tool_name: str, description: str | None
    ) -> str:
        """
        Add actionable fallback guidance for Playwright MCP browser controls.
        This improves success rate on media players where visible controls are
        often shadowed/overlaid and cannot be clicked directly.
        """
        if server_id != "playwright":
            return description or ""

        if not tool_name.startswith("browser_"):
            return description or ""

        base = description or ""
        guidance = (
            "\n\nPlaywright reliability notes for media controls:\n"
            "- Before any media control click, call browser_snapshot to get the latest element refs and labels.\n"
            "- Treat play/pause as a TOGGLE control: never blind-click it without checking current state first.\n"
            "- On YouTube-like players, button label usually indicates next action: '播放' means currently paused, '暂停' means currently playing.\n"
            "- If timeline/progress is already advancing, do NOT click play again.\n"
            "- If play/pause button is not clickable, focus the player area then try browser_press_key with Space or k.\n"
            "- If keyboard still fails, use browser_evaluate on page to run a fallback script that finds <audio>/<video> and calls play().\n"
            "- After each action, verify state change (timeline/progress/time text); if no progress increase after 1-2s, then retry once with fresh snapshot.\n"
            "- Prefer retrying with a fresh snapshot over repeated blind clicks; avoid repeated clicks on the same play/pause button.\n"
        )
        return f"{base}{guidance}"

    def _disconnect_all_server_ids(self) -> list[str]:
        """
        Return server IDs in safe disconnect order.
        AnyIO cancel scopes used by stdio transports require strict reverse
        exit order relative to __aenter__.
        """
        ordered: list[str] = []
        seen: set[str] = set()
        for sid in self._connection_order:
            if sid in self.sessions and sid not in seen:
                ordered.append(sid)
                seen.add(sid)
        for sid in self.sessions.keys():
            if sid not in seen:
                ordered.append(sid)
                seen.add(sid)
        return list(reversed(ordered))

    @staticmethod
    def _clear_current_task_cancellation() -> None:
        """
        Clear cancellation state when we intentionally swallow CancelledError.
        Python 3.11+ keeps cancellation requests pending unless `uncancel()` is called.
        """
        task = asyncio.current_task()
        if task is None or not hasattr(task, "uncancel"):
            return
        while task.cancelling():
            task.uncancel()

    async def connect_sse(self, server_url: str, server_id: str = "") -> None:
        """Connect to an MCP server using SSE transport."""
        if not server_url:
            raise ValueError("Server URL is required.")

        server_id = server_id or server_url

        # Always ensure clean disconnection before new connection
        if server_id in self.sessions:
            await self.disconnect(server_id)

        exit_stack = AsyncExitStack()
        self.exit_stacks[server_id] = exit_stack

        streams_context = sse_client(url=server_url)
        streams = await exit_stack.enter_async_context(streams_context)
        session = await exit_stack.enter_async_context(ClientSession(*streams))
        self.sessions[server_id] = session
        self._mark_connected(server_id)

        await self._initialize_and_list_tools(server_id)

    async def connect_stdio(
        self,
        command: str,
        args: List[str],
        server_id: str = "",
        env: Optional[Dict[str, str]] = None,
    ) -> None:
        """Connect to an MCP server using stdio transport."""
        if not command:
            raise ValueError("Server command is required.")

        server_id = server_id or command

        # Always ensure clean disconnection before new connection
        if server_id in self.sessions:
            await self.disconnect(server_id)

        exit_stack = AsyncExitStack()
        self.exit_stacks[server_id] = exit_stack

        server_params = StdioServerParameters(
            command=command,
            args=args,
            env=env or None,
        )
        stdio_transport = await exit_stack.enter_async_context(
            stdio_client(server_params)
        )
        read, write = stdio_transport
        session = await exit_stack.enter_async_context(ClientSession(read, write))
        self.sessions[server_id] = session
        self._mark_connected(server_id)

        await self._initialize_and_list_tools(server_id)

    async def _initialize_and_list_tools(self, server_id: str) -> None:
        """Initialize session and populate tool map."""
        session = self.sessions.get(server_id)
        if not session:
            raise RuntimeError(f"Session not initialized for server {server_id}")

        await session.initialize()
        response = await session.list_tools()

        # Create proper tool objects for each server tool
        for tool in response.tools:
            original_name = tool.name
            tool_name = f"mcp_{server_id}_{original_name}"
            tool_name = self._sanitize_tool_name(tool_name)
            description = self._augment_playwright_tool_description(
                server_id=server_id,
                tool_name=original_name,
                description=tool.description,
            )

            server_tool = MCPClientTool(
                name=tool_name,
                description=description,
                parameters=tool.inputSchema,
                session=session,
                server_id=server_id,
                original_name=original_name,
            )
            self.tool_map[tool_name] = server_tool

        # Update tools tuple
        self.tools = tuple(self.tool_map.values())
        logger.info(
            f"Connected to server {server_id} with tools: {[tool.name for tool in response.tools]}"
        )

    def _sanitize_tool_name(self, name: str) -> str:
        """Sanitize tool name to match MCPClientTool requirements."""
        import re

        # Replace invalid characters with underscores
        sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", name)

        # Remove consecutive underscores
        sanitized = re.sub(r"_+", "_", sanitized)

        # Remove leading/trailing underscores
        sanitized = sanitized.strip("_")

        # Truncate to 64 characters if needed
        if len(sanitized) > 64:
            sanitized = sanitized[:64]

        return sanitized

    async def list_tools(self) -> ListToolsResult:
        """List all available tools."""
        tools_result = ListToolsResult(tools=[])
        for session in self.sessions.values():
            response = await session.list_tools()
            tools_result.tools += response.tools
        return tools_result

    async def disconnect(self, server_id: str = "") -> None:
        """Disconnect from a specific MCP server or all servers if no server_id provided."""
        if server_id:
            if server_id in self.sessions:
                try:
                    exit_stack = self.exit_stacks.get(server_id)

                    # Close the exit stack which will handle session cleanup
                    if exit_stack:
                        try:
                            await exit_stack.aclose()
                        except asyncio.CancelledError as e:
                            logger.warning(
                                f"Disconnect from {server_id} was cancelled during cleanup, forcing local detach: {e}"
                            )
                            self._clear_current_task_cancellation()
                        except RuntimeError as e:
                            if "cancel scope" in str(e).lower():
                                logger.warning(
                                    f"Cancel scope error during disconnect from {server_id}, continuing with cleanup: {e}"
                                )
                                self._clear_current_task_cancellation()
                            else:
                                raise

                    # Clean up references
                    self.sessions.pop(server_id, None)
                    self.exit_stacks.pop(server_id, None)
                    self._mark_disconnected(server_id)

                    # Remove tools associated with this server
                    self.tool_map = {
                        k: v
                        for k, v in self.tool_map.items()
                        if v.server_id != server_id
                    }
                    self.tools = tuple(self.tool_map.values())
                    # Give asyncio transport callbacks one tick to settle on Windows Proactor.
                    await asyncio.sleep(0)
                    logger.info(f"Disconnected from MCP server {server_id}")
                except Exception as e:
                    logger.error(f"Error disconnecting from server {server_id}: {e}")
        else:
            # Disconnect from all servers in reverse connection order (LIFO).
            for sid in self._disconnect_all_server_ids():
                try:
                    await self.disconnect(sid)
                except asyncio.CancelledError as e:
                    logger.warning(
                        f"Disconnect from {sid} cancelled while disconnecting all servers, continuing: {e}"
                    )
                    self._clear_current_task_cancellation()
            self.tool_map = {}
            self.tools = tuple()
            self._connection_order = []
            logger.info("Disconnected from all MCP servers")
