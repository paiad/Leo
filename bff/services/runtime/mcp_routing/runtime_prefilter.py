from __future__ import annotations

from typing import Any

from bff.services.runtime.mcp_routing.runtime_mcp_router import RuntimeMcpRouter
from bff.services.runtime.mcp_routing.runtime_plan_models import PrefilterResult, PlannerFallback


class RuntimeMcpPrefilter:
    _INTENT_SERVER_ORDER: dict[str, list[str]] = {
        "browser_automation": ["playwright"],
        "repo_ops": ["github"],
        "knowledge_qa": ["rag"],
        "web_search": ["trendradar", "exa"],
        "tooling_meta": [],
        "general": [],
    }

    _MIXED_INTENTS = ["web_search", "browser_automation"]

    # Keyword-based routing for domain-specific MCP servers.
    # This keeps "general" intent defaulting to no-MCP while allowing explicit
    # product/service asks (e.g. McDonald's) to hit the right server.
    _DOMAIN_KEYWORD_ROUTES: dict[str, set[str]] = {
        # Common nicknames/variants for McDonald's in CN + English.
        "mcd-mcp": {"麦当劳", "麦当当", "mcdonald", "mcdonalds", "mcd"},
    }

    def __init__(self, *, router: RuntimeMcpRouter, store: Any | None = None):
        self._router = router
        self._store = store

    def build(self, prompt: str) -> PrefilterResult:
        prompt_text = self._router.normalized_current_user_request(prompt)
        intent = self._router.classify_prompt_intent(prompt)
        plan_intents = (
            list(self._MIXED_INTENTS)
            if self._router.is_mixed_news_and_browser_request(prompt)
            else [intent]
        )
        mixed_news_and_browser = len(plan_intents) > 1
        prefer_trendradar = self._router.should_force_trendradar_for_prompt(prompt)

        enabled_server_ids = self._enabled_server_ids()
        candidates: list[str] = []

        for item_intent in plan_intents:
            ordered_servers = list(self._INTENT_SERVER_ORDER.get(item_intent, []))
            if mixed_news_and_browser and item_intent == "web_search":
                # For mixed intent, keep TrendRadar first and defer Exa as fallback.
                ordered_servers = [sid for sid in ordered_servers if sid == "trendradar"]
            elif item_intent == "web_search" and not prefer_trendradar:
                # Prefer Exa for non-news web search requests (e.g. weather, general lookup).
                # TrendRadar remains available as fallback.
                ordered_servers = sorted(
                    ordered_servers,
                    key=lambda sid: 0 if sid == "exa" else 1,
                )
            for server_id in ordered_servers:
                if server_id in enabled_server_ids and server_id not in candidates:
                    candidates.append(server_id)

        if mixed_news_and_browser and "exa" in enabled_server_ids and "exa" not in candidates:
            candidates.append("exa")

        # Keyword-based routes for domain servers even when intent is "general".
        for server_id, keywords in self._DOMAIN_KEYWORD_ROUTES.items():
            if server_id not in enabled_server_ids or server_id in candidates:
                continue
            if any(keyword in prompt_text for keyword in keywords):
                candidates.insert(0, server_id)

        # Honor explicit server mentions in the user request.
        for server_id in enabled_server_ids:
            if server_id in prompt_text and server_id not in candidates:
                candidates.insert(0, server_id)

        # General/tooling queries default to no-MCP unless explicitly named.
        need_mcp = bool(candidates)

        candidate_tools: dict[str, list[str]] = {
            server_id: self._candidate_tools_for_server(server_id)
            for server_id in candidates
        }

        fallback = self._build_rule_fallback(need_mcp=need_mcp, candidates=candidates, tools=candidate_tools)

        return PrefilterResult(
            intent=intent,
            need_mcp=need_mcp,
            candidate_servers=candidates,
            candidate_tools=candidate_tools,
            rule_fallback=fallback,
        )

    def _enabled_server_ids(self) -> list[str]:
        if not self._store:
            # Keep deterministic defaults for tests/minimal runtime.
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

    def _candidate_tools_for_server(self, server_id: str) -> list[str]:
        if not self._store:
            return []

        server = (getattr(self._store, "mcp_servers", {}) or {}).get(server_id)
        if not server:
            return []

        discovered_tools = getattr(server, "discoveredTools", []) or []
        names: list[str] = []
        for tool in discovered_tools:
            if getattr(tool, "enabled", True) is False:
                continue
            name = str(getattr(tool, "name", "") or "").strip().lower()
            if name:
                names.append(name)
        return sorted(set(names))

    @staticmethod
    def _build_rule_fallback(
        *,
        need_mcp: bool,
        candidates: list[str],
        tools: dict[str, list[str]],
    ) -> PlannerFallback:
        if not need_mcp or not candidates:
            return PlannerFallback(mode="no_mcp", reason="prefilter has no MCP candidates")

        server_id = candidates[0]
        tool_candidates = tools.get(server_id) or []
        tool_name = tool_candidates[0] if tool_candidates else "auto"
        return PlannerFallback(
            mode="rule_route",
            server_id=server_id,
            tool_name=tool_name,
            reason="rule prefilter fallback",
        )
