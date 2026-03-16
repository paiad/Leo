from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from app.config import config
from app.llm import LLM
from app.logger import logger
from app.tool.mcp import MCPClients
from bff.domain.models import (
    McpDiscoveredTool,
    McpServerCreate,
    McpServerRecord,
    McpServerUpdate,
    new_id,
)
from bff.repositories.store import InMemoryStore, PostgresStore

LEGACY_LOCAL_MCP_SERVER_ID = "openmanus-local"
DEFAULT_LOCAL_MCP_SERVER_ID = "leo-local"
DEFAULT_MEMORY_MCP_SERVER_ID = "memory"


def _default_npx_command() -> str:
    return "npx.cmd" if os.name == "nt" else "npx"


def _is_truthy_env(value: str | None) -> bool:
    return (value or "").strip().lower() in {"1", "true", "yes", "on"}


def _expand_path(value: str | None) -> str | None:
    if not value:
        return None
    return os.path.abspath(os.path.expandvars(os.path.expanduser(value.strip())))


def _effective_playwright_args(server_id: str, args: list[str] | None) -> list[str]:
    """
    Allow env-level overrides for Playwright MCP session persistence without
    requiring users to hand-edit mcp.bff.json every time.
    """
    if server_id != "playwright":
        return list(args or [])

    user_data_dir = _expand_path(os.getenv("BFF_PLAYWRIGHT_USER_DATA_DIR"))
    storage_state = _expand_path(os.getenv("BFF_PLAYWRIGHT_STORAGE_STATE"))

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


