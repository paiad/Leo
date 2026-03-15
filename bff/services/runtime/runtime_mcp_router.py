from __future__ import annotations

import os
import re
from typing import Any, Literal

from app.agent.manus import Manus
from app.logger import logger
from bff.repositories.store import InMemoryStore


class RuntimeMcpRouter:
    IntentType = Literal[
        "browser_automation",
        "web_search",
        "repo_ops",
        "knowledge_qa",
        "tooling_meta",
        "general",
    ]

    _MCP_ROUTING_STOPWORDS = {
        "mcp",
        "tool",
        "tools",
        "server",
        "servers",
        "stdio",
        "sse",
        "http",
        "streamablehttp",
    }
    _RAG_QA_HINTS = {
        "什么是",
        "是什么意思",
        "意思",
        "含义",
        "定义",
        "解释",
        "解释一下",
        "概念",
        "作用",
        "原理",
        "区别",
        "meaning",
        "what is",
        "define",
        "definition",
        "explain",
    }
    _RAG_NEGATIVE_HINTS = {
        "不要用知识库",
        "不需要知识库",
        "不要知识库",
        "不要检索",
        "不要 rag",
        "no rag",
        "without rag",
        "don't use rag",
    }
    _TOOLING_META_HINTS = {
        "mcp",
        "tool",
        "tools",
        "server",
        "servers",
        "工具",
        "服务器",
        "接口",
        "api",
    }
    _TOOLING_META_QUERY_HINTS = {
        "有哪些",
        "都有什么",
        "工具列表",
        "server list",
        "list",
        "available",
        "show",
        "what mcp tools",
        "what tools",
        "怎么配置",
        "如何配置",
        "配置",
        "连接",
        "启用",
        "禁用",
    }
    _SEARCH_HINTS = {
        "search",
        "web search",
        "find",
        "look up",
        "news",
        "查一下",
        "搜索",
        "检索",
        "资讯",
    }
    _TRENDRADAR_NEWS_HINTS = {
        "news",
        "rss",
        "hot",
        "trend",
        "top",
        "热点",
        "热搜",
        "趋势",
        "新闻",
        "抖音",
        "微博",
        "知乎",
        "头条",
        "douyin",
        "weibo",
        "zhihu",
        "toutiao",
        "baidu",
    }
    _REPO_HINTS = {
        "github",
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
    _BROWSER_ACTION_HINTS = {
        "open",
        "open website",
        "navigate",
        "click",
        "fill form",
        "play",
        "pause",
        "video",
        "music",
        "song",
        "bilibili",
        "youtube",
        "browser",
        "web page",
        "网页",
        "网站",
        "浏览器",
        "打开",
        "打开网页",
        "页面",
        "点击",
        "播放",
        "暂停",
        "视频",
        "音乐",
        "歌曲",
        "b站",
    }

    def __init__(self, store: InMemoryStore | None = None):
        self._store = store

    @staticmethod
    def _is_truthy_env(value: str | None) -> bool:
        return (value or "").strip().lower() in {"1", "true", "yes", "on"}

    @staticmethod
    def _normalize_text(value: str) -> str:
        return (value or "").strip().lower()

    @staticmethod
    def _extract_current_user_request(prompt: str) -> str:
        marker = "[Current User Request]"
        if marker not in prompt:
            return prompt
        return prompt.rsplit(marker, 1)[-1].strip()

    @staticmethod
    def _tokenize_words(value: str) -> set[str]:
        # Keep simple ASCII word extraction; Chinese relies on substring matching.
        return {token for token in re.findall(r"[a-z0-9_-]{2,}", value.lower())}

    def _is_rag_negative_opt_out(self, prompt_text: str) -> bool:
        return any(hint in prompt_text for hint in self._RAG_NEGATIVE_HINTS)

    def _looks_like_trendradar_news_request(self, prompt_text: str) -> bool:
        if not prompt_text:
            return False
        return any(hint in prompt_text for hint in self._TRENDRADAR_NEWS_HINTS)

    def _is_tooling_meta_query(self, prompt_text: str) -> bool:
        if any(hint in prompt_text for hint in {"mcp工具", "mcp 服务器", "mcp工具列表"}):
            return True

        contains_mcp = "mcp" in prompt_text
        contains_meta_nouns = any(
            hint in prompt_text
            for hint in {"tool", "tools", "server", "servers", "工具", "服务器"}
        )
        if not (contains_mcp and contains_meta_nouns):
            return False

        # Prevent false positives when wrappers mention MCP/tool/server while
        # the actual user ask is a concrete business task (e.g. hot-news query).
        if self._looks_like_trendradar_news_request(prompt_text):
            return False
        if any(hint in prompt_text for hint in (self._SEARCH_HINTS | self._BROWSER_ACTION_HINTS | self._REPO_HINTS)):
            return False

        if any(hint in prompt_text for hint in self._TOOLING_META_QUERY_HINTS):
            return True

        return False

    def _looks_like_knowledge_qa(self, prompt_text: str) -> bool:
        if not prompt_text:
            return False
        if len(prompt_text) <= 80 and any(hint in prompt_text for hint in self._RAG_QA_HINTS):
            return True
        if prompt_text.endswith("?") or prompt_text.endswith("？"):
            return any(hint in prompt_text for hint in self._RAG_QA_HINTS)
        return False

    def should_force_rag_for_prompt(self, prompt: str) -> bool:
        if not self._is_truthy_env(os.getenv("BFF_RUNTIME_FORCE_RAG_FOR_QA", "1")):
            return False
        prompt_text = self._normalize_text(self._extract_current_user_request(prompt))
        if not prompt_text:
            return False
        if self._is_rag_negative_opt_out(prompt_text):
            return False
        if self._is_tooling_meta_query(prompt_text):
            return False
        return self._looks_like_knowledge_qa(prompt_text)

    def should_force_trendradar_for_prompt(self, prompt: str) -> bool:
        """
        Determine whether this prompt is a concrete news/hot-topic request that
        should prefer TrendRadar tools.
        """
        if not self._is_truthy_env(os.getenv("BFF_RUNTIME_FORCE_TRENDRADAR_FOR_NEWS", "1")):
            return False
        prompt_text = self._normalize_text(self._extract_current_user_request(prompt))
        if not prompt_text:
            return False
        if self._is_tooling_meta_query(prompt_text):
            return False
        return self._looks_like_trendradar_news_request(prompt_text)

    def _looks_like_browser_action_request(self, prompt_text: str) -> bool:
        if not prompt_text:
            return False
        return any(hint in prompt_text for hint in self._BROWSER_ACTION_HINTS)

    def _classify_intent(self, prompt_text: str) -> IntentType:
        if not prompt_text:
            return "general"
        if self._is_tooling_meta_query(prompt_text):
            return "tooling_meta"
        # Prefer TrendRadar for news/hot-topic asks even if the user also says
        # "打开" or other browser words.
        if self._looks_like_trendradar_news_request(prompt_text):
            return "web_search"
        if self._looks_like_browser_action_request(prompt_text):
            return "browser_automation"
        if any(hint in prompt_text for hint in self._REPO_HINTS):
            return "repo_ops"
        if self._looks_like_knowledge_qa(prompt_text) and not self._is_rag_negative_opt_out(
            prompt_text
        ):
            return "knowledge_qa"
        if any(hint in prompt_text for hint in self._SEARCH_HINTS):
            return "web_search"
        return "general"

    def _server_explicitly_mentioned(self, prompt_text: str, server: Any) -> bool:
        sid = self._normalize_text(getattr(server, "serverId", ""))
        name = self._normalize_text(getattr(server, "name", ""))
        return bool((sid and sid in prompt_text) or (name and name in prompt_text))

    def _intent_matches_server(
        self, intent: IntentType, prompt_text: str, server: Any
    ) -> bool:
        sid = self._normalize_text(getattr(server, "serverId", ""))
        if intent == "browser_automation":
            return sid == "playwright" or self._server_explicitly_mentioned(
                prompt_text, server
            )
        if intent == "web_search":
            if sid == "trendradar":
                return self._looks_like_trendradar_news_request(
                    prompt_text
                ) or self._server_explicitly_mentioned(prompt_text, server)
            if sid == "exa":
                return True
            return self._server_explicitly_mentioned(prompt_text, server)
        if intent == "repo_ops":
            return sid == "github" or self._server_explicitly_mentioned(
                prompt_text, server
            )
        if intent == "knowledge_qa":
            return sid == "rag" or self._server_explicitly_mentioned(prompt_text, server)
        if intent == "tooling_meta":
            return False
        return True

    def _server_priority(self, prompt_text: str, server_id: str) -> int:
        sid = self._normalize_text(server_id)
        intent = self._classify_intent(prompt_text)
        trendradar_news_request = self._looks_like_trendradar_news_request(prompt_text)

        if sid == "playwright":
            return 0 if intent == "browser_automation" else 40
        if sid == "trendradar":
            if intent == "web_search":
                return 12 if trendradar_news_request else 58
            return 58
        if sid == "rag":
            if intent == "knowledge_qa":
                return 5
            return 45
        if sid == "github":
            if intent == "repo_ops":
                return 10
            return 50
        if sid == "exa":
            if intent == "web_search":
                # Keep Exa as fallback for broad web search, but let TrendRadar
                # win for news/hot-topic/platform-focused asks.
                return 30 if trendradar_news_request else 20
            return 55
        return 60

    def _rank_selected_servers(self, prompt: str, servers: list[Any]) -> list[Any]:
        prompt_text = self._normalize_text(self._extract_current_user_request(prompt))
        return sorted(
            servers,
            key=lambda s: (
                self._server_priority(prompt_text, getattr(s, "serverId", "")),
                getattr(s, "serverId", ""),
            ),
        )

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
        if sid == "rag":
            aliases |= {
                "rag",
                "retrieval",
                "retrieve",
                "vector",
                "embedding",
                "knowledge base",
                "bm25",
                "rerank",
                "index",
                "search docs",
                "文档检索",
                "知识库",
                "向量检索",
                "召回",
                "重排",
                "语义搜索",
            }
        return aliases

    def _should_connect_server(self, prompt: str, server: Any) -> bool:
        prompt_text = self._normalize_text(self._extract_current_user_request(prompt))
        if not prompt_text:
            return False

        if self._is_truthy_env(os.getenv("BFF_RUNTIME_CONNECT_ALL_MCP")):
            return True

        intent = self._classify_intent(prompt_text)
        if not self._intent_matches_server(intent, prompt_text, server):
            return False

        server_id = getattr(server, "serverId", "")
        if server_id == "rag" and self.should_force_rag_for_prompt(prompt):
            logger.info("MCP routing: force-select rag for knowledge QA request")
            return True

        metadata_parts: list[str] = [
            self._normalize_text(server_id),
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
        alias_set = self._server_aliases(server_id)

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

    async def _connect_server(self, agent: Manus, server: Any) -> bool:
        try:
            if server.type == "stdio":
                if not server.command:
                    return False
                await agent.connect_mcp_server(
                    server.command,
                    server_id=server.serverId,
                    use_stdio=True,
                    stdio_args=self._effective_playwright_args(
                        server.serverId, server.args
                    ),
                    stdio_env=server.env or None,
                )
                return True
            if not server.url:
                return False
            if server.type == "streamablehttp":
                await agent.connect_mcp_server(
                    server.url,
                    server_id=server.serverId,
                    use_stdio=False,
                    connection_type="streamablehttp",
                    http_headers=server.env or None,
                )
                return True
            await agent.connect_mcp_server(
                server.url,
                server_id=server.serverId,
                use_stdio=False,
                connection_type="sse",
                http_headers=server.env or None,
            )
            return True
        except Exception as exc:
            logger.warning(
                f"Failed to connect runtime MCP server {server.serverId}: {exc}"
            )
            return False

    async def connect_server_by_id(self, agent: Manus, server_id: str) -> bool:
        if not self._store:
            return False
        server = self._store.mcp_servers.get(server_id)
        if not server or not server.enabled:
            return False
        return await self._connect_server(agent, server)

    def build_mcp_catalog_context(self) -> str:
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

    def augment_prompt_with_mcp_catalog(self, prompt: str) -> str:
        catalog = self.build_mcp_catalog_context()
        if not catalog:
            return prompt
        return f"{prompt}\n\n{catalog}"

    async def connect_enabled_mcp_servers(self, agent: Manus, prompt: str) -> list[str]:
        if not self._store:
            return []
        prompt_text = self._normalize_text(self._extract_current_user_request(prompt))
        intent = self._classify_intent(prompt_text)
        logger.info(f"MCP routing intent: {intent}")

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
            ranked_servers = self._rank_selected_servers(prompt, selected_servers)
            if [s.serverId for s in ranked_servers] != [s.serverId for s in selected_servers]:
                logger.info(
                    "MCP routing ranked matches by intent: "
                    f"{[s.serverId for s in ranked_servers]}"
                )
            selected_servers = ranked_servers

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

        connected_server_ids: list[str] = []
        for server in selected_servers:
            connected = await self._connect_server(agent, server)
            if connected:
                connected_server_ids.append(server.serverId)

        # Treat MCP terminate tools as special finish tools to avoid max-step loops.
        remote_terminate_tools = [
            tool_name
            for tool_name in agent.available_tools.tool_map.keys()
            if tool_name.startswith("mcp_") and tool_name.endswith("_terminate")
        ]
        for tool_name in remote_terminate_tools:
            if tool_name not in agent.special_tool_names:
                agent.special_tool_names.append(tool_name)
        return connected_server_ids
