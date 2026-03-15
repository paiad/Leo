export type ChatRole = "system" | "user" | "assistant";

export type ChatMessage = {
  id: string;
  role: ChatRole;
  content: string;
  createdAt: string;
  model?: string;
  toolEvents?: ChatToolEvent[];
  decisionEvents?: ChatDecisionEvent[];
  timelineEvents?: ChatTimelineEvent[];
};

export type ChatTimelineEvent = {
  id: string;
  phase: "progress" | "tool_start" | "tool_done" | "reply_start" | "reply_done";
  text: string;
  createdAt: string;
  status?: "running" | "success" | "failed";
};

export type ChatToolTransportType = "stdio" | "http" | "sse" | "streamablehttp";

export type ChatMcpCatalogItem = {
  toolId: string;
  name: string;
  type: ChatToolTransportType;
  command?: string | null;
  args: string[];
  url?: string | null;
  description: string;
  enabled: boolean;
};

export type ChatToolCatalogItem = ChatMcpCatalogItem;

export type ChatMcpDiscoveredTool = {
  name: string;
  description: string;
  inputSchema: Record<string, unknown>;
  enabled: boolean;
};

export type ChatMcpServer = {
  serverId: string;
  name: string;
  type: ChatToolTransportType;
  command?: string | null;
  args: string[];
  env: Record<string, string>;
  url?: string | null;
  description: string;
  enabled: boolean;
  discoveredTools: ChatMcpDiscoveredTool[];
};

export type ChatToolEvent = {
  toolId?: string;
  toolName: string;
  status: "started" | "success" | "failed" | "ignored";
  latencyMs?: number;
  inputPreview?: string;
  outputJson?: unknown;
  outputPreview?: string;
  errorMessage?: string;
};

export type ChatDecisionEvent = {
  action: "invoke" | "skip" | "clarify";
  reason: string;
  confidence?: number;
  prompt?: string;
  source?: string;
};

export type ChatRuntimeConfig = {
  workspacePrompt: string;
};