class ToolingService:
    def __init__(self, store: InMemoryStore | PostgresStore):
        self._store = store
        self._state_file = Path(config.root_path) / "config" / "mcp.bff.json"
        self._use_postgres_state = isinstance(store, PostgresStore)
        self._bootstrap_mcp_state()
        self._profile_llm = None

    def _bootstrap_mcp_state(self) -> None:
        # JSON file is used only in non-DB mode. When Postgres is enabled, the DB is the
        # single source of truth and we never read from `config/mcp.bff.json` to avoid
        # state drift and confusing "file overrides DB" behavior.
        if not self._use_postgres_state:
            self._load_state_file()

        # Fallback to current config when state file doesn't exist.
        if not self._store.mcp_servers:
            for server_id, server_cfg in config.mcp_config.servers.items():
                normalized_id = self._normalize_local_server_id(server_id)
                self._store.mcp_servers[normalized_id] = McpServerRecord(
                    serverId=normalized_id,
                    name=normalized_id,
                    type=(
                        server_cfg.type
                        if server_cfg.type in {"stdio", "sse", "http", "streamablehttp"}
                        else "stdio"
                    ),
                    command=server_cfg.command,
                    args=server_cfg.args,
                    env=self._normalize_env(getattr(server_cfg, "env", {})),
                    url=server_cfg.url,
                    description="",
                    enabled=True,
                    discoveredTools=[],
                )

        self._migrate_local_server_alias()

        # Provide a ready-to-use local Leo MCP server template (includes browser/editor/bash tools).
        if DEFAULT_LOCAL_MCP_SERVER_ID not in self._store.mcp_servers:
            self._store.mcp_servers[DEFAULT_LOCAL_MCP_SERVER_ID] = McpServerRecord(
                serverId=DEFAULT_LOCAL_MCP_SERVER_ID,
                name="Leo Local MCP",
                type="stdio",
                command=sys.executable,
                args=["-m", "app.mcp.server", "--transport", "stdio"],
                env={},
                description="Leo built-in MCP server (bash/browser/editor/terminate)",
                enabled=False,
                discoveredTools=[],
            )

        # Provide a ready-to-use Memory MCP template for long-term memory tools.
        if DEFAULT_MEMORY_MCP_SERVER_ID not in self._store.mcp_servers:
            self._store.mcp_servers[DEFAULT_MEMORY_MCP_SERVER_ID] = McpServerRecord(
                serverId=DEFAULT_MEMORY_MCP_SERVER_ID,
                name="Memory MCP",
                type="stdio",
                command=_default_npx_command(),
                args=["-y", "@modelcontextprotocol/server-memory"],
                env={},
                description="MCP memory server (knowledge graph based long-term memory)",
                enabled=False,
                discoveredTools=[],
            )

        self._persist_state()

    @staticmethod
    def _normalize_local_server_id(server_id: str) -> str:
        if server_id == LEGACY_LOCAL_MCP_SERVER_ID:
            return DEFAULT_LOCAL_MCP_SERVER_ID
        return server_id

    def _migrate_local_server_alias(self) -> None:
        legacy = self._store.mcp_servers.get(LEGACY_LOCAL_MCP_SERVER_ID)
        current = self._store.mcp_servers.get(DEFAULT_LOCAL_MCP_SERVER_ID)

        if legacy and not current:
            legacy.serverId = DEFAULT_LOCAL_MCP_SERVER_ID
            if legacy.name == "OpenManus Local MCP":
                legacy.name = "Leo Local MCP"
            if legacy.description:
                legacy.description = legacy.description.replace("OpenManus", "Leo")
            self._store.mcp_servers[DEFAULT_LOCAL_MCP_SERVER_ID] = legacy

        # Remove legacy alias to avoid duplicate entries in frontend.
        self._store.mcp_servers.pop(LEGACY_LOCAL_MCP_SERVER_ID, None)

    @staticmethod
    def _normalize_env(value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        normalized: dict[str, str] = {}
        for key, env_value in value.items():
            if not isinstance(key, str):
                continue
            if isinstance(env_value, str):
                normalized[key] = env_value
            elif env_value is None:
                continue
            else:
                normalized[key] = str(env_value)
        return normalized

    @staticmethod
    def _normalize_capability_profile(value: Any) -> dict[str, Any]:
        if value is None:
            return {}
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            candidate = value.strip()
            if candidate.startswith("{") and candidate.endswith("}"):
                try:
                    parsed = json.loads(candidate)
                    if isinstance(parsed, dict):
                        return parsed
                except Exception:
                    return {}
        return {}

    @classmethod
    def _normalize_connection_meta(
        cls,
        env_value: Any,
        headers_value: Any,
    ) -> dict[str, str]:
        # Keep a single persisted dict field for backward compatibility:
        # - stdio uses it as env
        # - http/sse/streamablehttp use it as headers
        merged: dict[str, str] = {}
        merged.update(cls._normalize_env(headers_value))
        merged.update(cls._normalize_env(env_value))
        return merged

    def _load_state_file(self) -> None:
        if not self._state_file.exists():
            return

        try:
            raw = json.loads(self._state_file.read_text(encoding="utf-8"))
            servers = raw.get("mcpServers", {})
            for server_id, item in servers.items():
                normalized_id = self._normalize_local_server_id(server_id)
                discovered = []
                for tool in item.get("discoveredTools", []):
                    try:
                        discovered.append(McpDiscoveredTool.model_validate(tool))
                    except Exception:
                        continue

                server_type = item.get("type", "stdio")
                if server_type not in {"stdio", "sse", "http", "streamablehttp"}:
                    server_type = "stdio"

                name = item.get("name") or normalized_id
                if normalized_id == DEFAULT_LOCAL_MCP_SERVER_ID and name == "OpenManus Local MCP":
                    name = "Leo Local MCP"
                description = item.get("description", "")
                if normalized_id == DEFAULT_LOCAL_MCP_SERVER_ID and description:
                    description = description.replace("OpenManus", "Leo")

                self._store.mcp_servers[normalized_id] = McpServerRecord(
                    serverId=normalized_id,
                    name=name,
                    type=server_type,
                    command=item.get("command"),
                    args=item.get("args", []),
                    env=self._normalize_connection_meta(
                        item.get("env", {}),
                        item.get("headers", {}),
                    ),
                    url=item.get("url"),
                    description=description,
                    enabled=bool(item.get("enabled", True)),
                    discoveredTools=discovered,
                )
        except Exception:
            # Ignore corrupted state and continue with fallback bootstrap.
            return

    def _persist_state_file(self) -> None:
        payload = {
            "mcpServers": {
                server_id: server.model_dump()
                for server_id, server in sorted(self._store.mcp_servers.items())
            }
        }
        self._state_file.parent.mkdir(parents=True, exist_ok=True)
        self._state_file.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _persist_state(self) -> None:
        if self._use_postgres_state:
            self._store.persist_mcp_servers()
            return
        self._persist_state_file()

    def list_tools(self) -> list[dict[str, Any]]:
        builtin_browser_enabled = _is_truthy_env(
            os.getenv("BFF_RUNTIME_ENABLE_BUILTIN_BROWSER_USE", "0")
        )
        tools: list[dict[str, Any]] = [
            {
                "toolId": "builtin_python_execute",
                "name": "Python Execute",
                "type": "stdio",
                "command": None,
                "args": [],
                "url": None,
                "description": "Leo built-in Python execution tool",
                "enabled": True,
            },
            {
                "toolId": "builtin_browser_use",
                "name": "Browser Use",
                "type": "stdio",
                "command": None,
                "args": [],
                "url": None,
                "description": "Leo built-in browser automation tool (disabled by default, prefer MCP browser tools)",
                "enabled": builtin_browser_enabled,
            },
            {
                "toolId": "builtin_editor",
                "name": "Str Replace Editor",
                "type": "stdio",
                "command": None,
                "args": [],
                "url": None,
                "description": "Leo built-in file editing tool",
                "enabled": True,
            },
        ]

        # Include MCP servers so frontend can map toolId -> server name.
        for server in sorted(self._store.mcp_servers.values(), key=lambda x: x.serverId):
            tools.append(
                {
                    "toolId": server.serverId,
                    "name": server.name,
                    "type": server.type,
                    "command": server.command,
                    "args": server.args,
                    "url": server.url,
                    "description": server.description,
                    "enabled": server.enabled,
                }
            )

        return tools

    def list_mcp_servers(self) -> list[dict]:
        return [
            item.model_dump()
            for item in sorted(self._store.mcp_servers.values(), key=lambda x: x.serverId)
        ]

    def create_mcp_server(self, payload: McpServerCreate) -> dict:
        server_id = payload.serverId or new_id()
        if server_id in self._store.mcp_servers:
            raise ValueError("serverId 已存在")

        record = McpServerRecord(
            serverId=server_id,
            name=payload.name or server_id,
            type=payload.type,
            command=payload.command,
            args=payload.args,
            env=self._normalize_env(payload.env),
            url=payload.url,
            description=payload.description or "",
            category=(payload.category or "domain"),
            capabilityProfile=self._normalize_capability_profile(payload.capabilityProfile),
            enabled=payload.enabled if payload.enabled is not None else True,
            discoveredTools=[],
        )
        self._store.mcp_servers[server_id] = record
        self._persist_state()
        return record.model_dump()

    def update_mcp_server(self, server_id: str, payload: McpServerUpdate) -> dict | None:
        record = self._store.mcp_servers.get(server_id)
        if not record:
            return None

        update = payload.model_dump(exclude_unset=True)
        if "env" in update:
            update["env"] = self._normalize_env(update.get("env"))
        if "capabilityProfile" in update:
            update["capabilityProfile"] = self._normalize_capability_profile(update.get("capabilityProfile"))
        for key, value in update.items():
            setattr(record, key, value)

        # Config changes invalidate previously discovered tool cache.
        if any(k in update for k in {"type", "command", "args", "env", "url"}):
            record.discoveredTools = []

        self._persist_state()
        return record.model_dump()

    @staticmethod
    def _is_truthy_env(value: str | None) -> bool:
        return (value or "").strip().lower() in {"1", "true", "yes", "on"}

    def _get_profile_llm(self) -> LLM:
        if self._profile_llm is not None:
            return self._profile_llm
        config_name = str(os.getenv("BFF_MCP_PROFILE_LLM_CONFIG", "default") or "default").strip()
        self._profile_llm = LLM(config_name=config_name)
        return self._profile_llm

    @staticmethod
    def _extract_json_object(raw_text: str) -> dict[str, Any] | None:
        if not raw_text:
            return None
        text = raw_text.strip()
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            pass

        first = text.find("{")
        last = text.rfind("}")
        if first >= 0 and last > first:
            try:
                parsed = json.loads(text[first : last + 1].strip())
                return parsed if isinstance(parsed, dict) else None
            except Exception:
                return None
        return None

    async def generate_mcp_server_capability_profile(
        self,
        server_id: str,
        *,
        force: bool = False,
    ) -> dict | None:
        record = self._store.mcp_servers.get(server_id)
        if not record:
            return None

        existing = getattr(record, "capabilityProfile", None)
        if isinstance(existing, dict) and existing and not force:
            return record.model_dump()

        if not self._is_truthy_env(os.getenv("BFF_MCP_PROFILE_GENERATION_ENABLED", "1")):
            raise ValueError("capabilityProfile generation disabled (BFF_MCP_PROFILE_GENERATION_ENABLED=0)")

        tools_summary: list[dict[str, Any]] = []
        for tool in (record.discoveredTools or [])[:60]:
            input_schema = getattr(tool, "inputSchema", {}) or {}
            input_keys: list[str] = []
            if isinstance(input_schema, dict):
                props = input_schema.get("properties")
                if isinstance(props, dict):
                    input_keys = [str(k) for k in list(props.keys())[:30]]
            tools_summary.append(
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_keys": input_keys,
                }
            )

        payload = {
            "version": "mcp-capability-profile.v1",
            "server": {
                "server_id": record.serverId,
                "name": record.name,
                "description": record.description or "",
                "category": getattr(record, "category", "domain") or "domain",
                "discovered_tools": tools_summary,
            },
            "output_contract": {
                "version": "string, must be mcp-capability-profile.v1",
                "language": "string, one of zh|en|mixed",
                "domains": "array of strings, short domain tags",
                "keywords": {"zh": "array of strings", "en": "array of strings"},
                "example_queries": "array of natural user queries",
                "negative_queries": "array of queries that should NOT route here",
                "notes": "string, short guidance for routing",
            },
            "rules": [
                "Return JSON object only. No markdown, no prose, no code fences.",
                "Prefer user words in keywords and example_queries.",
                "Keep example_queries realistic (<= 16 items).",
                "Include common nicknames/synonyms in keywords when applicable.",
                "For Chinese brands, include common nicknames/abbreviations (e.g. 麦当当 for 麦当劳).",
                "Do not include secrets, tokens, urls, or commands.",
            ],
        }

        system_prompt = "You generate strict JSON capability profiles for MCP servers. Output JSON only."
        llm = self._get_profile_llm()
        try:
            response = await llm.ask(
                messages=[{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
                system_msgs=[{"role": "system", "content": system_prompt}],
                stream=False,
                temperature=0,
            )
        except Exception as exc:
            logger.warning(f"capabilityProfile LLM call failed for server {server_id}: {exc}")
            raise ValueError(f"capabilityProfile generation failed: {exc}") from exc

        parsed = self._extract_json_object(response)
        if parsed is None:
            raise ValueError("capabilityProfile generation returned invalid JSON object")

        parsed.setdefault("version", "mcp-capability-profile.v1")
        record.capabilityProfile = parsed
        self._persist_state()
        return record.model_dump()

    async def backfill_mcp_server_capability_profiles(
        self,
        *,
        force: bool = False,
        limit: int = 50,
    ) -> list[dict]:
        updated: list[dict] = []
        for server_id in sorted(self._store.mcp_servers.keys()):
            record = self._store.mcp_servers.get(server_id)
            if not record or not getattr(record, "enabled", True):
                continue
            existing = getattr(record, "capabilityProfile", None)
            if isinstance(existing, dict) and existing and not force:
                continue
            result = await self.generate_mcp_server_capability_profile(server_id, force=force)
            if result is not None:
                updated.append(result)
            if len(updated) >= max(1, int(limit)):
                break
        return updated

    def delete_mcp_server(self, server_id: str) -> list[dict]:
        self._store.mcp_servers.pop(server_id, None)
        self._persist_state()
        return self.list_mcp_servers()

    async def discover_mcp_server_tools(self, server_id: str) -> list[dict] | None:
        record = self._store.mcp_servers.get(server_id)
        if not record:
            return None

        clients = MCPClients()
        try:
            if record.type == "stdio":
                if not record.command:
                    raise ValueError("stdio MCP 需要 command")
                await clients.connect_stdio(
                    record.command,
                    _effective_playwright_args(record.serverId, record.args),
                    record.serverId,
                    env=record.env or None,
                )
            elif record.type == "streamablehttp":
                if not record.url:
                    raise ValueError("streamablehttp MCP 需要 url")
                await clients.connect_streamable_http(
                    record.url,
                    record.serverId,
                    headers=record.env or None,
                )
            else:
                if not record.url:
                    raise ValueError("sse/http MCP 需要 url")
                await clients.connect_sse(
                    record.url,
                    record.serverId,
                    headers=record.env or None,
                )

            response = await clients.list_tools()
            discovered_tools = [
                McpDiscoveredTool(
                    name=tool.name,
                    description=tool.description or "",
                    inputSchema=getattr(tool, "inputSchema", {}) or {},
                    enabled=True,
                )
                for tool in response.tools
            ]
            record.discoveredTools = discovered_tools
            self._persist_state()
            return [tool.model_dump() for tool in record.discoveredTools]
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"discover 失败: {exc}") from exc
        finally:
            try:
                await clients.disconnect()
            except Exception:
                pass

    async def auto_discover_enabled_servers(self, *, only_if_empty: bool = True, limit: int = 20) -> dict[str, object]:
        """
        Auto-discover tools for enabled servers.

        This is intended for startup indexing: Retrieval-First relies on discoveredTools
        being populated at least once for each MCP server.
        """
        servers = list((self._store.mcp_servers or {}).values())
        enabled = [s for s in servers if getattr(s, "enabled", False)]
        targets = []
        for server in sorted(enabled, key=lambda s: str(getattr(s, "serverId", ""))):
            sid = str(getattr(server, "serverId", "") or "").strip().lower()
            if not sid:
                continue
            tools = getattr(server, "discoveredTools", []) or []
            if only_if_empty and len(tools) > 0:
                continue
            targets.append(sid)
        targets = targets[: max(0, int(limit or 0))]

        discovered: list[str] = []
        failed: list[dict[str, str]] = []
        for sid in targets:
            try:
                result = await self.discover_mcp_server_tools(sid)
                if result is not None:
                    discovered.append(sid)
            except Exception as exc:
                failed.append({"server_id": sid, "error": str(exc)})

        return {
            "targets": targets,
            "discovered": discovered,
            "failed": failed,
        }

    def list_mcp_server_tools(self, server_id: str) -> list[dict] | None:
        record = self._store.mcp_servers.get(server_id)
        if not record:
            return None
        return [tool.model_dump() for tool in record.discoveredTools]

    def get_enabled_mcp_servers(self) -> list[McpServerRecord]:
        return [server for server in self._store.mcp_servers.values() if server.enabled]
