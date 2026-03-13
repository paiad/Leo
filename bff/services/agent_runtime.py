from __future__ import annotations

import asyncio
import inspect
import os
import re
from typing import Any, Callable, Protocol

from app.agent.manus import Manus
from app.logger import logger
from app.schema import Memory
from app.tool.tool_collection import ToolCollection
from bff.repositories.store import InMemoryStore


class AgentRuntime(Protocol):
    async def ask(
        self,
        prompt: str,
        *,
        max_steps: int | None = None,
        progress_callback: Callable[[dict[str, Any]], Any] | None = None,
    ) -> str: ...


class ManusRuntime:
    _MCP_ROUTING_STOPWORDS = {
        "mcp",
        "tool",
        "tools",
        "server",
        "servers",
        "stdio",
        "sse",
        "http",
    }

    def __init__(self, store: InMemoryStore | None = None):
        self._store = store
        self._shared_agent: Manus | None = None
        self._shared_agent_lock = asyncio.Lock()

    @staticmethod
    def _is_truthy_env(value: str | None) -> bool:
        return (value or "").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _normalize_text(value: str) -> str:
        return (value or "").strip().lower()

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
    def _tokenize_words(value: str) -> set[str]:
        # Keep simple ASCII word extraction; Chinese relies on substring matching.
        return {token for token in re.findall(r"[a-z0-9_-]{2,}", value.lower())}

    @staticmethod
    def _expand_path(value: str | None) -> str | None:
        if not value:
            return None
        return os.path.abspath(os.path.expandvars(os.path.expanduser(value.strip())))

    def _effective_playwright_args(
        self, server_id: str, args: list[str] | None
    ) -> list[str]:
        if server_id != "playwright":
            return list(args or [])

        user_data_dir = self._expand_path(os.getenv("BFF_PLAYWRIGHT_USER_DATA_DIR"))
        storage_state = self._expand_path(os.getenv("BFF_PLAYWRIGHT_STORAGE_STATE"))

        effective = list(args or [])
        cleaned: list[str] = []
        i = 0
        while i < len(effective):
            token = effective[i]

            if user_data_dir and token == "--isolated":
                i += 1
                continue

            if user_data_dir and (
                token == "--user-data-dir" or token.startswith("--user-data-dir=")
            ):
                i += 2 if token == "--user-data-dir" else 1
                continue

            if storage_state and (
                token == "--storage-state" or token.startswith("--storage-state=")
            ):
                i += 2 if token == "--storage-state" else 1
                continue

            cleaned.append(token)
            i += 1

        if user_data_dir:
            cleaned.extend(["--user-data-dir", user_data_dir])
        if storage_state:
            cleaned.extend(["--storage-state", storage_state])
        return cleaned

    def _server_aliases(self, server_id: str) -> set[str]:
        sid = self._normalize_text(server_id)
        aliases: set[str] = set()
        if sid == "github":
            aliases |= {
                "github",
                "git",
                "repo",
                "repository",
                "pull request",
                "pr",
                "issue",
                "commit",
                "branch",
                "仓库",
                "分支",
                "提交",
                "拉取请求",
                "代码库",
            }
        if sid == "trendradar":
            aliases |= {
                "trendradar",
                "news",
                "rss",
                "trend",
                "hot",
                "热点",
                "热搜",
                "趋势",
                "新闻",
                "情感",
                "抖音",
                "微博",
                "知乎",
                "头条",
                "baidu",
                "toutiao",
            }
        if sid == "playwright":
            aliases |= {
                "playwright",
                "browser",
                "web",
                "website",
                "page",
                "navigate",
                "click",
                "play",
                "music",
                "song",
                "video",
                "audio",
                "player",
                "form",
                "截图",
                "网页",
                "网站",
                "浏览器",
                "打开网页",
                "页面",
                "播放",
                "歌曲",
                "音乐",
                "视频",
                "音频",
                "播放器",
                "暂停",
                "继续播放",
            }
        return aliases

    @staticmethod
    def _clear_current_task_cancellation() -> None:
        task = asyncio.current_task()
        if task is None or not hasattr(task, "uncancel"):
            return
        while task.cancelling():
            task.uncancel()

    def _build_mcp_catalog_context(self) -> str:
        """
        Build a factual MCP catalog block so the model can answer MCP/tooling
        questions correctly even when no MCP server is connected for this turn.
        """
        if not self._store:
            return ""

        servers = sorted(self._store.mcp_servers.values(), key=lambda s: s.serverId)
        if not servers:
            return ""

        enabled_count = sum(1 for s in servers if s.enabled)
        lines = [
            "[Runtime MCP Catalog]",
            "Use this as source of truth for MCP questions.",
            "Builtin tools (python_execute/str_replace_editor/terminate/browser_use) are NOT MCP servers.",
            f"configured_mcp_servers={len(servers)}",
            f"enabled_mcp_servers={enabled_count}",
            "servers:",
        ]
        for server in servers:
            discovered_tools = getattr(server, "discoveredTools", []) or []
            lines.append(
                f"- id={server.serverId}; enabled={server.enabled}; type={server.type}; "
                f"name={server.name}; discovered_tools={len(discovered_tools)}"
            )
        return "\n".join(lines)

    def _augment_prompt_with_mcp_catalog(self, prompt: str) -> str:
        catalog = self._build_mcp_catalog_context()
        if not catalog:
            return prompt
        return f"{prompt}\n\n{catalog}"

    def _should_connect_server(self, prompt: str, server: Any) -> bool:
        prompt_text = self._normalize_text(self._extract_current_user_request(prompt))
        if not prompt_text:
            return False

        if self._is_truthy_env(os.getenv("BFF_RUNTIME_CONNECT_ALL_MCP")):
            return True

        metadata_parts: list[str] = [
            self._normalize_text(getattr(server, "serverId", "")),
            self._normalize_text(getattr(server, "name", "")),
            self._normalize_text(getattr(server, "description", "")),
        ]
        discovered_tools = getattr(server, "discoveredTools", []) or []
        for tool in discovered_tools:
            tool_name = getattr(tool, "name", "")
            tool_desc = getattr(tool, "description", "")
            metadata_parts.append(self._normalize_text(tool_name))
            metadata_parts.append(self._normalize_text(tool_desc))

        metadata_text = " ".join(part for part in metadata_parts if part)
        alias_set = self._server_aliases(getattr(server, "serverId", ""))

        # Fast substring path for Chinese/phrases.
        for alias in alias_set:
            if alias and alias in prompt_text:
                return True
        for part in metadata_parts:
            if part and len(part) >= 3 and part in prompt_text:
                return True

        # English keyword overlap path.
        prompt_tokens = self._tokenize_words(prompt_text) - self._MCP_ROUTING_STOPWORDS
        metadata_tokens = self._tokenize_words(metadata_text) - self._MCP_ROUTING_STOPWORDS
        alias_tokens = {
            tok
            for alias in alias_set
            for tok in (self._tokenize_words(alias) - self._MCP_ROUTING_STOPWORDS)
        }
        if not prompt_tokens:
            return False
        return len(prompt_tokens & (metadata_tokens | alias_tokens)) > 0

    async def _connect_enabled_mcp_servers(self, agent: Manus, prompt: str) -> None:
        if not self._store:
            return

        use_local_mcp = self._is_truthy_env(
            os.getenv("BFF_RUNTIME_USE_LEO_LOCAL_MCP", "0")
        ) or self._is_truthy_env(os.getenv("BFF_RUNTIME_USE_OPENMANUS_LOCAL_MCP", "0"))

        selected_servers = []
        for server in self._store.mcp_servers.values():
            if not server.enabled:
                continue
            if server.serverId in {"leo-local", "openmanus-local"} and not use_local_mcp:
                continue
            if self._should_connect_server(prompt, server):
                selected_servers.append(server)

        if selected_servers:
            # Multi-server connect/disconnect in the same request is currently unstable
            # with some stdio MCP servers on Windows (cancel-scope propagation). Keep
            # default routing to a single best-match server unless explicitly enabled.
            if (
                len(selected_servers) > 1
                and not self._is_truthy_env(os.getenv("BFF_RUNTIME_ALLOW_MULTI_MCP_PER_REQUEST"))
            ):
                logger.warning(
                    "Multiple MCP servers matched; selecting the first match only for stability: "
                    f"{[server.serverId for server in selected_servers]}"
                )
                selected_servers = [selected_servers[0]]
            logger.info(
                f"MCP on-demand selected servers: {[server.serverId for server in selected_servers]}"
            )
        else:
            logger.info("MCP on-demand selected servers: []")

        for server in selected_servers:
            try:
                if server.type == "stdio":
                    if not server.command:
                        continue
                    await agent.connect_mcp_server(
                        server.command,
                        server_id=server.serverId,
                        use_stdio=True,
                        stdio_args=self._effective_playwright_args(
                            server.serverId, server.args
                        ),
                        stdio_env=server.env or None,
                    )
                else:
                    if not server.url:
                        continue
                    await agent.connect_mcp_server(
                        server.url,
                        server_id=server.serverId,
                        use_stdio=False,
                    )
            except Exception as exc:
                logger.warning(
                    f"Failed to connect runtime MCP server {server.serverId}: {exc}"
                )

        # Treat MCP terminate tools as special finish tools to avoid max-step loops.
        remote_terminate_tools = [
            tool_name
            for tool_name in agent.available_tools.tool_map.keys()
            if tool_name.startswith("mcp_") and tool_name.endswith("_terminate")
        ]
        for tool_name in remote_terminate_tools:
            if tool_name not in agent.special_tool_names:
                agent.special_tool_names.append(tool_name)

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
        if progress_callback:
            try:
                maybe_awaitable = progress_callback(
                    {
                        "type": "progress",
                        "phase": "runtime_start",
                        "maxSteps": steps,
                        "message": f"运行中，最多 {steps} 步",
                    }
                )
                if inspect.isawaitable(maybe_awaitable):
                    await maybe_awaitable
            except Exception as exc:
                logger.debug(f"Failed to emit runtime_start progress: {exc}")
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
                tool for tool in agent.available_tools.tools if getattr(tool, "name", "") != "ask_human"
            )
            agent.available_tools = ToolCollection(*filtered_tools)
            if reuse_agent:
                async with self._shared_agent_lock:
                    await self._connect_enabled_mcp_servers(agent, prompt)
                    run_prompt = self._augment_prompt_with_mcp_catalog(prompt)
                    run_invoked = True
                    raw = await agent.run(run_prompt)
            else:
                await self._connect_enabled_mcp_servers(agent, prompt)
                run_prompt = self._augment_prompt_with_mcp_catalog(prompt)
                run_invoked = True
                raw = await agent.run(run_prompt)

            assistant_messages: list[str] = []
            for message in agent.messages:
                role = getattr(message, "role", None)
                content = getattr(message, "content", None)
                role_value = getattr(role, "value", role)
                if role_value == "assistant" and isinstance(content, str) and content.strip():
                    assistant_messages.append(content)
            if assistant_messages:
                if progress_callback:
                    try:
                        maybe_awaitable = progress_callback(
                            {
                                "type": "progress",
                                "phase": "runtime_done",
                                "message": "执行完成，正在整理答案",
                            }
                        )
                        if inspect.isawaitable(maybe_awaitable):
                            await maybe_awaitable
                    except Exception as exc:
                        logger.debug(f"Failed to emit runtime_done progress: {exc}")
                return assistant_messages[-1]
            return raw
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
