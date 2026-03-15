from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid4())


class ChatRequest(BaseModel):
    content: str
    model: str | None = None
    baseUrl: str | None = None
    apiKey: str | None = None
    workspacePrompt: str | None = None
    sessionId: str | None = None
    toolMode: Literal["auto", "ban"] = "auto"
    source: Literal["browser", "lark"] = "browser"
    userInputType: Literal["text", "audio_asr"] = "text"


class CreateSessionRequest(BaseModel):
    title: str | None = None
    source: Literal["browser", "lark"] = "browser"


class MessageRecord(BaseModel):
    id: str
    role: Literal["system", "user", "assistant"]
    content: str
    createdAt: str
    model: str | None = None
    toolEvents: list[dict[str, Any]] = Field(default_factory=list)
    decisionEvents: list[dict[str, Any]] = Field(default_factory=list)
    userInputType: Literal["text", "audio_asr"] = "text"


class SessionRecord(BaseModel):
    id: str
    title: str
    createdAt: str
    updatedAt: str
    source: Literal["browser", "lark"] = "browser"
    messages: list[MessageRecord] = Field(default_factory=list)


class McpDiscoveredTool(BaseModel):
    name: str
    description: str
    inputSchema: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True


class McpServerRecord(BaseModel):
    serverId: str
    name: str
    type: Literal["stdio", "http", "sse", "streamablehttp"]
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    description: str = ""
    enabled: bool = True
    discoveredTools: list[McpDiscoveredTool] = Field(default_factory=list)


class McpServerCreate(BaseModel):
    serverId: str | None = None
    name: str | None = None
    type: Literal["stdio", "http", "sse", "streamablehttp"]
    command: str | None = None
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    description: str | None = ""
    enabled: bool | None = True


class McpServerUpdate(BaseModel):
    name: str | None = None
    type: Literal["stdio", "http", "sse", "streamablehttp"] | None = None
    command: str | None = None
    args: list[str] | None = None
    env: dict[str, str] | None = None
    url: str | None = None
    description: str | None = None
    enabled: bool | None = None


class WorkspaceModelCreate(BaseModel):
    name: str
    provider: str
    baseUrl: str = ""
    apiKey: str = ""
    enabled: bool = True


class WorkspaceModelUpdate(BaseModel):
    name: str
    provider: str
    baseUrl: str = ""
    apiKey: str = ""
    enabled: bool = True
