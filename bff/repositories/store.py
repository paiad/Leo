from __future__ import annotations

from dataclasses import dataclass, field

from bff.domain.models import SessionRecord, McpServerRecord


@dataclass
class InMemoryStore:
    sessions: dict[str, SessionRecord] = field(default_factory=dict)
    mcp_servers: dict[str, McpServerRecord] = field(default_factory=dict)


store = InMemoryStore()
