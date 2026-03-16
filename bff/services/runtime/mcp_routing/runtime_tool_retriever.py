from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.logger import logger
from bff.services.runtime.mcp_routing.runtime_logviz import render_ascii_box
from bff.services.runtime.mcp_routing.runtime_tool_index_sqlite import (
    ToolIndexRow,
    create_mcp_tool_index_from_env,
)


@dataclass(frozen=True)
class RetrievedTool:
    server_id: str
    tool_name: str
    score: float
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class RetrievalOutput:
    intent: str
    candidate_servers: list[str]
    candidate_tools: dict[str, list[str]]
    candidate_tool_profiles: dict[str, dict[str, dict[str, Any]]]
    fallback_server_id: str | None
    fallback_tool_name: str | None
    debug: dict[str, Any]


class RuntimeMcpToolRetriever:
    def __init__(self, *, router: Any, store: Any | None = None):
        self._router = router
        self._store = store
        self._index = create_mcp_tool_index_from_env()

    @staticmethod
    def _is_truthy_env(value: str | None) -> bool:
        return (value or "").strip().lower() in {"1", "true", "yes", "on"}

    # Index configuration is now centralized in create_mcp_tool_index_from_env().

    def retrieve(self, prompt: str) -> RetrievalOutput:
        started = time.perf_counter()
        router_started = time.perf_counter()
        request_text = self._router.current_user_request(prompt)
        normalized = self._router.normalized_current_user_request(prompt)
        intent = self._router.classify_prompt_intent(prompt)
        router_ms = int((time.perf_counter() - router_started) * 1000)
        force_playwright = False
        try:
            force_playwright = bool(self._router.should_force_playwright_for_prompt(prompt))
        except Exception:
            force_playwright = False

        timing_ms: dict[str, int] = {
            "total_ms": 0,
            "router_ms": router_ms,
            "index_refresh_ms": 0,
            "index_search_ms": 0,
            "enabled_servers_ms": 0,
            "description_map_ms": 0,
            "filter_rows_ms": 0,
            "rank_build_ms": 0,
            "profiles_build_ms": 0,
        }

        # Meta questions about "what tools/servers are available" should rely on the
        # runtime catalog context, not on connecting MCP servers.
        if intent == "tooling_meta":
            timing_ms["total_ms"] = int((time.perf_counter() - started) * 1000)
            return RetrievalOutput(
                intent=intent,
                candidate_servers=[],
                candidate_tools={},
                candidate_tool_profiles={},
                fallback_server_id=None,
                fallback_tool_name=None,
                debug={
                    "refreshed_index": False,
                    "request_preview": self._router.request_preview(request_text),
                    "reason": "tooling_meta_intent_no_mcp",
                    "timing_ms": timing_ms,
                },
            )

        refresh_started = time.perf_counter()
        refreshed = self._index.refresh_from_store(self._store)
        timing_ms["index_refresh_ms"] = int((time.perf_counter() - refresh_started) * 1000)

        topk_tools = int(os.getenv("BFF_MCP_TOOL_RETRIEVAL_TOPK_TOOLS", "30") or 30)
        fts_topk = int(os.getenv("BFF_MCP_TOOL_RETRIEVAL_FTS_TOPK", "120") or 120)
        max_servers = int(os.getenv("BFF_MCP_TOOL_RETRIEVAL_TOPK_SERVERS", "4") or 4)

        w_keyword = float(os.getenv("BFF_MCP_TOOL_RETRIEVAL_W_KEYWORD", "0.40") or 0.40)
        w_vector = float(os.getenv("BFF_MCP_TOOL_RETRIEVAL_W_VECTOR", "0.60") or 0.60)

        search_started = time.perf_counter()
        rows = self._index.search(
            query=normalized,
            topk=topk_tools,
            fts_topk=fts_topk,
            w_keyword=w_keyword,
            w_vector=w_vector,
        )
        timing_ms["index_search_ms"] = int((time.perf_counter() - search_started) * 1000)

        enabled_started = time.perf_counter()
        enabled_servers = self._enabled_server_ids()
        timing_ms["enabled_servers_ms"] = int((time.perf_counter() - enabled_started) * 1000)

        filter_started = time.perf_counter()
        tools = self._filter_rows_to_enabled_servers(
            rows,
            enabled_servers,
            timing_ms=timing_ms,
        )
        timing_ms["filter_rows_ms"] = int((time.perf_counter() - filter_started) * 1000)

        rank_started = time.perf_counter()
        server_scores: dict[str, float] = {}
        for tool in tools:
            server_scores[tool.server_id] = max(server_scores.get(tool.server_id, 0.0), tool.score)

        ranked_servers = sorted(server_scores.items(), key=lambda x: (-x[1], x[0]))
        candidate_servers = [sid for sid, _score in ranked_servers[: max(1, max_servers)]]
        timing_ms["rank_build_ms"] = int((time.perf_counter() - rank_started) * 1000)

        candidate_tools: dict[str, list[str]] = {}
        candidate_tool_profiles: dict[str, dict[str, dict[str, Any]]] = {}

        profiles_started = time.perf_counter()
        for sid in candidate_servers:
            sid_tools = [tool for tool in tools if tool.server_id == sid]
            sid_tools.sort(key=lambda t: (-t.score, t.tool_name))
            names: list[str] = []
            profiles: dict[str, dict[str, Any]] = {}
            for tool in sid_tools[:15]:
                if tool.tool_name not in names:
                    names.append(tool.tool_name)
                profiles[tool.tool_name] = {
                    "description": tool.description,
                    "inputSchema": tool.input_schema,
                    "score": tool.score,
                }
            candidate_tools[sid] = names
            candidate_tool_profiles[sid] = profiles
        timing_ms["profiles_build_ms"] = int((time.perf_counter() - profiles_started) * 1000)

        if force_playwright and "playwright" in set(enabled_servers):
            # Browser actions should prefer Playwright even if semantic retrieval
            # matches domain MCP servers more strongly (e.g. TrendRadar mentions "B站").
            forced_sid = "playwright"
            if forced_sid in candidate_servers:
                candidate_servers = [forced_sid] + [sid for sid in candidate_servers if sid != forced_sid]
            else:
                candidate_servers = [forced_sid] + list(candidate_servers)

            # Populate a minimal tool list for planner hints even when retrieval rows
            # don't include Playwright tools.
            if not (candidate_tools.get(forced_sid) or []):
                names = self._index.list_enabled_tool_names(server_id=forced_sid, limit=30)
                name_set = set(names)
                preferred_order = [
                    "browser_navigate",
                    "browser_type",
                    "browser_click",
                    "browser_fill_form",
                    "browser_snapshot",
                    "browser_take_screenshot",
                    "browser_press_key",
                    "browser_tabs",
                ]
                ordered = [name for name in preferred_order if name in name_set]
                ordered.extend([name for name in names if name not in set(ordered)])
                candidate_tools[forced_sid] = ordered[:15]
                candidate_tool_profiles.setdefault(forced_sid, {})

            candidate_servers = list(candidate_servers)[: max(1, max_servers)]

        fallback_server_id = candidate_servers[0] if candidate_servers else None
        fallback_tool_name = None
        if fallback_server_id:
            tools_for_server = candidate_tools.get(fallback_server_id) or []
            fallback_tool_name = tools_for_server[0] if tools_for_server else "auto"

        debug = {
            "refreshed_index": refreshed,
            "request_preview": self._router.request_preview(request_text),
            "retrieved_tools": [
                {
                    "server": tool.server_id,
                    "tool": tool.tool_name,
                    "score": round(tool.score, 4),
                }
                for tool in tools[:10]
            ],
            "timing_ms": timing_ms,
        }

        timing_ms["total_ms"] = int((time.perf_counter() - started) * 1000)
        if self._is_truthy_env(os.getenv("BFF_RUNTIME_TIMING_LOG_ENABLED", "1")):
            lines = [
                f"intent: {intent}",
                f"total_ms: {timing_ms.get('total_ms', 0)}",
                f"router_ms: {timing_ms.get('router_ms', 0)}",
                f"index.refresh_ms: {timing_ms.get('index_refresh_ms', 0)}",
                f"index.search_ms: {timing_ms.get('index_search_ms', 0)}",
                f"enabled_servers_ms: {timing_ms.get('enabled_servers_ms', 0)}",
                f"description_map_ms: {timing_ms.get('description_map_ms', 0)}",
                f"filter_rows_ms: {timing_ms.get('filter_rows_ms', 0)}",
                f"rank_build_ms: {timing_ms.get('rank_build_ms', 0)}",
                f"profiles_build_ms: {timing_ms.get('profiles_build_ms', 0)}",
                f"rows: {len(rows)}",
                f"enabled_servers: {len(enabled_servers)}",
                f"tools_kept: {len(tools)}",
            ]
            logger.info("\n" + render_ascii_box("MCP TIMING (RETRIEVAL)", lines))

        logger.info(
            "MCP tool retrieval: "
            f"intent={intent}, "
            f"candidate_servers={candidate_servers}, "
            f"fallback={fallback_server_id}/{fallback_tool_name}, "
            f"request='{self._router.request_preview(prompt)}'"
        )

        return RetrievalOutput(
            intent=intent,
            candidate_servers=candidate_servers,
            candidate_tools=candidate_tools,
            candidate_tool_profiles=candidate_tool_profiles,
            fallback_server_id=fallback_server_id,
            fallback_tool_name=fallback_tool_name,
            debug=debug,
        )

    def _enabled_server_ids(self) -> list[str]:
        if not self._store:
            return ["playwright", "github", "rag", "trendradar", "exa"]
        servers = getattr(self._store, "mcp_servers", {}) or {}
        enabled: list[str] = []
        for server_id, server in servers.items():
            if not getattr(server, "enabled", False):
                continue
            sid = str(server_id or "").strip().lower()
            if sid:
                enabled.append(sid)
        return sorted(set(enabled))

    def _filter_rows_to_enabled_servers(
        self,
        rows: list[ToolIndexRow],
        enabled_servers: list[str],
        *,
        timing_ms: dict[str, int] | None = None,
    ) -> list[RetrievedTool]:
        enabled = set(enabled_servers)
        desc_started = time.perf_counter()
        description_map = self._build_tool_description_map()
        desc_ms = int((time.perf_counter() - desc_started) * 1000)
        if timing_ms is not None:
            timing_ms["description_map_ms"] = desc_ms
        result: list[RetrievedTool] = []
        for row in rows:
            if row.server_id not in enabled:
                continue
            desc, schema = description_map.get((row.server_id, row.tool_name), ("", {}))
            result.append(
                RetrievedTool(
                    server_id=row.server_id,
                    tool_name=row.tool_name,
                    score=row.score,
                    description=desc,
                    input_schema=schema,
                )
            )
        return result

    def _build_tool_description_map(self) -> dict[tuple[str, str], tuple[str, dict[str, Any]]]:
        if not self._store:
            return {}
        servers = getattr(self._store, "mcp_servers", {}) or {}
        mapping: dict[tuple[str, str], tuple[str, dict[str, Any]]] = {}
        for server_id, server in servers.items():
            if not getattr(server, "enabled", False):
                continue
            sid = str(server_id or "").strip().lower()
            if not sid:
                continue
            for tool in (getattr(server, "discoveredTools", []) or [])[:8000]:
                if getattr(tool, "enabled", True) is False:
                    continue
                name = str(getattr(tool, "name", "") or "").strip().lower()
                if not name:
                    continue
                mapping[(sid, name)] = (
                    str(getattr(tool, "description", "") or "").strip(),
                    getattr(tool, "inputSchema", {}) or {},
                )
        return mapping
